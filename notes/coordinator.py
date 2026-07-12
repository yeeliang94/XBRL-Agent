"""Notes coordinator — fans out one agent per requested notes template.

Mirrors `coordinator.py` for face statements, but uses notes agents from
`notes.agent` and emits events under `agent_id = "notes:<TEMPLATE>"`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from agent_tracing import MAX_AGENT_ITERATIONS, save_agent_trace  # noqa: F401
from agent_runner import (
    AgentLoopSpec,
    RetryPolicy,
    make_emitter,
    run_agent_loop,
    run_agent_with_retries,
    # Re-exported under the legacy name so
    # `from notes.coordinator import _iter_with_turn_timeout`
    # (tests/test_notes_turn_timeout) keeps working — the implementation
    # now lives in agent_runner (rewrite Phase 2).
    iter_with_turn_timeout as _iter_with_turn_timeout,  # noqa: F401
)
from notes._rate_limit import (
    RATE_LIMIT_MAX_RETRIES,
    is_rate_limit_error,
)
from notes.agent import create_notes_agent
from notes.constants import NOTES_PHASE_MAP
from notes.listofnotes_subcoordinator import run_listofnotes_subcoordinator
from notes.writer import BORDERLINE_FUZZY_SCORE
from notes_types import NotesTemplateType
from pricing import estimate_cost
from scout.infopack import Infopack
from scout.notes_discoverer import NoteInventoryEntry

logger = logging.getLogger(__name__)

# Re-exported for backwards compatibility — any caller that imports
# NOTES_PHASE_MAP from notes.coordinator continues to work. Prefer
# ``from notes.constants import NOTES_PHASE_MAP`` in new code.
__all__ = ("NOTES_PHASE_MAP",)


def _backfill_token_report(token_report, usage_callable, template_label: str) -> None:
    """Fold the agent's end-of-run usage into the TokenReport totals.

    ``usage_callable`` may be the usage VALUE itself (V2 idiom —
    ``agent_run.usage`` is a property) or a zero-arg callable returning
    it (legacy/tests). Accepting both closed the 2026-07-12 V2-review
    finding where the property value was invoked as a function and the
    resulting TypeError was swallowed into 0-token cost reports. Per CLAUDE.md gotcha #6
    per-turn counts stay at zero (PydanticAI does counting internally)
    — only the end-of-run aggregate is accurate. The try/except is the
    last line of defence against a surprise usage shape; any failure
    is logged rather than crashing the run because cost telemetry is
    strictly advisory.
    """
    try:
        usage = usage_callable() if callable(usage_callable) else usage_callable
        # New-name-first: input_/output_tokens are the pydantic-ai 1.x
        # primary API; request_/response_ are deprecated aliases that V2
        # removes. Old-name-first here silently reported 0 under V2.
        _prompt = getattr(usage, "input_tokens", None)
        if _prompt is None:
            _prompt = getattr(usage, "request_tokens", 0)
        _completion = getattr(usage, "output_tokens", None)
        if _completion is None:
            _completion = getattr(usage, "response_tokens", 0)
        token_report.total_prompt_tokens += int(_prompt or 0)
        token_report.total_completion_tokens += int(_completion or 0)
        # Thinking tokens aren't separately tracked by the OpenAI-compat
        # proxy — leaving at 0 keeps the report internally consistent.
    except Exception:  # noqa: BLE001 — cost telemetry is best-effort
        logger.debug("notes cost backfill skipped for %s", template_label)


@dataclass
class NotesRunConfig:
    pdf_path: str
    output_dir: str
    # Default model used for any template whose `models` entry is unset. The
    # CLI and pre-per-note callers keep using this single field with no
    # behaviour change.
    model: Any
    notes_to_run: Set[NotesTemplateType] = field(default_factory=set)
    filing_level: str = "company"
    # Filing standard axis, orthogonal to filing_level. Threaded into each
    # `notes_template_path()` resolution so MPERS runs route through
    # XBRL-template-MPERS/{Company,Group}/ with the shifted 11..15 numbering.
    # Defaults to "mfrs" so existing CLI / test callers keep working.
    filing_standard: str = "mfrs"
    # Optional per-template model overrides. When present, the coordinator
    # passes ``models[template_type]`` into the agent factory instead of
    # ``model``; templates missing from this dict fall back to ``model``.
    models: Dict[NotesTemplateType, Any] = field(default_factory=dict)
    # Pages the face-statement scout marked as note-bearing (union across
    # all 5 statements). Flows into each notes agent's system prompt as a
    # "start here" hint so scanned PDFs — where scout's deterministic
    # inventory is empty — don't trigger a page 1-N sweep. Empty list =
    # no hints available (CLI invocation without scout, or scout failure).
    page_hints: List[int] = field(default_factory=list)
    # Scout-measured gap between PDF page index and the printed folio
    # visible in the page image footer. Passed through to each notes
    # agent's system prompt so citations stay on the PDF-page scale
    # (Phase 4). 0 = no offset (cover/TOC-free PDF); caller sets this
    # from `Infopack.page_offset` when an infopack is available.
    page_offset: int = 0
    # Audit-DB wiring for Step 6 (notes rich-editor plan): when both
    # are set, the coordinator persists each successful agent's
    # `cells_written` manifest to the `notes_cells` table so the
    # post-run editor (Phase 3) has canonical HTML to load. Either
    # left unset (CLI invocations, unit tests that don't exercise
    # persistence) skips the persist step cleanly.
    run_id: Optional[int] = None
    audit_db_path: Optional[str] = None
    # Item 28 — per-entity advisory memory. Set by the server when this run's
    # entity matched a prior completed run (an entity_memory.PriorYearAdvisory).
    # Rendered into each notes prompt as advisory-only prior-year hints; None
    # when no match / disabled.
    prior_year_advisory: Any = None

    def model_for(self, template_type: NotesTemplateType) -> Any:
        """Resolve the model instance to use for a given notes template.

        Keeps call-sites simple: ``config.model_for(nt)`` instead of
        ``config.models.get(nt, config.model)`` with a fallback every time.
        """
        return self.models.get(template_type, self.model)


# Per-turn LLM timeout for notes agents. If ``agent.iter`` doesn't
# produce the next node within this many seconds the runner aborts.
# Observed failure: after a successful ``write_notes`` call the LLM's
# follow-up turn sometimes stalls for minutes, keeping the agent
# alive and blocking ``run_notes_extraction``'s ``wait(ALL_COMPLETED)``
# — siblings finish their work but the whole run hangs waiting on a
# model that won't speak. 180s is well above the healthy p99 for a
# model-request turn on gpt-5.4 / gemini-3 while still catching the
# minutes-long stalls that triggered the fix.
NOTES_TURN_TIMEOUT: float = 180.0

# Item 18: cancel-path grace period before a surviving child task is
# logged as a possible leak (mirrors coordinator.CANCEL_GRACE_PERIOD_S).
CANCEL_GRACE_PERIOD_S: float = 5.0

# `_iter_with_turn_timeout` is imported from agent_runner above (the per-step
# timeout helper used to be defined here; rewrite Phase 2 moved it).


@dataclass
class NotesAgentResult:
    template_type: NotesTemplateType
    status: str  # succeeded / failed / cancelled
    workbook_path: Optional[str] = None
    error: Optional[str] = None
    # v17 (item 9): machine-readable failure class (coordinator.py
    # ERROR_TYPE_* vocabulary). None on success; when left None on a
    # failed/cancelled result, server persistence derives it from status +
    # error so the run_agents row never carries NULL on a failure.
    error_type: Optional[str] = None
    # Non-fatal issues surfaced to History / SSE without flipping status.
    # Populated from writer skip-list (unresolvable labels, formula-cell
    # collisions) and borderline fuzzy matches. Keeping status=succeeded
    # preserves PLAN §4 Checkpoint C ("partial coverage is success") but
    # the warnings give operators a reviewable audit trail.
    warnings: List[str] = field(default_factory=list)
    # Per-cell HTML payload manifest produced by the writer. Each entry
    # carries enough info for `notes.persistence.persist_notes_cells` to
    # upsert the row in the `notes_cells` table (Step 6 of the rich
    # editor plan). Empty when the agent didn't write any prose cells
    # (numeric-only sheets or total failures).
    cells_written: List[dict] = field(default_factory=list)
    # Per-cell NUMERIC manifest from the writer (PLAN-notes-template-registry
    # Step 9). Each entry: sheet, row, col (numeric column B/C/D/E), value,
    # evidence. The coordinator projects these into run_concept_facts via
    # cell_resolver so numeric notes (sheets 13/14) are captured like face
    # statements. Empty on prose sheets / total failures.
    numeric_cells: List[dict] = field(default_factory=list)
    # End-of-run usage so server.py can backfill run_agents.total_tokens
    # / total_cost. Mirrors the face-coordinator AgentResult addition.
    total_tokens: int = 0
    total_cost: float = 0.0
    # v8 per-turn telemetry (peer-review [2]) — same shape as the face
    # AgentResult. Populated for the single-agent notes path; the Sheet-12
    # fan-out leaves these empty (its sub-agents merge into one row).
    turns: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # v15 cache telemetry — bubbles from _SingleAgentOutcome (peer-review F2).
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    turn_count: int = 0
    tool_call_count: int = 0


@dataclass
class NotesCoordinatorResult:
    agent_results: List[NotesAgentResult] = field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return all(r.status == "succeeded" for r in self.agent_results)

    @property
    def workbook_paths(self) -> Dict[NotesTemplateType, str]:
        return {
            r.template_type: r.workbook_path
            for r in self.agent_results
            if r.workbook_path
        }


async def _project_numeric_notes_facts(
    config: "NotesRunConfig", result: "NotesAgentResult"
) -> Optional[Any]:
    """Project one numeric notes agent's value cells into run_concept_facts.

    The live-capture counterpart of ``persist_notes_cells`` for the numeric
    notes (sheets 13/14): resolves each ``(sheet, row, col)`` write to a
    ``concept_uuid`` and upserts a fact via the same Phase-B machinery face
    statements use, so the multi-column numeric tables the prose
    ``notes_cells`` store can't represent are captured identically
    (PLAN-notes-template-registry Step 9).

    Returns the :class:`ProjectionResult`, or ``None`` when there's nothing to
    project (no numeric cells) or the projection failed. Never raises — this is
    best-effort, mirroring ``persist_notes_cells``: the xlsx is already on disk
    so a capture failure must not fail the run. Extracted from the persistence
    loop so the wiring (writer manifest → ``project_writes``) is unit-testable
    in isolation.
    """
    if not result.numeric_cells:
        return None
    from notes_types import notes_template_path
    from concept_model.parser import _derive_template_id
    from concept_model.cell_resolver import project_writes
    try:
        numeric_template_id = _derive_template_id(
            notes_template_path(
                result.template_type,
                level=config.filing_level,
                standard=config.filing_standard,
            )
        )
        projection = await asyncio.to_thread(
            project_writes,
            config.audit_db_path,
            config.run_id,
            numeric_template_id,
            result.numeric_cells,
            filing_level=config.filing_level,
        )
        if projection.has_gaps:
            logger.warning(
                "Numeric notes projection for %s had gaps: "
                "%d skipped, %d rejected",
                result.template_type.value,
                len(projection.skipped),
                len(projection.rejected),
            )
        return projection
    except Exception:  # noqa: BLE001 — best-effort projection
        logger.warning(
            "Failed to project numeric notes facts for %s (run_id=%s)",
            result.template_type.value, config.run_id, exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_notes_extraction(
    config: NotesRunConfig,
    infopack: Optional[Infopack] = None,
    event_queue: Optional[asyncio.Queue] = None,
    session_id: Optional[str] = None,
) -> NotesCoordinatorResult:
    """Run notes agents concurrently for each requested template.

    When ``session_id`` is provided, every per-template task is registered in
    ``task_registry`` under ``f"notes:{template.value}"`` so the abort API
    can cancel notes agents alongside face agents. The outer orchestrator
    (server.run_multi_agent_stream) owns ``task_registry.remove_session`` —
    this coordinator only registers, never removes, so a still-running notes
    coordinator won't have its task references wiped when the face
    coordinator finishes first.
    """
    if not config.notes_to_run:
        return NotesCoordinatorResult(agent_results=[])

    # Peer-review C1: defence-in-depth on output_dir. Today only trusted
    # server/CLI paths flow in (UUID-based session dirs under PROJECT_ROOT/
    # output), but a single upstream bug could escalate into path traversal
    # + blind directory creation against /etc, /tmp, etc. Resolve once
    # here and reject anything outside the project root.
    from utils.paths import assert_writable_output_dir

    assert_writable_output_dir(config.output_dir, label="notes output_dir")

    import task_registry

    inventory: list[NoteInventoryEntry] = []
    if infopack is not None and infopack.notes_inventory:
        inventory = list(infopack.notes_inventory)

    # Fall back to the infopack's derived page hints when the caller
    # didn't set them explicitly. Keeps CLI / test invocations simple
    # (they can leave ``config.page_hints`` as the default) while letting
    # the server pre-compute and pass them in if it prefers.
    page_hints: list[int] = list(config.page_hints)
    if not page_hints and infopack is not None:
        page_hints = list(infopack.notes_page_hints())

    # Same fallback rule for page_offset: prefer caller-supplied value,
    # otherwise read off the infopack. Either can be 0 — the notes
    # agent simply omits the prompt block when the offset is not
    # positive, so we don't need a separate "present?" flag.
    page_offset: int = config.page_offset
    if page_offset == 0 and infopack is not None:
        page_offset = infopack.page_offset

    # Phase 2 — entity/period/unit context dict for the notes prompt
    # renderer. None when scout couldn't enrich; passes through
    # render_notes_prompt to _render_scout_context_block which omits
    # the block entirely on degraded runs.
    scout_context: Optional[dict] = None
    if infopack is not None:
        scout_context = {
            "entity_name": infopack.entity_name,
            "reporting_period_cy": infopack.reporting_period_cy,
            "reporting_period_py": infopack.reporting_period_py,
            "currency": infopack.currency,
            "scale_unit": infopack.scale_unit,
            "consolidation_level": infopack.consolidation_level,
        }

    # Item 28 — attach the matched prior-year advisory (statement=None on the
    # notes path: no per-statement variant line, just the shared hints).
    # getattr keeps test/CLI configs that predate the field working.
    _prior_advisory = getattr(config, "prior_year_advisory", None)
    if _prior_advisory is not None:
        if scout_context is None:
            scout_context = {}
        scout_context["_prior_year"] = _prior_advisory.to_prompt_dict(None)

    # Launch one task per template.
    ordered = sorted(config.notes_to_run, key=lambda t: list(NotesTemplateType).index(t))

    tasks: dict[NotesTemplateType, asyncio.Task] = {}
    for index, template_type in enumerate(ordered):
        agent_id = f"notes:{template_type.value}"
        template_model = config.model_for(template_type)
        # Stagger parallel notes-agent launches by NOTES_LAUNCH_STAGGER_SECS
        # per index so 5 concurrent tasks don't burst requests into the
        # provider's TPM bucket at the same millisecond — OpenAI
        # gpt-5.4-mini's 200k TPM limit is easy to saturate when 5 PDF-page
        # requests all fire inside the same second. The first agent starts
        # immediately; later ones sleep briefly at the top of their runner.
        stagger = index * NOTES_LAUNCH_STAGGER_SECS
        # Sheet 12 goes through the sub-agent fan-out runner; others run
        # as a single agent.
        if template_type == NotesTemplateType.LIST_OF_NOTES:
            runner = _run_list_of_notes_fanout(
                pdf_path=config.pdf_path,
                inventory=inventory,
                filing_level=config.filing_level,
                model=template_model,
                output_dir=config.output_dir,
                event_queue=event_queue,
                agent_id=agent_id,
                session_id=session_id,
                page_hints=page_hints,
                page_offset=page_offset,
                launch_delay=stagger,
                filing_standard=config.filing_standard,
                scout_context=scout_context,
            )
        else:
            runner = _run_single_notes_agent(
                template_type=template_type,
                pdf_path=config.pdf_path,
                inventory=inventory,
                filing_level=config.filing_level,
                model=template_model,
                output_dir=config.output_dir,
                event_queue=event_queue,
                agent_id=agent_id,
                page_hints=page_hints,
                page_offset=page_offset,
                launch_delay=stagger,
                filing_standard=config.filing_standard,
                scout_context=scout_context,
            )
        task = asyncio.create_task(runner, name=agent_id)
        tasks[template_type] = task
        if session_id:
            task_registry.register(session_id, agent_id, task)

    results: list[NotesAgentResult] = []
    try:
        await asyncio.wait(list(tasks.values()), return_when=asyncio.ALL_COMPLETED)
        for template_type, task in tasks.items():
            try:
                results.append(task.result())
            except asyncio.CancelledError:
                results.append(NotesAgentResult(
                    template_type=template_type,
                    status="cancelled",
                    error="Cancelled by user",
                ))
            except Exception as e:
                results.append(NotesAgentResult(
                    template_type=template_type,
                    status="failed",
                    error=str(e),
                ))
    except asyncio.CancelledError:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        if tasks:
            _, still_pending = await asyncio.wait(
                list(tasks.values()), timeout=CANCEL_GRACE_PERIOD_S,
            )
            # Item 18: surface wedged tasks that outlive the cancel grace
            # period (logging only — never double-fault, gotcha #10).
            for t in still_pending:
                logger.warning(
                    "cancellation timeout: task %s (session %s) still "
                    "pending after %.0fs — possible leak",
                    t.get_name(), session_id or "<none>",
                    CANCEL_GRACE_PERIOD_S,
                )
        raise

    # Step 6 of the notes rich-editor plan: persist per-cell HTML for
    # every successful agent so the post-run editor and the download
    # overlay have a canonical payload. Each sheet's cells are wiped
    # and re-upserted (clobber semantics — re-runs replace prior
    # content rather than merging on top). Best-effort: a persistence
    # failure must not fail the run — the xlsx is already on disk and
    # the operator still sees the result.
    if config.run_id is not None and config.audit_db_path:
        from notes.persistence import persist_notes_cells
        from notes_types import NOTES_REGISTRY
        for r in results:
            if r.status != "succeeded":
                continue
            # NUMERIC notes (sheets 13/14): project the value cells into
            # run_concept_facts via the same Phase-B machinery face statements
            # use. This is the LIVE capture (per-template, during the run) of
            # the multi-column numeric tables the prose `notes_cells` store
            # can't represent (PLAN-notes-template-registry Step 9). Best-effort
            # — a projection failure must not fail the run (the xlsx is already
            # on disk), mirroring the persist_notes_cells contract below.
            await _project_numeric_notes_facts(config, r)
            # Source the sheet name from the registry (not from the
            # cells_written list) so a succeeded-but-empty agent still
            # clobbers prior rows. Otherwise a second run that writes
            # zero prose cells (numeric-only template, or an LLM that
            # wrote nothing on re-attempt) would leave stale content
            # from the prior run — visible in the editor and overlaid
            # on every download — with no way for the user to tell
            # the xlsx is lagging the DB.
            registry_entry = NOTES_REGISTRY.get(r.template_type)
            if registry_entry is None:
                continue
            sheet = registry_entry.sheet_name
            try:
                # Persistence is synchronous sqlite — offload to a
                # thread so the event loop is not blocked while the
                # DB BEGIN IMMEDIATE acquires its write lock.
                # `cells_written` may legitimately be empty: the
                # persist helper still runs the clobber inside its
                # transaction before looping over the (empty) batch,
                # so stale rows are wiped.
                await asyncio.to_thread(
                    persist_notes_cells,
                    db_path=config.audit_db_path,
                    run_id=config.run_id,
                    sheet_name=sheet,
                    cells_written=r.cells_written,
                )
            except Exception:  # noqa: BLE001 — best-effort persistence
                logger.warning(
                    "Failed to persist notes_cells for %s (run_id=%s)",
                    r.template_type.value, config.run_id, exc_info=True,
                )

    return NotesCoordinatorResult(agent_results=results)


# ---------------------------------------------------------------------------
# Per-agent runner — patched in unit tests
# ---------------------------------------------------------------------------

# PLAN §4 Phase E.1 — max retries per single notes agent. The whole sheet is
# re-attempted on any non-cancellation error (including the "finished
# without writing any payloads" silent-miss guard). Kept as a module-level
# constant so failure-injection tests can patch it per-invocation.
SINGLE_AGENT_MAX_RETRIES = 1

# Seconds of stagger between parallel notes-template launches. With 5
# templates, this spreads first-turn requests across ~4 seconds so a
# single TPM burst doesn't knock out all 5 on the first attempt. Small
# enough to be invisible next to the dominant PDF-extraction latency
# (~15-30s per agent) but big enough to decouple their initial bursts.
# Set to 0 to disable staggering (e.g. in unit tests that assert
# ordering without real sleeps).
NOTES_LAUNCH_STAGGER_SECS = 0.8


class _NoWriteError(RuntimeError):
    """Raised when an attempt finishes cleanly but wrote no payloads.

    Treated as a retryable failure — the model sometimes gets distracted,
    and a second attempt is cheap insurance before giving up on the sheet.
    """


@dataclass
class _SingleAgentOutcome:
    """Full result from one successful invocation of a single-sheet notes
    agent. Carries the filled-workbook path plus writer diagnostics so the
    retry loop can lift them into ``NotesAgentResult.warnings`` (peer-review
    [HIGH] — single-sheet runs used to silently drop skip-errors and
    borderline fuzzy matches)."""
    filled_path: str
    write_errors: list[str] = field(default_factory=list)
    fuzzy_matches: list[tuple[str, str, float]] = field(default_factory=list)
    # HTML-sanitiser warnings (Step 5 of the notes rich-editor plan).
    # Surfaced by the writer when a payload contained something the
    # sanitiser had to strip (e.g. <script>, inline event handlers).
    sanitizer_warnings: list[str] = field(default_factory=list)
    # Per-cell HTML manifest produced by the writer. See
    # `NotesAgentResult.cells_written` for the entry shape.
    cells_written: list[dict] = field(default_factory=list)
    # Per-cell NUMERIC manifest (PLAN-notes-template-registry Step 9). See
    # `NotesAgentResult.numeric_cells` for the entry shape.
    numeric_cells: list[dict] = field(default_factory=list)
    # End-of-run usage; bubbles up through the retry loop into
    # NotesAgentResult.total_tokens / total_cost. Zero when the agent
    # short-circuited via the post-write timeout path (no usage object
    # is reachable after a mid-turn timeout).
    total_tokens: int = 0
    total_cost: float = 0.0
    # v8 per-turn telemetry (peer-review [2]); bubbles into NotesAgentResult.
    turns: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # v15 cache telemetry — summed from the per-turn deltas (peer-review F2).
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    tool_call_count: int = 0


async def _run_single_notes_agent(
    template_type: NotesTemplateType,
    pdf_path: str,
    inventory: list[NoteInventoryEntry],
    filing_level: str,
    model: Any,
    output_dir: str,
    event_queue: Optional[asyncio.Queue] = None,
    agent_id: str = "",
    max_retries: int = SINGLE_AGENT_MAX_RETRIES,
    page_hints: Optional[List[int]] = None,
    page_offset: int = 0,
    launch_delay: float = 0.0,
    filing_standard: str = "mfrs",
    scout_context: Optional[dict] = None,
) -> NotesAgentResult:
    """Run one notes agent end-to-end with PLAN §4 E.1 retry budget.

    Any non-cancellation exception is retried at most ``max_retries`` times
    (default 1). Rate-limit (HTTP 429) failures get a separate, larger
    budget (``RATE_LIMIT_MAX_RETRIES``) with honoured retry-after hints and
    jittered backoff — a TPM throttle isn't a real failure and shouldn't
    burn the generic-error budget. When either budget is exhausted the
    sheet is marked failed and a ``notes_<TEMPLATE>_failures.json``
    side-log is written so operators have a durable record of what blew
    up and why.

    ``launch_delay`` staggers the start of parallel notes agents so 5
    concurrent tasks don't burst requests into the provider's TPM bucket
    at the same millisecond. The coordinator sets this per task index;
    callers that run a single agent leave it at 0.
    """

    # Notes contract: safe_emit swallows Exception (the broad teardown catch
    # used inside ``except CancelledError`` blocks so a second cancellation
    # raised by ``await queue.put`` can't trap the terminal NotesAgentResult —
    # peer-review #3). The outer coordinator synthesizes its own fallback SSE
    # event if a dropped one never lands, so dropping is safe.
    _emit, _safe_emit = make_emitter(
        event_queue, agent_id, template_type.value, safe_swallow=(Exception,),
    )

    # Stagger parallel agent launches so 5 concurrent notes agents don't
    # burst requests into the provider's TPM bucket at the same instant.
    # CancelledError from the sleep propagates naturally — the outer
    # coordinator's task.result() branch maps it to status="cancelled".
    if launch_delay > 0:
        await asyncio.sleep(launch_delay)

    # Per-attempt failure history for the ``notes_<TEMPLATE>_failures.json``
    # side-log written on exhaustion. ``on_attempt_error`` appends one entry
    # per failed attempt; ``total_attempts`` for log/UX display is derived
    # from its length.
    attempts: list[dict[str, Any]] = []

    async def _attempt(retry_index: int) -> NotesAgentResult:
        # One whole invocation. Raises on failure (the invoke never converts
        # a failure to a result), so the scaffold classifies + retries; on
        # success we build the succeeded NotesAgentResult here.
        outcome = await _invoke_single_notes_agent_once(
            template_type=template_type,
            pdf_path=pdf_path,
            inventory=inventory,
            filing_level=filing_level,
            model=model,
            output_dir=output_dir,
            event_queue=event_queue,
            agent_id=agent_id,
            emit=_emit,
            page_hints=page_hints,
            page_offset=page_offset,
            filing_standard=filing_standard,
            scout_context=scout_context,
        )
        if retry_index > 0:
            logger.info("Notes agent %s recovered on attempt %d",
                        template_type.value, retry_index + 1)
        warnings = _build_single_sheet_warnings(outcome)
        if warnings:
            logger.info(
                "Notes agent %s succeeded with %d warning(s): %s",
                template_type.value, len(warnings), "; ".join(warnings[:5]),
            )
        await _emit("complete", {
            "success": True,
            "workbook_path": outcome.filled_path,
            "warnings": warnings,
        })
        return NotesAgentResult(
            template_type=template_type,
            status="succeeded",
            workbook_path=outcome.filled_path,
            warnings=warnings,
            cells_written=list(outcome.cells_written),
            numeric_cells=list(outcome.numeric_cells),
            total_tokens=outcome.total_tokens,
            total_cost=outcome.total_cost,
            # v8 per-turn telemetry (peer-review [2]).
            turns=list(outcome.turns),
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
            cache_read_tokens=outcome.cache_read_tokens,
            cache_write_tokens=outcome.cache_write_tokens,
            turn_count=len(outcome.turns),
            tool_call_count=outcome.tool_call_count,
        )

    def _record_attempt(e: BaseException) -> None:
        attempts.append({
            "attempt": len(attempts) + 1,
            "error_type": type(e).__name__,
            "error": str(e),
            "rate_limited": is_rate_limit_error(e),
        })

    async def _on_retry(total_attempts: int, last_error: Optional[str]) -> None:
        # Visible retry marker so operators see the second attempt in the live
        # UI / History timeline. Reuses the ``reading_template`` phase (the
        # PipelineStages indicator already has a pulse state for it).
        await _emit("status", {
            "phase": "reading_template",
            "message": (
                f"{template_type.value}: retrying "
                f"(attempt {total_attempts}) — last error: "
                f"{last_error or 'unknown'}"
            ),
        })

    async def _make_cancelled() -> NotesAgentResult:
        # _safe_emit (not _emit): awaiting queue.put inside an active
        # cancellation can itself be cancelled (peer-review #3).
        await _safe_emit("complete", {"success": False, "error": "Cancelled by user"})
        return NotesAgentResult(
            template_type=template_type,
            status="cancelled",
            error="Cancelled by user",
        )

    async def _make_terminal(e: BaseException, last_error: str) -> NotesAgentResult:
        logger.warning(
            "Notes agent %s failed after %d attempt(s): %s",
            template_type.value, len(attempts), last_error,
        )
        failures_path = _write_single_sheet_failure_log(
            output_dir=output_dir,
            template_type=template_type,
            attempts=attempts,
        )
        if failures_path:
            logger.info("Wrote notes failure log: %s", failures_path)
        await _emit("error", {"message": last_error or "Unknown error"})
        await _emit("complete", {
            "success": False,
            "error": last_error,
            "attempts": len(attempts),
            "failures_path": failures_path,
        })
        # v17 (item 9): classify the terminal failure from the last attempt —
        # a per-turn stall is operationally distinct from a code error.
        # ``attempts`` is always non-empty here (on_attempt_error appends before
        # terminal), but keep the original empty-list fallback (`""`) verbatim
        # so this stays byte-identical to the pre-refactor classification.
        last_exc_class = attempts[-1]["error_type"] if attempts else ""
        return NotesAgentResult(
            template_type=template_type,
            status="failed",
            error=last_error,
            error_type=(
                "turn_timeout" if last_exc_class == "TimeoutError"
                else "tool_exception"
            ),
        )

    # Two retry budgets, consumed independently (so a flaky TPM bucket doesn't
    # burn the generic budget and a real code error doesn't masquerade as rate
    # limiting): rate-limit 429s use RATE_LIMIT_MAX_RETRIES with backoff;
    # generic errors use ``max_retries`` (default 1), no backoff.
    policy = RetryPolicy(
        rate_limit_retries=RATE_LIMIT_MAX_RETRIES,
        connection_retries=0,
        generic_retries=max_retries,
        is_rate_limit=is_rate_limit_error,
    )
    return await run_agent_with_retries(
        attempt=_attempt,
        policy=policy,
        make_terminal=_make_terminal,
        make_cancelled=_make_cancelled,
        on_retry=_on_retry,
        on_attempt_error=_record_attempt,
        label=f"Notes agent {template_type.value}",
    )


async def _invoke_single_notes_agent_once(
    template_type: NotesTemplateType,
    pdf_path: str,
    inventory: list[NoteInventoryEntry],
    filing_level: str,
    model: Any,
    output_dir: str,
    event_queue: Optional[asyncio.Queue],
    agent_id: str,
    emit,
    page_hints: Optional[List[int]] = None,
    page_offset: int = 0,
    filing_standard: str = "mfrs",
    scout_context: Optional[dict] = None,
) -> _SingleAgentOutcome:
    """One invocation of a single-sheet notes agent.

    Returns the filled workbook path on success, or raises on failure so
    the outer retry loop can decide whether to try again. The no-op
    "agent returned without writing" path raises ``_NoWriteError`` so it
    participates in the retry budget (the model sometimes produces a
    successful-looking return without ever calling ``write_notes``).
    """
    agent, deps = create_notes_agent(
        template_type=template_type,
        pdf_path=pdf_path,
        inventory=inventory,
        filing_level=filing_level,
        model=model,
        output_dir=output_dir,
        page_hints=page_hints,
        page_offset=page_offset,
        filing_standard=filing_standard,
        scout_context=scout_context,
    )

    prompt = (
        f"Fill the {template_type.value} notes template from the PDF. "
        f"Follow the strategy in your system prompt."
    )

    await emit("status", {"phase": "started", "message": f"Starting {template_type.value}..."})

    # v8 per-turn telemetry capture (peer-review [2]). Populated by
    # run_agent_loop below; read after the loop for the outcome rollups.
    _turn_records: list[dict] = []

    async with agent.iter(prompt, deps=deps) as agent_run:
        try:
            # Per-turn timeout guard: if the LLM's next turn stalls past
            # NOTES_TURN_TIMEOUT, TimeoutError bubbles out to the handler
            # below. We convert it based on whether the agent already
            # wrote rows — a stall after a successful write is
            # "close enough to done" and we keep the workbook; an early
            # stall (no write yet) is a real failure and re-raises.
            notes_spec = AgentLoopSpec(
                agent_role=template_type.value,
                model=model,
                turn_timeout=NOTES_TURN_TIMEOUT,
                phase_map=NOTES_PHASE_MAP,
                phase_message=lambda role, phase: f"{role}: {phase.replace('_', ' ')}",
                set_turn_counter=False,
                # Preserve notes' historical behaviour: the old loop only timed
                # out outer node iteration, NOT the inner tool/model streams, so
                # a legitimate long-running write_notes isn't cancelled at the
                # per-turn timeout (peer-review MEDIUM, rewrite Phase 2).
                bound_inner_streams=False,
            )
            await run_agent_loop(
                agent_run, deps, notes_spec, emit, _turn_records,
            )
        except asyncio.TimeoutError:
            # LLM stalled past NOTES_TURN_TIMEOUT. If the agent already
            # wrote a workbook, the rows are on disk — treat as done so
            # the sibling notes agents aren't stuck on ALL_COMPLETED.
            # If nothing was written yet, the agent never produced
            # usable output and the outer retry loop / caller must see
            # a failure.
            if deps.wrote_once and deps.filled_path:
                logger.warning(
                    "%s: LLM stalled past %ss after write — treating as done "
                    "(rows already on disk at %s).",
                    template_type.value, NOTES_TURN_TIMEOUT, deps.filled_path,
                )
                # Short-circuit: skip trace save / token backfill (the
                # agent_run.result may be unreachable after a mid-turn
                # timeout) and return what we have.
                return _SingleAgentOutcome(
                    filled_path=deps.filled_path,
                    write_errors=list(deps.write_skip_errors),
                    fuzzy_matches=list(deps.write_fuzzy_matches),
                    sanitizer_warnings=list(deps.write_sanitizer_warnings),
                    cells_written=list(deps.cells_written),
                    numeric_cells=list(deps.numeric_cells),
                )
            raise RuntimeError(
                f"{template_type.value}: LLM stalled past {NOTES_TURN_TIMEOUT}s "
                "without writing any payloads"
            )

    result = agent_run.result
    save_agent_trace(
        result, output_dir, f"NOTES_{template_type.value}", turns=_turn_records
    )

    # Phase 5.1 + peer-review #2: backfill the cost report totals from
    # the final aggregate usage. Extracted into a helper so it can be
    # unit-tested without standing up the full agent-iter harness —
    # otherwise the wrapped try/except silently hides regressions.
    _backfill_token_report(deps.token_report, agent_run.usage, template_type.value)

    # RUN-REVIEW P2-3 (gotcha #6 closure): capture the SAME usage we
    # just folded into the cost report and bubble it up so the
    # NotesAgentResult lands real numbers in run_agents.total_tokens.
    # Best-effort — if usage is unreachable we still return success
    # rather than failing the run on advisory telemetry.
    _agent_tokens = 0
    _agent_cost = 0.0
    try:
        _u = agent_run.usage
        _prompt = int(_u.input_tokens or 0)
        _completion = int(_u.output_tokens or 0)
        _agent_tokens = int(_u.total_tokens or 0)
        _agent_cost = estimate_cost(_prompt, _completion, 0, model)
    except Exception:  # noqa: BLE001 — telemetry is best-effort
        logger.debug("notes agent token bubble-up skipped for %s", template_type.value)

    # Guard against silent no-op success — retryable per PLAN §4 E.1.
    if not deps.wrote_once or not deps.filled_path:
        raise _NoWriteError("Notes agent finished without writing any payloads")

    return _SingleAgentOutcome(
        filled_path=deps.filled_path,
        write_errors=list(deps.write_skip_errors),
        fuzzy_matches=list(deps.write_fuzzy_matches),
        sanitizer_warnings=list(deps.write_sanitizer_warnings),
        cells_written=list(deps.cells_written),
        numeric_cells=list(deps.numeric_cells),
        total_tokens=_agent_tokens,
        total_cost=_agent_cost,
        turns=list(_turn_records),
        prompt_tokens=sum(int(t.get("prompt_tokens") or 0) for t in _turn_records),
        completion_tokens=sum(int(t.get("completion_tokens") or 0) for t in _turn_records),
        cache_read_tokens=sum(int(t.get("cache_read_tokens") or 0) for t in _turn_records),
        cache_write_tokens=sum(int(t.get("cache_write_tokens") or 0) for t in _turn_records),
        tool_call_count=sum(int(t.get("_n_tool_calls") or 0) for t in _turn_records),
    )


def _build_single_sheet_warnings(outcome: _SingleAgentOutcome) -> list[str]:
    """Compose user-facing warning strings from a successful single-sheet
    run's accumulated diagnostics. Mirrors ``_build_write_warnings`` for
    the Sheet-12 fan-out path so the two success paths produce the same
    shape of ``NotesAgentResult.warnings``."""
    warnings: list[str] = []
    for err in outcome.write_errors:
        warnings.append(f"writer: {err}")
    for requested, chosen, score in outcome.fuzzy_matches:
        if score < BORDERLINE_FUZZY_SCORE:
            warnings.append(
                f"borderline fuzzy match: '{requested}' -> '{chosen}' "
                f"(score {score:.2f})"
            )
    for w in outcome.sanitizer_warnings:
        warnings.append(f"sanitiser: {w}")
    return warnings


def _write_notes12_skips(output_dir: str, sub_result: Any) -> None:
    """Write the run's Sheet-12 skip receipts to ``notes12_skips.json``.

    One ``{"note_num", "reason"}`` entry per note a succeeded sub-agent
    explicitly skipped (``action == "skipped"``). Overwrites wholesale so a
    notes re-run clears stale skips. Consumed by the coverage checklist so an
    intentional skip lands `skipped`, not `missing` (Codex review P2)."""
    skips: list[dict[str, Any]] = []
    for sub in getattr(sub_result, "sub_agent_results", None) or []:
        if getattr(sub, "status", None) != "succeeded":
            continue
        coverage = getattr(sub, "coverage", None)
        if coverage is None:
            continue
        for entry in getattr(coverage, "entries", None) or []:
            if getattr(entry, "action", None) == "skipped":
                skips.append({
                    "note_num": int(entry.note_num),
                    "reason": str(getattr(entry, "reason", "") or ""),
                })
    path = Path(output_dir) / "notes12_skips.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(skips, indent=2, ensure_ascii=False), encoding="utf-8",
    )


def _write_single_sheet_failure_log(
    output_dir: str,
    template_type: NotesTemplateType,
    attempts: list[dict[str, Any]],
) -> Optional[str]:
    """Persist per-sheet failure history after the retry budget is exhausted.

    Returns the side-log path on success, or None if we couldn't write it
    (the run still returns a terminal NotesAgentResult — the log is a nice-
    to-have audit trail, not a correctness gate).
    """
    if not attempts:
        return None
    try:
        from pathlib import Path as _Path
        from utils.sanitize import sanitize as _sanitize_for_log

        path = _Path(output_dir) / f"notes_{template_type.value}_failures.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Peer-review C2: attempt errors echo LLM/provider strings —
        # sanitise before writing so a terminal cat is safe.
        payload = _sanitize_for_log({
            "template": template_type.value,
            "attempts": attempts,
        })
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return str(path)
    except Exception:  # noqa: BLE001 — best-effort audit log
        logger.warning(
            "Failed to persist failure side-log for notes agent %s",
            template_type.value,
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Sheet 12 fan-out runner
# ---------------------------------------------------------------------------

async def _run_list_of_notes_fanout(
    pdf_path: str,
    inventory: list[NoteInventoryEntry],
    filing_level: str,
    model: Any,
    output_dir: str,
    event_queue: Optional[asyncio.Queue] = None,
    agent_id: str = "notes:LIST_OF_NOTES",
    session_id: Optional[str] = None,
    page_hints: Optional[List[int]] = None,
    page_offset: int = 0,
    launch_delay: float = 0.0,
    filing_standard: str = "mfrs",
    scout_context: Optional[dict] = None,
) -> NotesAgentResult:
    """Drive the Sheet-12 sub-agent fan-out and write the final workbook.

    Returns a NotesAgentResult shaped the same as single-agent runs so the
    coordinator result list is homogeneous and the merger doesn't care.

    ``launch_delay`` staggers this template's start relative to the other
    notes templates. The sub-agents fanned out *inside* the subcoordinator
    have their own launch stagger applied there (see
    ``run_listofnotes_subcoordinator``).
    """
    # Stagger start relative to sibling notes templates so the first
    # request doesn't land simultaneously with 4 other sheets. See the
    # single-agent runner for the same pattern.
    if launch_delay > 0:
        await asyncio.sleep(launch_delay)
    from notes.writer import write_notes_workbook
    from notes_types import NOTES_REGISTRY, notes_template_path
    from pricing import resolve_notes_parallel

    # Cancellation-safe emit swallows Exception — see the single-agent variant
    # for rationale (peer-review #3); safe_emit is used only inside
    # ``except CancelledError``.
    _emit, _safe_emit = make_emitter(
        event_queue, agent_id, NotesTemplateType.LIST_OF_NOTES.value,
        safe_swallow=(Exception,),
    )

    try:
        await _emit("status", {
            "phase": "started",
            "message": f"Starting {NotesTemplateType.LIST_OF_NOTES.value} ({len(inventory)} notes)...",
        })

        # Empty-inventory short-circuit (peer-review #1, post-Phase 2):
        # Scout's deterministic notes-inventory builder returns [] on
        # scanned PDFs (PyMuPDF finds no text). With an empty inventory the
        # sub-coordinator would produce zero batches, the aggregated
        # payload list would stay empty, and the writer would report
        # success=True on a no-op write — shipping an untouched template
        # copy as "Sheet-12 succeeded" while hiding the gap from reviewers.
        # That is worse than a loud failure: operators lose the signal to
        # re-run with a better scout or switch to a text-extractable PDF.
        # Fail the sheet explicitly so the run shows "Notes-12 failed:
        # empty inventory" in History instead of a silent green tick.
        if not inventory:
            # Short one-sentence message for UI toasts + history. The full
            # diagnostic (operator-facing: scanned-PDF hint, PyMuPDF context,
            # the "fail loud" rationale) lands in the structured log
            # identified by session_id — SSE/history doesn't have room for
            # a wall of text (PR B.4).
            err = "Notes-12: no inventory to fan out (scout found no note headers)"
            logger.error(
                "Notes-12 empty inventory for session=%s: scout's deterministic "
                "PyMuPDF-regex pass found no note headers (common on scanned "
                "PDFs where PyMuPDF cannot extract text). Failing the sheet "
                "loudly instead of shipping an untouched template.",
                session_id,
            )
            await _emit("error", {"message": err})
            await _emit("complete", {"success": False, "error": err})
            return NotesAgentResult(
                template_type=NotesTemplateType.LIST_OF_NOTES,
                status="failed",
                error=err,
            )

        # Model-aware fan-out width. Cheap/fast models (gpt-5.4-mini,
        # gemini-*-flash-*, haiku) saturate the provider's TPM bucket
        # faster than heavy/slow models and hit HTTP 429 at parallel=5;
        # the registry in config/models.json drops them to 2. Unknown
        # models fall back to the previous hardcoded default (5) so
        # operators can drop in new model ids without a registry edit.
        parallel = resolve_notes_parallel(model)
        logger.info(
            "Notes-12 fan-out: %d-way (model=%s)",
            parallel,
            getattr(model, "model_name", str(model)),
        )

        sub_result = await run_listofnotes_subcoordinator(
            pdf_path=pdf_path,
            inventory=inventory,
            filing_level=filing_level,
            model=model,
            output_dir=output_dir,
            event_queue=event_queue,
            session_id=session_id,
            agent_id=agent_id,
            parallel=parallel,
            page_hints=page_hints,
            page_offset=page_offset,
            scout_context=scout_context,
        )

        # Persist Sheet-12 skip receipts as a durable side-log so the notes
        # coverage checklist (server._compute_notes_coverage_checklist) marks
        # an INTENTIONALLY skipped note `skipped` (reason attached) instead of
        # `missing` — a skipped note has no provenance, so inventory×provenance
        # alone would flag it missing and wrongly tip the run to
        # completed_with_errors. Read from output_dir by BOTH the auto and the
        # manual reviewer passes. Best-effort — never fail the fan-out.
        try:
            _write_notes12_skips(output_dir, sub_result)
        except Exception:  # noqa: BLE001
            logger.debug("notes12 skips side-log write skipped", exc_info=True)

        # Sum each sub-agent's captured usage up front — every return
        # branch below (skip carve-out, total failure, normal write)
        # attaches these rollups to the NotesAgentResult so the parent
        # run_agents row records real spend. Before this the sums only
        # reached the cost-report text file, so the Activity tab showed
        # Sheet-12 as 0 tokens / $0 despite real spend (run-168 QA
        # finding). Per-turn detail intentionally stays empty (gotcha
        # #6: the sub-agents merge into one row).
        total_prompt = sum(r.prompt_tokens for r in sub_result.sub_agent_results)
        total_completion = sum(r.completion_tokens for r in sub_result.sub_agent_results)
        try:
            rollup_cost = estimate_cost(total_prompt, total_completion, 0, model)
        except Exception:  # noqa: BLE001 — an unknown model must not zero the token rollup
            rollup_cost = 0.0

        # Total-failure guard: an empty aggregated payload list coming out
        # of a non-empty inventory means every sub-agent lost coverage.
        # Writer treats empty payloads as a no-op success, so without this
        # check the sheet would silently report succeeded with an untouched
        # xlsx. Partial coverage (some payloads present) remains a success
        # per PLAN §4 Checkpoint C.
        #
        # Coverage-receipt carve-out (peer-review [HIGH]): if every
        # succeeded sub-agent submitted a valid receipt that fully
        # accounts for its batch — and every entry is `skipped` — then
        # zero payloads is a legitimate outcome ("everything in this
        # sheet belongs elsewhere"), not a failure. This mirrors the
        # same carve-out at the sub-agent layer
        # (_SubAgentNoWriteError). Failure is still the default for
        # any sub-agent missing a receipt, for any uncovered notes,
        # or for a hard sub-agent failure.
        if not sub_result.aggregated_payloads:
            def _fully_accounts_for_skips(sub_results: list) -> bool:
                """True iff every succeeded sub-agent has a receipt
                covering its full batch with ONLY skipped entries.
                Sub-agents with status='failed' are not receipt-
                carrying and break the carve-out — partial coverage
                is still a failure."""
                if not sub_results:
                    return False
                for r in sub_results:
                    if r.status != "succeeded":
                        return False
                    if r.coverage is None:
                        return False
                    batch_nums = {e.note_num for e in r.batch}
                    receipt_nums = {e.note_num for e in r.coverage.entries}
                    if receipt_nums != batch_nums:
                        return False
                    if not all(
                        e.action == "skipped" for e in r.coverage.entries
                    ):
                        return False
                return True

            if _fully_accounts_for_skips(sub_result.sub_agent_results):
                # Deliberately blank sheet — every sub-agent submitted a
                # receipt saying "this note belongs elsewhere". The writer
                # treats `rows_written == 0` as failure, so we don't call
                # it. We still need a workbook on disk for the merger —
                # peer-review [HIGH]: without it, a user-requested
                # Notes-Listofnotes silently disappears from the final
                # filled.xlsx. Copy the template verbatim so the merged
                # workbook contains an untouched Sheet-12 page. Warnings
                # from the coordinator's warning builder will still carry
                # every "Note N skipped: ..." line.
                template = str(notes_template_path(
                    NotesTemplateType.LIST_OF_NOTES,
                    level=filing_level,
                    standard=filing_standard,
                ))
                output_path = str(
                    Path(output_dir)
                    / f"NOTES_{NotesTemplateType.LIST_OF_NOTES.value}_filled.xlsx"
                )
                await asyncio.to_thread(shutil.copy, template, output_path)

                warnings_only = _build_write_warnings(
                    _EmptyWriteResult(), sub_result,
                )
                logger.info(
                    "Notes-12: empty aggregate covered by skip receipts "
                    "— treating as success with %d warning(s)",
                    len(warnings_only),
                )
                await _emit("complete", {
                    "success": True,
                    "warnings": warnings_only,
                })
                return NotesAgentResult(
                    template_type=NotesTemplateType.LIST_OF_NOTES,
                    status="succeeded",
                    workbook_path=output_path,
                    warnings=warnings_only,
                    # Sub-agents still burned tokens deciding to skip.
                    prompt_tokens=total_prompt,
                    completion_tokens=total_completion,
                    total_tokens=total_prompt + total_completion,
                    total_cost=rollup_cost,
                )

            failed = [r for r in sub_result.sub_agent_results if r.status == "failed"]
            errors_joined = "; ".join(
                f"{r.sub_agent_id}: {r.error or 'unknown error'}" for r in failed
            ) or "every sub-agent produced zero payloads"
            err = f"all sub-agents failed — {errors_joined}"
            await _emit("error", {"message": f"Notes-12: {err}"})
            await _emit("complete", {"success": False, "error": err})
            return NotesAgentResult(
                template_type=NotesTemplateType.LIST_OF_NOTES,
                status="failed",
                error=err,
                # A failed pass still spent tokens — report them honestly.
                prompt_tokens=total_prompt,
                completion_tokens=total_completion,
                total_tokens=total_prompt + total_completion,
                total_cost=rollup_cost,
            )

        # Final workbook write — one call with the aggregated payload list.
        # The writer handles row-concatenation (including row 112) and
        # evidence-column placement based on filing_level.
        entry = NOTES_REGISTRY[NotesTemplateType.LIST_OF_NOTES]
        template = str(notes_template_path(
            NotesTemplateType.LIST_OF_NOTES,
            level=filing_level,
            standard=filing_standard,
        ))
        output_path = str(Path(output_dir) / f"NOTES_{NotesTemplateType.LIST_OF_NOTES.value}_filled.xlsx")

        write_result = await asyncio.to_thread(
            write_notes_workbook,
            template_path=template,
            payloads=sub_result.aggregated_payloads,
            output_path=output_path,
            filing_level=filing_level,
            sheet_name=entry.sheet_name,
        )

        if not write_result.success:
            err = "; ".join(write_result.errors) or "write failed"
            await _emit("error", {"message": f"Notes-12 write failed: {err}"})
            await _emit("complete", {"success": False, "error": err})
            return NotesAgentResult(
                template_type=NotesTemplateType.LIST_OF_NOTES,
                status="failed",
                error=err,
                # The sub-agents ran and spent tokens before the write
                # failed — report them like the other failure branches.
                prompt_tokens=total_prompt,
                completion_tokens=total_completion,
                total_tokens=total_prompt + total_completion,
                total_cost=rollup_cost,
            )

        # Phase 5.1: write the parent Sheet-12 cost report. Best-effort:
        # a write failure here must not fail the sheet, so we log and
        # continue.
        try:
            report_lines = [
                f"Sheet 12 (List of Notes) — aggregate across {len(sub_result.sub_agent_results)} sub-agent(s)",
                "─" * 80,
                f"{'Sub-agent':<30} {'Status':<10} {'Prompt':>10} {'Complete':>10}",
                "─" * 80,
            ]
            for r in sub_result.sub_agent_results:
                report_lines.append(
                    f"{r.sub_agent_id:<30} {r.status:<10} "
                    f"{r.prompt_tokens:>10} {r.completion_tokens:>10}"
                )
            report_lines.append("─" * 80)
            report_lines.append(
                f"{'Total':<30} {'':<10} {total_prompt:>10} {total_completion:>10}"
            )
            report_lines.append("")
            report_lines.append(f"Estimated cost: ${rollup_cost:.4f}")
            report_path = Path(output_dir) / (
                f"NOTES_{NotesTemplateType.LIST_OF_NOTES.value}_cost_report.txt"
            )
            await asyncio.to_thread(
                report_path.write_text, "\n".join(report_lines), encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            logger.debug("Notes-12 cost report write skipped", exc_info=True)

        # Peer-review finding: writer may succeed with `success=True`
        # while also emitting skip errors (e.g. unresolvable labels) and
        # borderline fuzzy matches. Surface them as warnings instead of
        # silently discarding — the sheet is still "succeeded" per
        # PLAN §4 Checkpoint C, but History/SSE now carry the detail.
        warnings = _build_write_warnings(write_result, sub_result)
        if warnings:
            logger.warning(
                "Notes-12 completed with %d warning(s): %s",
                len(warnings), "; ".join(warnings[:5]),
            )
        # Warnings ride on the `complete` event payload below — the frontend
        # SSE filter intentionally recognises only the fixed event vocabulary,
        # and complete is durable (persisted to History). A dedicated
        # "warning" event would be dropped on the floor.

        await _emit("complete", {
            "success": True,
            "workbook_path": output_path,
            "rows_written": write_result.rows_written,
            "unmatched_count": len(sub_result.unmatched_payloads),
            "failed_sub_agents": sum(
                1 for r in sub_result.sub_agent_results if r.status == "failed"
            ),
            "write_errors": list(write_result.errors),
            "fuzzy_match_count": len(write_result.fuzzy_matches),
            "warnings": warnings,
        })

        # Partial coverage (some sub-agents failed but others produced
        # payloads) is still a success at the sheet level — PLAN §4
        # Checkpoint C: "Failure of one sub-agent produces partial
        # coverage, not a whole-sheet failure."
        return NotesAgentResult(
            template_type=NotesTemplateType.LIST_OF_NOTES,
            status="succeeded",
            workbook_path=output_path,
            warnings=warnings,
            # Sheet-12 rolls up the sub-agents' payloads into one write
            # pass; the single `write_result.cells_written` is therefore
            # the authoritative manifest for persistence (Step 6).
            cells_written=list(write_result.cells_written),
            # Token ROLLUPS populate even though per-turn detail stays
            # empty (gotcha #6: sub-agents merge into one row). Without
            # these the parent run_agents row records 0 tokens / $0.
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            total_tokens=total_prompt + total_completion,
            total_cost=rollup_cost,
        )

    except asyncio.CancelledError:
        await _safe_emit("complete", {"success": False, "error": "Cancelled by user"})
        return NotesAgentResult(
            template_type=NotesTemplateType.LIST_OF_NOTES,
            status="cancelled",
            error="Cancelled by user",
        )
    except Exception as e:
        logger.exception("Notes-12 fan-out failed")
        await _emit("error", {"message": str(e)})
        await _emit("complete", {"success": False, "error": str(e)})
        return NotesAgentResult(
            template_type=NotesTemplateType.LIST_OF_NOTES,
            status="failed",
            error=str(e),
        )


@dataclass
class _EmptyWriteResult:
    """Stand-in for a real `NotesWriteResult` on the all-skipped success
    path where no write was performed.

    `_build_write_warnings` reads `errors` and `fuzzy_matches` off the
    write result. On the all-skipped carve-out we never call the writer
    (zero-row writes return success=False which would otherwise tip the
    sheet back into failure), so the warnings come exclusively from the
    sub-agent receipts. This class supplies the fields the warning
    builder needs without pulling in the heavier dataclass.

    Peer-review #12: `errors` and `fuzzy_matches` are per-instance
    lists (`field(default_factory=list)`), not class-level mutables.
    Today's readers are read-only so the old class-level defaults were
    latent, but a future append would silently cross-contaminate every
    no-op sheet on the same process.
    """

    errors: list[str] = field(default_factory=list)
    fuzzy_matches: list[tuple[str, str, float]] = field(default_factory=list)


def _build_write_warnings(write_result: Any, sub_result: Any) -> List[str]:
    """Compose user-facing warning strings from a writer result + sub-agent result.

    Each warning is one line, formatted for the SSE/History UI without
    further processing. Borderline fuzzy matches are flagged individually
    (with both labels + score) so an operator can spot-check them.
    """
    warnings: List[str] = []
    for err in write_result.errors:
        warnings.append(f"writer: {err}")
    for requested, chosen, score in write_result.fuzzy_matches:
        if score < BORDERLINE_FUZZY_SCORE:
            warnings.append(
                f"borderline fuzzy match: '{requested}' -> '{chosen}' "
                f"(score {score:.2f})"
            )
    failed_subs = [r for r in sub_result.sub_agent_results if r.status == "failed"]
    if failed_subs:
        warnings.append(
            f"{len(failed_subs)} of {len(sub_result.sub_agent_results)} "
            f"sub-agent(s) failed — partial coverage only"
        )
    # Slice 6: surface coverage-receipt outcomes through the same warning
    # channel. Skipped entries are one line per skip so operators can
    # judge whether each skip was legitimate (cross-sheet) or a missed
    # disclosure. Uncovered notes (no receipt submitted) are one line
    # per note — more explicit than a collapsed "N notes uncovered"
    # summary because the user needs to know WHICH notes to manually
    # re-check.
    for sub in sub_result.sub_agent_results:
        if sub.coverage is None:
            # No receipt — every batch note is uncovered. Only emit a
            # warning for sub-agents that actually succeeded at writing
            # but skipped the handshake; for hard failures the existing
            # "N of M sub-agent(s) failed" line already tells the story.
            if sub.status == "succeeded":
                for entry in sub.batch:
                    warnings.append(
                        f"Note {entry.note_num} uncovered — sub-agent did "
                        f"not submit a coverage receipt."
                    )
            continue
        for entry in sub.coverage.entries:
            if entry.action == "skipped":
                warnings.append(
                    f"Note {entry.note_num} skipped: {entry.reason}"
                )
    return warnings
