"""Sheet 12 (List of Notes) sub-agent fan-out.

The Notes-Listofnotes sheet has 138 target rows. Instead of one agent
reading every note in the PDF and choosing among 138 labels, we split the
scout-discovered inventory into N contiguous batches and run N parallel
sub-agents. Each sub-agent only sees its own batch; the sub-coordinator
owns the final payload aggregation and workbook write.

Contract:
  input  = NoteInventoryEntry list (from scout) + notes deps
  output = ListOfNotesSubResult with:
           - aggregated_payloads (ready for notes.writer)
           - unmatched_payloads (those targeting row 112 — the
             "Disclosure of other notes to accounts" row)
           - per-sub-agent status (succeeded / failed, with retry count)
           - side-log paths (notes12_unmatched.json, notes12_failures.json)

Retry budget: each sub-agent is retried at most once (PLAN section 2 #10).
A failed sub-agent after its retry contributes no payloads — the other
sub-agents' work still lands.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
    ThinkingPartDelta,
)

from agent_tracing import MAX_AGENT_ITERATIONS
from notes._rate_limit import (
    RATE_LIMIT_MAX_RETRIES,
    compute_backoff_delay,
    is_rate_limit_error,
)
from notes.agent import create_notes_agent
from notes.constants import NOTES_PHASE_MAP as _PHASE_MAP
from notes.coverage import CoverageReceipt
from notes.payload import NotesPayload
from utils.sanitize import sanitize as _sanitize_for_log
from notes_types import NotesTemplateType
from pricing import estimate_cost
from scout.notes_discoverer import NoteInventoryEntry

logger = logging.getLogger(__name__)

# The "Disclosure of other notes to accounts" row — where unmatched notes
# land per PLAN section 2 edge-cases. Kept here as the single source of
# truth so the prompt and the side-log filter agree on spelling.
ROW_112_LABEL = "Disclosure of other notes to accounts"

# Per-note iteration overhead (view_pdf_pages + write_notes + an optional
# continuation view) plus a fixed bootstrap (read_template + save_result).
# A 6-note batch needs ~4 iterations/note + ~4 bootstrap ≈ 28, well under
# the global cap. The formula just guarantees the cap never clips a
# well-behaved run regardless of batch size.
_PER_NOTE_ITERATIONS = 4
_BOOTSTRAP_ITERATIONS = 10


def _iteration_budget(batch_size: int) -> int:
    return max(MAX_AGENT_ITERATIONS, _BOOTSTRAP_ITERATIONS + _PER_NOTE_ITERATIONS * batch_size)


# Seconds of stagger between parallel Sheet-12 sub-agent launches. Same
# rationale as ``notes.coordinator.NOTES_LAUNCH_STAGGER_SECS`` — 5
# concurrent sub-agents are the most common 429 offender because they
# all render the same PDF page range and fire nearly-identical sized
# bursts. Shorter than the template-level stagger because the sub-agents
# are already waiting for their parent fanout to start.
_SUB_AGENT_LAUNCH_STAGGER_SECS = 0.6


# ---------------------------------------------------------------------------
# Retry-contract marker
# ---------------------------------------------------------------------------

class _SubAgentNoWriteError(RuntimeError):
    """Raised when a sub-agent finishes cleanly but produces zero payloads
    for a non-empty batch.

    Mirrors ``notes.coordinator._NoWriteError``: the single-sheet notes
    path has guarded against "agent ran and returned without calling
    write_notes" since PLAN §4 E.1; without this marker the Sheet-12
    sub-agent path silently accepted empty-payload returns as success
    and whole note batches disappeared without retry (peer-review #2).
    Participates in the normal retry loop via the broad ``except Exception``
    block below — no special handling required upstream.
    """


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SubAgentRunResult:
    """One sub-agent's outcome. ``retry_count`` is the number of retries
    performed (0 = first-try success; 1 = one retry, success or failure).

    ``prompt_tokens`` / ``completion_tokens`` are captured from
    ``agent_run.usage()`` at the end of the last attempt. The fan-out
    runner aggregates them into the parent cost report (Phase 5.1);
    they stay at 0 when the sub-agent never reached model execution
    (empty batch short-circuit, early cancellation) so the aggregate
    still sums cleanly."""
    sub_agent_id: str
    batch: list[NoteInventoryEntry]
    payloads: list[NotesPayload]
    status: str  # "succeeded" | "failed"
    error: Optional[str] = None
    retry_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Slice 5: the sub-agent's coverage receipt, if it submitted one.
    # None when the agent never called `submit_batch_coverage` (e.g.
    # hit the iteration cap before the terminal call, or failed
    # outright). Downstream treats `coverage is None AND status ==
    # 'succeeded'` as "agent completed work but didn't close the
    # handshake" — different from an outright failure.
    coverage: Optional[CoverageReceipt] = None


@dataclass
class ListOfNotesSubResult:
    sub_agent_results: list[SubAgentRunResult] = field(default_factory=list)
    # Flat union of payloads from every successful sub-agent. Ready to
    # hand to notes.writer.write_notes_workbook — the writer already
    # concatenates duplicate rows (including row 112).
    aggregated_payloads: list[NotesPayload] = field(default_factory=list)
    # Subset of aggregated_payloads whose chosen_row_label is row 112.
    # Surfaced separately so the UI / side-log can show "this many notes
    # were funnelled into the catch-all row".
    unmatched_payloads: list[NotesPayload] = field(default_factory=list)
    unmatched_path: Optional[str] = None
    failures_path: Optional[str] = None
    # Slice 5: path to notes12_coverage.json containing per-sub-agent
    # receipts. None when no sub-agents ran (empty inventory) — writing
    # an empty file would misleadingly imply coverage was attempted.
    coverage_path: Optional[str] = None

    @property
    def all_succeeded(self) -> bool:
        # Vacuous truth: no sub-agents ran (empty inventory) → all succeeded.
        if not self.sub_agent_results:
            return True
        return all(r.status == "succeeded" for r in self.sub_agent_results)


# ---------------------------------------------------------------------------
# Batch splitter
# ---------------------------------------------------------------------------

def split_inventory_contiguous(
    inventory: list[NoteInventoryEntry],
    n_batches: int = 5,
) -> list[list[NoteInventoryEntry]]:
    """Split the inventory into up to ``n_batches`` contiguous chunks.

    Page-contiguous split (not round-robin) so adjacent notes — which
    often share context, e.g. Note 4 "revenue" and Note 5 "cost of
    sales" — stay with the same sub-agent. When the inventory has fewer
    entries than ``n_batches`` we return fewer, non-empty batches.
    """
    if not inventory:
        return []
    n = min(n_batches, len(inventory))
    base, extra = divmod(len(inventory), n)
    # First `extra` batches take one more entry so 7 items in 5 batches
    # splits as [2, 2, 1, 1, 1] (not [3, 1, 1, 1, 1]).
    batches: list[list[NoteInventoryEntry]] = []
    start = 0
    for i in range(n):
        size = base + (1 if i < extra else 0)
        batches.append(inventory[start:start + size])
        start += size
    return batches


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_listofnotes_subcoordinator(
    pdf_path: str,
    inventory: list[NoteInventoryEntry],
    filing_level: str,
    model: Any,
    output_dir: str,
    event_queue: Optional[asyncio.Queue] = None,
    session_id: Optional[str] = None,
    agent_id: str = "notes:LIST_OF_NOTES",
    parallel: int = 5,
    max_retries: int = 1,
    page_hints: Optional[list[int]] = None,
    page_offset: int = 0,
) -> ListOfNotesSubResult:
    """Fan out the LIST_OF_NOTES work across `parallel` sub-agents."""
    import task_registry

    batches = split_inventory_contiguous(inventory, n_batches=parallel)

    # Track the (task, sub_id, batch) triple so a task that raises
    # unexpectedly (bypassing _run_list_of_notes_sub_agent's own exception
    # handling) can still be surfaced as a failed SubAgentRunResult instead
    # of silently vanishing from the failure log.
    task_metadata: list[tuple[asyncio.Task, str, list[NoteInventoryEntry]]] = []

    async def _run_sub_with_cleanup(
        sub_id: str,
        batch: list[NoteInventoryEntry],
        launch_delay: float,
    ) -> SubAgentRunResult:
        """Run a sub-agent and guarantee task_registry unregistration on exit.

        PR A.5: task_registry.register was never paired with an unregister
        call, so sub-agent task refs lingered past completion. The outer
        abort path's remove_session() eventually caught them, but standalone
        cancellations and the window between abort and cleanup leaked.
        This finally fires on success, failure, AND CancelledError.
        """
        try:
            return await _run_list_of_notes_sub_agent(
                sub_agent_id=sub_id,
                batch=batch,
                pdf_path=pdf_path,
                filing_level=filing_level,
                model=model,
                output_dir=output_dir,
                event_queue=event_queue,
                parent_agent_id=agent_id,
                max_retries=max_retries,
                page_hints=page_hints,
                page_offset=page_offset,
                launch_delay=launch_delay,
            )
        finally:
            if session_id:
                task_registry.unregister(session_id, sub_id)

    for i, batch in enumerate(batches):
        sub_id = f"{agent_id}:sub{i}"
        # Stagger sub-agent starts so all 5 don't hit the provider's
        # per-minute-token bucket in the same instant. First sub-agent
        # starts immediately (delay=0); later ones sleep at the top of
        # their runner. See _SUB_AGENT_LAUNCH_STAGGER_SECS for rationale.
        task = asyncio.create_task(
            _run_sub_with_cleanup(sub_id, batch, i * _SUB_AGENT_LAUNCH_STAGGER_SECS),
            name=sub_id,
        )
        task_metadata.append((task, sub_id, batch))
        if session_id:
            task_registry.register(session_id, sub_id, task)

    sub_results: list[SubAgentRunResult] = []
    if task_metadata:
        tasks = [t for t, _, _ in task_metadata]
        try:
            await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)
        except asyncio.CancelledError:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.wait(tasks, timeout=5.0)
            raise

        for t, sub_id, batch in task_metadata:
            try:
                sub_results.append(t.result())
            except asyncio.CancelledError:
                # Should be rare — we've already awaited ALL_COMPLETED.
                # Treat as a failed batch so the failure log still reflects
                # lost coverage.
                sub_results.append(SubAgentRunResult(
                    sub_agent_id=sub_id,
                    batch=batch,
                    payloads=[],
                    status="failed",
                    error="Cancelled",
                    retry_count=0,
                ))
            except Exception as e:
                # Defensive: the retry wrapper itself swallows Exception, so
                # this is only reachable if the wrapper has a bug. Preserve
                # the batch in failures_path instead of logging-and-dropping.
                logger.exception("Sub-agent task raised unexpectedly: %s", e)
                sub_results.append(SubAgentRunResult(
                    sub_agent_id=sub_id,
                    batch=batch,
                    payloads=[],
                    status="failed",
                    error=f"Unexpected task error: {e}",
                    retry_count=max_retries,
                ))

    # Aggregate payloads. The writer already concatenates duplicate-row
    # payloads, so we only need the flat union here — row 112 rollup
    # happens naturally in `notes.writer._combine_payloads`.
    aggregated: list[NotesPayload] = []
    for r in sub_results:
        if r.status == "succeeded":
            aggregated.extend(r.payloads)

    unmatched = [p for p in aggregated if _is_row_112(p.chosen_row_label)]

    unmatched_path = _write_unmatched_side_log(unmatched, output_dir) if unmatched else None
    failures_path = _write_failures_side_log(sub_results, output_dir)
    # Slice 5: coverage side-log. Written whenever any sub-agent ran —
    # ``sub_results`` is empty only when the inventory was empty, in
    # which case an empty file would be misleading.
    coverage_path = (
        _write_coverage_side_log(sub_results, output_dir)
        if sub_results else None
    )

    return ListOfNotesSubResult(
        sub_agent_results=sub_results,
        aggregated_payloads=aggregated,
        unmatched_payloads=unmatched,
        unmatched_path=unmatched_path,
        failures_path=failures_path,
        coverage_path=coverage_path,
    )


# ---------------------------------------------------------------------------
# Per-sub-agent runner
# ---------------------------------------------------------------------------

async def _run_list_of_notes_sub_agent(
    sub_agent_id: str,
    batch: list[NoteInventoryEntry],
    pdf_path: str,
    filing_level: str,
    model: Any,
    output_dir: str,
    event_queue: Optional[asyncio.Queue] = None,
    parent_agent_id: str = "notes:LIST_OF_NOTES",
    max_retries: int = 1,
    page_hints: Optional[list[int]] = None,
    page_offset: int = 0,
    launch_delay: float = 0.0,
) -> SubAgentRunResult:
    """Run one sub-agent over its batch.

    Retries the full invocation once on any generic exception; after
    ``max_retries`` consecutive failures, marks the sub-agent as failed
    and returns an empty payload list. Rate-limit (HTTP 429) failures
    use a separate, larger budget (``RATE_LIMIT_MAX_RETRIES``) with
    honoured retry-after hints and jittered backoff — a TPM throttle
    isn't a real failure and shouldn't burn the generic-error budget.

    ``launch_delay`` staggers the start relative to sibling sub-agents
    so 5 parallel tasks don't all burst into the provider's TPM bucket
    at the same millisecond.
    """
    # Stagger start relative to sibling sub-agents. CancelledError from
    # the sleep propagates naturally — the parent fanout awaits all sub
    # tasks and handles cancelled results in its normal result loop.
    if launch_delay > 0:
        await asyncio.sleep(launch_delay)

    last_error: Optional[str] = None
    # ``retry_count`` on the returned result is the number of retries
    # performed (0 = first-try success). Tracked independently of the
    # generic/rate-limit split below so the side-log stays compatible
    # with the existing contract.
    retries_performed = 0

    # Track the last attempt's usage so both success and failure paths
    # return populated token counts. Final assignment wins — per Phase 5
    # we don't sum across retries because the operator cares about the
    # last attempt's cost, not the total trial-and-error spend.
    last_prompt_tokens = 0
    last_completion_tokens = 0

    # Two retry budgets — see notes.coordinator._run_single_notes_agent
    # for the same two-budget treatment.
    generic_retries = 0
    rl_retries = 0
    attempt_num = 0
    # Backoff scheduled on the previous iteration, consumed at the top
    # of the next — keeps the sleep inside the try/except so a user
    # abort during backoff raises CancelledError through the runner
    # instead of bubbling out of the retry loop raw (the parent fan-out
    # interprets raised CancelledError as a cancelled sub-agent).
    pending_backoff: float = 0.0

    while True:
        # Peer-review MEDIUM: mutable accumulator threaded into
        # _invoke_sub_agent_once so the usage from a failing attempt
        # isn't lost. Without this the failure branch below would fall
        # back to 0/0 and the aggregate cost report under-reports every
        # time a sub-agent spends tokens then raises.
        usage_accumulator: dict[str, int] = {"prompt": 0, "completion": 0}
        try:
            if pending_backoff > 0:
                await asyncio.sleep(pending_backoff)
                pending_backoff = 0.0
            payloads, prompt_t, completion_t, coverage = await _invoke_sub_agent_once(
                sub_agent_id=sub_agent_id,
                batch=batch,
                pdf_path=pdf_path,
                filing_level=filing_level,
                model=model,
                output_dir=output_dir,
                event_queue=event_queue,
                parent_agent_id=parent_agent_id,
                attempt=attempt_num,
                page_hints=page_hints,
                page_offset=page_offset,
                usage_out=usage_accumulator,
            )
            last_prompt_tokens = prompt_t
            last_completion_tokens = completion_t
            # Zero-payload guard (peer-review #2): a non-empty batch
            # that produces zero payloads is the Sheet-12 analogue of
            # the single-sheet path's `_NoWriteError` — the model
            # sometimes returns without ever calling `write_notes`,
            # which should retry not succeed.
            #
            # Slice 5 carve-out: a submitted coverage receipt that
            # legitimately skips every batch note (e.g. all notes in
            # the slice belong on another sheet) is a real success —
            # the agent looked and deliberately chose not to write.
            # Treat "zero payloads AND a receipt covering every note"
            # as success; the existing retry still catches "zero
            # payloads AND no receipt" (silent abandon).
            if batch and not payloads:
                receipt_covers_batch = (
                    coverage is not None
                    and {e.note_num for e in coverage.entries}
                    >= {entry.note_num for entry in batch}
                )
                if not receipt_covers_batch:
                    raise _SubAgentNoWriteError(
                        f"Sub-agent finished without emitting any payloads "
                        f"for a batch of {len(batch)} note(s)"
                    )
            return SubAgentRunResult(
                sub_agent_id=sub_agent_id,
                batch=batch,
                payloads=payloads,
                status="succeeded",
                retry_count=retries_performed,
                prompt_tokens=last_prompt_tokens,
                completion_tokens=last_completion_tokens,
                coverage=coverage,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Carry the accumulator's last-known counts into the failure
            # record so retry-exhaustion still reports real spend.
            last_prompt_tokens = usage_accumulator["prompt"]
            last_completion_tokens = usage_accumulator["completion"]
            last_error = str(e)
            if is_rate_limit_error(e):
                if rl_retries >= RATE_LIMIT_MAX_RETRIES:
                    logger.warning(
                        "Sub-agent %s rate-limit retries exhausted (%d) — giving up",
                        sub_agent_id, RATE_LIMIT_MAX_RETRIES,
                    )
                    break
                pending_backoff = compute_backoff_delay(e, rl_retries)
                rl_retries += 1
                retries_performed += 1
                attempt_num += 1
                logger.warning(
                    "Sub-agent %s hit 429 (rl-retry %d/%d) — sleeping %.2fs: %s",
                    sub_agent_id, rl_retries, RATE_LIMIT_MAX_RETRIES,
                    pending_backoff, e,
                )
                continue
            if generic_retries >= max_retries:
                break
            generic_retries += 1
            retries_performed += 1
            attempt_num += 1
            logger.warning(
                "Sub-agent %s failed (generic-retry %d/%d): %s — retrying",
                sub_agent_id, generic_retries, max_retries, e,
            )

    logger.warning("Sub-agent %s exhausted retries: %s", sub_agent_id, last_error)
    return SubAgentRunResult(
        sub_agent_id=sub_agent_id,
        batch=batch,
        payloads=[],
        status="failed",
        error=last_error,
        retry_count=retries_performed,
        prompt_tokens=last_prompt_tokens,
        completion_tokens=last_completion_tokens,
    )


async def _invoke_sub_agent_once(
    sub_agent_id: str,
    batch: list[NoteInventoryEntry],
    pdf_path: str,
    filing_level: str,
    model: Any,
    output_dir: str,
    event_queue: Optional[asyncio.Queue] = None,
    parent_agent_id: str = "notes:LIST_OF_NOTES",
    attempt: int = 0,
    page_hints: Optional[list[int]] = None,
    page_offset: int = 0,
    usage_out: Optional[dict[str, int]] = None,
) -> tuple[list[NotesPayload], int, int, Optional[CoverageReceipt]]:
    """Single attempt at a sub-agent run.

    Returns ``(payloads, prompt_tokens, completion_tokens, coverage)``
    — the usage counts come from the final `agent_run.usage()` read,
    consistent with how `token_update` events are already emitted
    during the run. Callers (Phase 5.1) aggregate these into the
    parent cost report so `NOTES_LIST_OF_NOTES_cost_report.txt`
    reflects real spend instead of the zeros the per-turn tracker
    records (CLAUDE.md gotcha #6).

    ``coverage`` (Slice 5) is the CoverageReceipt the sub-agent
    submitted through `submit_batch_coverage`, or None if the agent
    exited without calling the tool. Callers use None as a signal
    that the sub-agent didn't close the handshake — the batch's
    notes are considered uncovered and surfaced in the side-log.

    ``usage_out`` (peer-review MEDIUM) is a mutable accumulator that
    the caller reads from on the failure branch. We update its
    ``"prompt"`` and ``"completion"`` keys on every iteration so a
    mid-run raise doesn't drop token counts already spent.
    """
    payload_sink: list[NotesPayload] = []

    # Peer-review [HIGH]: batch_note_nums MUST be passed at factory time,
    # not set post-construction. `create_notes_agent` registers the
    # `submit_batch_coverage` tool CONDITIONALLY on deps.batch_note_nums
    # being populated; a post-hoc assignment arrives too late for that
    # check and the tool silently fails to register on the live agent.
    # The post-construction line below is kept as belt-and-braces (the
    # field is equal in both places so either value wins identically)
    # and is covered by
    # tests/test_notes_batch_note_nums_wiring.py::test_invoke_sub_agent_once_registers_submit_batch_coverage_tool.
    batch_note_nums = [entry.note_num for entry in batch]

    agent, deps = create_notes_agent(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        pdf_path=pdf_path,
        inventory=batch,  # sub-agent only sees its slice
        filing_level=filing_level,
        model=model,
        page_hints=page_hints,
        page_offset=page_offset,
        output_dir=output_dir,
        batch_note_nums=batch_note_nums,
    )
    deps.payload_sink = payload_sink
    deps.sub_agent_id = sub_agent_id
    # Redundant with the factory kwarg above — the factory already
    # populates this field. Kept so callers that mutate `deps` directly
    # (tests, future internal plumbing) see the value even if they
    # didn't go through the factory path.
    deps.batch_note_nums = batch_note_nums

    # Derive the batch's page span for the scope-nudge paragraph below.
    # Empty batch (vacuous) collapses both values to 0 and we skip the
    # line entirely — no batch, nothing to scope.
    batch_pages = [p for entry in batch for p in range(entry.page_range[0], entry.page_range[1] + 1)]
    batch_min = min(batch_pages) if batch_pages else 0
    batch_max = max(batch_pages) if batch_pages else 0

    scope_line = (
        f"Your batch covers PDF pages {batch_min}–{batch_max}. Prefer these "
        f"pages when viewing the PDF. If a note in your batch legitimately "
        f"cross-references a page outside {batch_min}–{batch_max} (e.g. a "
        f"Financial Instruments note pointing to Risk Management detail), "
        f"you may view it — but mention the cross-reference page(s) in "
        f"`evidence` so the reader knows the citation left your batch range. "
        f"When practical, request all pages you expect to need in a single "
        f"`view_pdf_pages` call instead of one page per turn.\n\n"
    ) if batch_pages else ""

    # Render the assigned notes by number + title so the agent sees
    # exactly what it must cover, not just a count. Enumerated list
    # feeds into the coverage-receipt contract below — the receipt
    # validator checks the agent accounted for each number here.
    # Empty-batch case renders no list (vacuously covered; the
    # sub-coordinator skips the run anyway).
    if batch:
        note_lines = "\n".join(
            f"  - note_num={entry.note_num} ({entry.title}) on pages "
            f"{entry.page_range[0]}–{entry.page_range[1]}"
            for entry in batch
        )
        batch_list = (
            f"Your batch is {len(batch)} note(s):\n{note_lines}\n\n"
        )
    else:
        batch_list = ""

    prompt = (
        f"You are sub-agent {sub_agent_id}. "
        f"{batch_list}"
        f"{scope_line}"
        f"For each note, view the PDF pages, pick the best-matching "
        f"template row label(s), and emit payloads through write_notes.\n\n"
        f"After all write_notes calls are done, call `submit_batch_coverage` "
        f"with a JSON list accounting for EVERY note in your batch — one "
        f"entry per note, each either \"written\" with the template row "
        f"labels you wrote, or \"skipped\" with a one-sentence reason. "
        f"This is your last tool call; the run is not complete without it. "
        f"See the COVERAGE RECEIPT section of your system prompt for the "
        f"exact shape.\n\n"
        f"Follow your system prompt for the full contract."
    )

    async def _emit(event_type: str, data: dict) -> None:
        if event_queue is None:
            return
        await event_queue.put({
            "event": event_type,
            "data": {
                **data,
                # Parent agent_id so the UI aggregates into the single
                # Notes-12 tab. sub_agent_id is preserved in the payload
                # for timeline tracing.
                "agent_id": parent_agent_id,
                "agent_role": NotesTemplateType.LIST_OF_NOTES.value,
                "sub_agent_id": sub_agent_id,
            },
        })

    # Phase 5.2: include the batch's note-number range and PDF page span
    # in the "started" status so the Notes-12 tab can show something like
    # "sub0: Notes 1-3 (pp 18-30)" instead of just "sub0: 3 notes". Much
    # easier for a reviewer to spot the fan-out split at a glance, and
    # for post-hoc history replay to show which sub-agent covered which
    # part of the PDF without diving into the trace JSON.
    if batch:
        note_range = f"Notes {batch[0].note_num}-{batch[-1].note_num}"
        if batch_pages:
            started_msg = (
                f"{sub_agent_id} starting ({note_range}, "
                f"pp {batch_min}-{batch_max}, {len(batch)} notes)..."
            )
        else:
            started_msg = f"{sub_agent_id} starting ({note_range}, {len(batch)} notes)..."
    else:
        started_msg = f"{sub_agent_id} starting (0 notes)..."
    await _emit("status", {
        "phase": "started",
        "message": started_msg,
        # Structured fields so the UI can render a badge without parsing
        # the human-readable message string.
        "batch_note_range": (
            [batch[0].note_num, batch[-1].note_num] if batch else []
        ),
        "batch_page_range": (
            [batch_min, batch_max] if batch_pages else []
        ),
    })

    iteration = 0
    iteration_cap = _iteration_budget(len(batch))
    tool_start: dict[str, float] = {}
    thinking_counter = 0

    async with agent.iter(prompt, deps=deps) as agent_run:
        async for node in agent_run:
            iteration += 1
            if iteration > iteration_cap:
                raise RuntimeError(
                    f"Sub-agent {sub_agent_id} hit iteration limit "
                    f"({iteration_cap})"
                )
            if Agent.is_call_tools_node(node):
                async with node.stream(agent_run.ctx) as tool_stream:
                    async for event in tool_stream:
                        if isinstance(event, FunctionToolCallEvent):
                            phase = _PHASE_MAP.get(event.part.tool_name)
                            if phase:
                                await _emit("status", {
                                    "phase": phase,
                                    "message": f"{sub_agent_id}: {phase.replace('_', ' ')}",
                                })
                            raw_args = event.part.args
                            if isinstance(raw_args, str):
                                try:
                                    parsed = json.loads(raw_args)
                                except (json.JSONDecodeError, TypeError):
                                    parsed = {}
                            elif isinstance(raw_args, dict):
                                parsed = raw_args
                            else:
                                parsed = {}
                            # Namespace tool_call_id with sub_agent_id so parallel
                            # sub-agents that share one parent agent_id can't
                            # collide in the frontend timeline Map (the live
                            # reducer treats matching ids as duplicates and
                            # would silently drop the second call).
                            namespaced_id = f"{sub_agent_id}:{event.part.tool_call_id}"
                            await _emit("tool_call", {
                                "tool_name": event.part.tool_name,
                                "tool_call_id": namespaced_id,
                                "args": parsed,
                            })
                            tool_start[namespaced_id] = time.monotonic()
                        elif isinstance(event, FunctionToolResultEvent):
                            content = event.result.content
                            summary = str(content)[:800] if content else ""
                            namespaced_id = f"{sub_agent_id}:{event.result.tool_call_id}"
                            start_t = tool_start.pop(namespaced_id, None)
                            duration_ms = int((time.monotonic() - start_t) * 1000) if start_t else 0
                            await _emit("tool_result", {
                                "tool_name": event.result.tool_name,
                                "tool_call_id": namespaced_id,
                                "result_summary": summary,
                                "duration_ms": duration_ms,
                            })
            elif Agent.is_model_request_node(node):
                tid = f"{sub_agent_id}_think_{thinking_counter}"
                active = False
                async with node.stream(agent_run.ctx) as model_stream:
                    async for event in model_stream:
                        if isinstance(event, PartDeltaEvent):
                            delta = event.delta
                            if isinstance(delta, TextPartDelta):
                                if active:
                                    await _emit("thinking_end", {
                                        "thinking_id": tid, "summary": "", "full_length": 0,
                                    })
                                    active = False
                                    thinking_counter += 1
                                    tid = f"{sub_agent_id}_think_{thinking_counter}"
                                await _emit("text_delta", {"content": delta.content_delta})
                            elif isinstance(delta, ThinkingPartDelta):
                                active = True
                                await _emit("thinking_delta", {
                                    "content": delta.content_delta or "",
                                    "thinking_id": tid,
                                })
                if active:
                    await _emit("thinking_end", {
                        "thinking_id": tid, "summary": "", "full_length": 0,
                    })
                    thinking_counter += 1

            usage = agent_run.usage()
            total = usage.total_tokens or 0
            prompt_t = usage.request_tokens or 0
            completion_t = usage.response_tokens or 0
            # Keep the caller's failure-path accumulator in sync so the
            # retry wrapper can report accurate spend if the next
            # iteration raises.
            if usage_out is not None:
                usage_out["prompt"] = int(prompt_t)
                usage_out["completion"] = int(completion_t)
            await _emit("token_update", {
                "prompt_tokens": prompt_t,
                "completion_tokens": completion_t,
                "thinking_tokens": 0,
                "cumulative": total,
                "cost_estimate": estimate_cost(prompt_t, completion_t, 0, model),
            })

    # Capture the final aggregate usage so the retry wrapper + fanout can
    # fold it into the parent cost report (Phase 5.1). If the loop never
    # entered a model_request_node (empty batch short-circuit, cancel
    # before first turn) we'd still have prompt_t=completion_t=0 from
    # the locals above — ``try/except NameError`` would be overkill.
    final_usage = agent_run.usage()
    final_prompt = int(final_usage.request_tokens or 0)
    final_completion = int(final_usage.response_tokens or 0)
    if usage_out is not None:
        usage_out["prompt"] = final_prompt
        usage_out["completion"] = final_completion
    # Slice 5: hand the coverage receipt back to the retry wrapper.
    # ``deps.coverage_receipt`` is set by the submit_batch_coverage tool
    # when it accepts a valid receipt; None means the agent never made
    # the terminal call. We pass the attribute through regardless — the
    # aggregator decides how to treat None (uncovered) vs a populated
    # receipt (covered, possibly with skips).
    return list(payload_sink), final_prompt, final_completion, deps.coverage_receipt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_row_112(label: str) -> bool:
    # Normalise the same way notes.writer does so a stray leading '*' or
    # case difference can't bypass the catch-all detection.
    if not label:
        return False
    return label.strip().lstrip("*").strip().lower() == ROW_112_LABEL.lower()


def _write_unmatched_side_log(
    unmatched: list[NotesPayload],
    output_dir: str,
) -> str:
    """Persist the unmatched-note side log. Only called when non-empty."""
    path = Path(output_dir) / "notes12_unmatched.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "chosen_row_label": p.chosen_row_label,
            "content": p.content,
            "evidence": p.evidence,
            "source_pages": p.source_pages,
            "sub_agent_id": p.sub_agent_id,
        }
        for p in unmatched
    ]
    # Peer-review C2: sanitise LLM-authored strings before persisting
    # so a `cat notes12_unmatched.json` in a terminal can't be hijacked
    # by ANSI escapes / Unicode-override tricks the model emitted.
    payload = _sanitize_for_log({"count": len(entries), "entries": entries})
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)


def _write_failures_side_log(
    sub_results: list[SubAgentRunResult],
    output_dir: str,
) -> Optional[str]:
    """Persist the failed-sub-agent side log. Returns None if no failures."""
    failed = [r for r in sub_results if r.status == "failed"]
    if not failed:
        return None
    path = Path(output_dir) / "notes12_failures.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "sub_agent_id": r.sub_agent_id,
            "error": r.error,
            "retry_count": r.retry_count,
            "batch_note_nums": [e.note_num for e in r.batch],
        }
        for r in failed
    ]
    # Peer-review C2: error strings from upstream HTTP failures often
    # quote LLM/provider output verbatim — sanitise before persistence.
    payload = _sanitize_for_log({"count": len(entries), "entries": entries})
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)


def _write_coverage_side_log(
    sub_results: list[SubAgentRunResult],
    output_dir: str,
) -> str:
    """Persist the per-sub-agent coverage receipts.

    Written whenever any sub-agent ran so operators can see coverage
    even on clean runs — the absence of this file otherwise would
    force them to infer "no skips" from "no coverage file", which is
    exactly the silent-success failure mode the receipts exist to
    prevent.

    Shape — one entry per sub-agent:
      - `sub_agent_id`, `status`, `retry_count`, `batch_note_nums`
      - `receipt`: the receipt as emitted (None when the agent never
        called `submit_batch_coverage`)
      - `uncovered_note_nums`: derived — batch notes that the receipt
        didn't account for. For `receipt is None` this is the full
        batch. For a submitted receipt this should always be empty
        (the tool rejects receipts with missing notes); we compute it
        defensively anyway so a future bug in the tool doesn't silently
        hide uncovered notes.
    """
    path = Path(output_dir) / "notes12_coverage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for r in sub_results:
        batch_nums = [e.note_num for e in r.batch]
        receipt_dict: Optional[dict[str, Any]] = None
        covered: set[int] = set()
        if r.coverage is not None:
            receipt_dict = r.coverage.to_dict()
            covered = {e.note_num for e in r.coverage.entries}
        uncovered = [n for n in batch_nums if n not in covered]
        entries.append({
            "sub_agent_id": r.sub_agent_id,
            "status": r.status,
            "retry_count": r.retry_count,
            "batch_note_nums": batch_nums,
            "receipt": receipt_dict,
            "uncovered_note_nums": uncovered,
        })
    # Peer-review C2: receipts include LLM-authored skip reasons —
    # sanitise before persisting so terminal `cat` is safe.
    payload = _sanitize_for_log(
        {"count": len(entries), "entries": entries},
    )
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)
