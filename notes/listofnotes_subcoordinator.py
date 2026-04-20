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
from notes.agent import create_notes_agent
from notes.payload import NotesPayload
from notes_types import NotesTemplateType
from pricing import estimate_cost
from scout.notes_discoverer import NoteInventoryEntry

logger = logging.getLogger(__name__)

# The "Disclosure of other notes to accounts" row — where unmatched notes
# land per PLAN section 2 edge-cases. Kept here as the single source of
# truth so the prompt and the side-log filter agree on spelling.
ROW_112_LABEL = "Disclosure of other notes to accounts"

# Shared source of truth for tool-name → phase mapping. Peer-review #2:
# was duplicated between notes.coordinator.NOTES_PHASE_MAP and a local
# _PHASE_MAP here; any new notes-only tool would need both updated.
from notes.coordinator import NOTES_PHASE_MAP as _PHASE_MAP  # noqa: E402

# Per-note iteration overhead (view_pdf_pages + write_notes + an optional
# continuation view) plus a fixed bootstrap (read_template + save_result).
# A 6-note batch needs ~4 iterations/note + ~4 bootstrap ≈ 28, well under
# the global cap. The formula just guarantees the cap never clips a
# well-behaved run regardless of batch size.
_PER_NOTE_ITERATIONS = 4
_BOOTSTRAP_ITERATIONS = 10


def _iteration_budget(batch_size: int) -> int:
    return max(MAX_AGENT_ITERATIONS, _BOOTSTRAP_ITERATIONS + _PER_NOTE_ITERATIONS * batch_size)


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
    performed (0 = first-try success; 1 = one retry, success or failure)."""
    sub_agent_id: str
    batch: list[NoteInventoryEntry]
    payloads: list[NotesPayload]
    status: str  # "succeeded" | "failed"
    error: Optional[str] = None
    retry_count: int = 0


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
) -> ListOfNotesSubResult:
    """Fan out the LIST_OF_NOTES work across `parallel` sub-agents."""
    import task_registry

    batches = split_inventory_contiguous(inventory, n_batches=parallel)

    # Track the (task, sub_id, batch) triple so a task that raises
    # unexpectedly (bypassing _run_list_of_notes_sub_agent's own exception
    # handling) can still be surfaced as a failed SubAgentRunResult instead
    # of silently vanishing from the failure log.
    task_metadata: list[tuple[asyncio.Task, str, list[NoteInventoryEntry]]] = []
    for i, batch in enumerate(batches):
        sub_id = f"{agent_id}:sub{i}"
        task = asyncio.create_task(
            _run_list_of_notes_sub_agent(
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
            ),
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

    return ListOfNotesSubResult(
        sub_agent_results=sub_results,
        aggregated_payloads=aggregated,
        unmatched_payloads=unmatched,
        unmatched_path=unmatched_path,
        failures_path=failures_path,
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
) -> SubAgentRunResult:
    """Run one sub-agent over its batch. Retries the full invocation once
    on any exception; after ``max_retries`` consecutive failures, marks
    the sub-agent as failed and returns an empty payload list."""
    last_error: Optional[str] = None
    attempt = 0  # the for loop will clobber this; initialised for the
                 # ``max_retries=-1``-style edge case where the loop body
                 # never executes (shouldn't happen but keeps the post-loop
                 # `retry_count=attempt` well-defined).

    for attempt in range(max_retries + 1):
        try:
            payloads = await _invoke_sub_agent_once(
                sub_agent_id=sub_agent_id,
                batch=batch,
                pdf_path=pdf_path,
                filing_level=filing_level,
                model=model,
                output_dir=output_dir,
                event_queue=event_queue,
                parent_agent_id=parent_agent_id,
                attempt=attempt,
                page_hints=page_hints,
            )
            # Zero-payload guard (peer-review #2): a non-empty batch that
            # produces zero payloads is the Sheet-12 analogue of the
            # single-sheet path's `_NoWriteError` — the model sometimes
            # returns without ever calling `write_notes`, which should
            # retry not succeed. An empty batch is a real empty (no notes
            # in this slice) and passes through as succeeded.
            if batch and not payloads:
                raise _SubAgentNoWriteError(
                    f"Sub-agent finished without emitting any payloads "
                    f"for a batch of {len(batch)} note(s)"
                )
            return SubAgentRunResult(
                sub_agent_id=sub_agent_id,
                batch=batch,
                payloads=payloads,
                status="succeeded",
                retry_count=attempt,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_error = str(e)
            if attempt >= max_retries:
                break
            logger.warning(
                "Sub-agent %s failed (attempt %d/%d): %s — retrying",
                sub_agent_id, attempt + 1, max_retries + 1, e,
            )

    logger.warning("Sub-agent %s exhausted retries: %s", sub_agent_id, last_error)
    return SubAgentRunResult(
        sub_agent_id=sub_agent_id,
        batch=batch,
        payloads=[],
        status="failed",
        error=last_error,
        retry_count=attempt,
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
) -> list[NotesPayload]:
    """Single attempt at a sub-agent run. Returns the captured payloads."""
    payload_sink: list[NotesPayload] = []

    agent, deps = create_notes_agent(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        pdf_path=pdf_path,
        inventory=batch,  # sub-agent only sees its slice
        filing_level=filing_level,
        model=model,
        page_hints=page_hints,
        output_dir=output_dir,
    )
    deps.payload_sink = payload_sink
    deps.sub_agent_id = sub_agent_id

    prompt = (
        f"You are sub-agent {sub_agent_id}. Your batch contains "
        f"{len(batch)} PDF note(s). For each note in the batch, view the "
        f"PDF pages, pick the best-matching template row label(s), and "
        f"emit payloads through write_notes. Follow your system prompt."
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

    await _emit("status", {
        "phase": "started",
        "message": f"{sub_agent_id} starting ({len(batch)} notes)...",
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
            await _emit("token_update", {
                "prompt_tokens": prompt_t,
                "completion_tokens": completion_t,
                "thinking_tokens": 0,
                "cumulative": total,
                "cost_estimate": estimate_cost(prompt_t, completion_t, 0, model),
            })

    return list(payload_sink)


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
    path.write_text(
        json.dumps({"count": len(entries), "entries": entries}, indent=2, ensure_ascii=False),
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
    path.write_text(
        json.dumps({"count": len(entries), "entries": entries}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)
