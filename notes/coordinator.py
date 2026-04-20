"""Notes coordinator — fans out one agent per requested notes template.

Mirrors `coordinator.py` for face statements, but uses notes agents from
`notes.agent` and emits events under `agent_id = "notes:<TEMPLATE>"`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
    ThinkingPartDelta,
)

from agent_tracing import MAX_AGENT_ITERATIONS, save_agent_trace
from notes.agent import create_notes_agent
from notes.writer import BORDERLINE_FUZZY_SCORE
from notes_types import NotesTemplateType
from pricing import estimate_cost
from scout.notes_discoverer import NoteInventoryEntry

logger = logging.getLogger(__name__)


# Tool-name → phase mapping. Mirrors coordinator.PHASE_MAP so the frontend
# timeline can colour-code notes-agent phases identically to face agents.
NOTES_PHASE_MAP = {
    "read_template": "reading_template",
    "view_pdf_pages": "viewing_pdf",
    "write_notes": "writing_notes",
    "save_result": "complete",
}


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

    def model_for(self, template_type: NotesTemplateType) -> Any:
        """Resolve the model instance to use for a given notes template.

        Keeps call-sites simple: ``config.model_for(nt)`` instead of
        ``config.models.get(nt, config.model)`` with a fallback every time.
        """
        return self.models.get(template_type, self.model)


@dataclass
class NotesAgentResult:
    template_type: NotesTemplateType
    status: str  # succeeded / failed / cancelled
    workbook_path: Optional[str] = None
    error: Optional[str] = None
    # Non-fatal issues surfaced to History / SSE without flipping status.
    # Populated from writer skip-list (unresolvable labels, formula-cell
    # collisions) and borderline fuzzy matches. Keeping status=succeeded
    # preserves PLAN §4 Checkpoint C ("partial coverage is success") but
    # the warnings give operators a reviewable audit trail.
    warnings: List[str] = field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_notes_extraction(
    config: NotesRunConfig,
    infopack: Any = None,
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

    import task_registry

    inventory: list[NoteInventoryEntry] = []
    if infopack is not None and getattr(infopack, "notes_inventory", None):
        inventory = list(infopack.notes_inventory)

    # Fall back to the infopack's derived page hints when the caller
    # didn't set them explicitly. Keeps CLI / test invocations simple
    # (they can leave ``config.page_hints`` as the default) while letting
    # the server pre-compute and pass them in if it prefers.
    page_hints: list[int] = list(config.page_hints)
    if not page_hints and infopack is not None:
        derive = getattr(infopack, "notes_page_hints", None)
        if callable(derive):
            try:
                page_hints = list(derive())
            except Exception:  # noqa: BLE001 — hints are advisory; never block a run
                logger.warning(
                    "Failed to derive notes_page_hints from infopack; proceeding without hints",
                    exc_info=True,
                )
                page_hints = []

    # Launch one task per template.
    ordered = sorted(config.notes_to_run, key=lambda t: list(NotesTemplateType).index(t))

    tasks: dict[NotesTemplateType, asyncio.Task] = {}
    for template_type in ordered:
        agent_id = f"notes:{template_type.value}"
        template_model = config.model_for(template_type)
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
            await asyncio.wait(list(tasks.values()), timeout=5.0)
        raise

    return NotesCoordinatorResult(agent_results=results)


# ---------------------------------------------------------------------------
# Per-agent runner — patched in unit tests
# ---------------------------------------------------------------------------

# PLAN §4 Phase E.1 — max retries per single notes agent. The whole sheet is
# re-attempted on any non-cancellation error (including the "finished
# without writing any payloads" silent-miss guard). Kept as a module-level
# constant so failure-injection tests can patch it per-invocation.
SINGLE_AGENT_MAX_RETRIES = 1


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
) -> NotesAgentResult:
    """Run one notes agent end-to-end with PLAN §4 E.1 retry budget.

    Any non-cancellation exception is retried at most ``max_retries`` times
    (default 1). When the retry budget is exhausted the sheet is marked
    failed and a ``notes_<TEMPLATE>_failures.json`` side-log is written so
    operators have a durable record of what blew up and why.
    """

    async def _emit(event_type: str, data: dict) -> None:
        if event_queue is not None:
            await event_queue.put({
                "event": event_type,
                "data": {**data, "agent_id": agent_id, "agent_role": template_type.value},
            })

    async def _safe_emit(event_type: str, data: dict) -> None:
        """Emit variant that swallows errors — use inside ``except CancelledError``
        blocks so a second cancellation raised by the ``await queue.put`` can't
        trap the coordinator before it returns a terminal NotesAgentResult
        (peer-review #3). The outer coordinator synthesizes its own fallback
        SSE event if this one never lands, so dropping it is safe.
        """
        try:
            await _emit(event_type, data)
        except Exception:  # noqa: BLE001 — defensive teardown path
            logger.debug(
                "Dropped %s event during cancellation teardown for %s",
                event_type, agent_id or template_type.value,
            )

    attempts: list[dict[str, Any]] = []
    last_error: Optional[str] = None
    filled_path: Optional[str] = None

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                # Emit a visible retry marker so operators see the second
                # attempt in the live UI / History timeline instead of
                # guessing from the tool-event duplication. Reuses the
                # existing ``reading_template`` phase so the PipelineStages
                # indicator already has a pulse state for it — a dedicated
                # ``retrying`` phase would need frontend plumbing for one
                # edge-case message (peer-review #4 / #5 follow-up). The
                # message text carries the attempt count so the UI can
                # still surface the retry explicitly.
                await _emit("status", {
                    "phase": "reading_template",
                    "message": (
                        f"{template_type.value}: retrying "
                        f"(attempt {attempt + 1}/{max_retries + 1}) — last error: "
                        f"{last_error or 'unknown'}"
                    ),
                })
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
            )
            # Success — stop retrying.
            if attempt > 0:
                logger.info("Notes agent %s recovered on retry %d",
                            template_type.value, attempt)
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
            )
        except asyncio.CancelledError:
            # Never retry on user cancellation — propagate the cancellation
            # status untouched so task_registry abort logic remains predictable.
            # _safe_emit (not _emit) because awaiting a queue.put inside an
            # active cancellation can itself be cancelled, which would trap
            # the return below (peer-review #3).
            await _safe_emit("complete", {"success": False, "error": "Cancelled by user"})
            return NotesAgentResult(
                template_type=template_type,
                status="cancelled",
                error="Cancelled by user",
            )
        except Exception as e:  # noqa: BLE001 — we explicitly want broad catch
            last_error = str(e)
            attempts.append({
                "attempt": attempt + 1,
                "error_type": type(e).__name__,
                "error": last_error,
            })
            if attempt < max_retries:
                logger.warning(
                    "Notes agent %s failed on attempt %d/%d: %s — retrying",
                    template_type.value, attempt + 1, max_retries + 1, e,
                )
                continue
            logger.exception("Notes agent %s failed after %d attempt(s)",
                             template_type.value, attempt + 1)

    # Retries exhausted — persist the failure log and return a terminal result.
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
    return NotesAgentResult(
        template_type=template_type,
        status="failed",
        error=last_error,
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
    )

    prompt = (
        f"Fill the {template_type.value} notes template from the PDF. "
        f"Follow the strategy in your system prompt."
    )

    await emit("status", {"phase": "started", "message": f"Starting {template_type.value}..."})

    iteration = 0
    tool_start: dict[str, float] = {}
    thinking_counter = 0

    async with agent.iter(prompt, deps=deps) as agent_run:
        async for node in agent_run:
            iteration += 1
            if iteration > MAX_AGENT_ITERATIONS:
                raise RuntimeError(
                    f"Hit iteration limit ({MAX_AGENT_ITERATIONS}) — agent may be stuck."
                )
            if Agent.is_call_tools_node(node):
                async with node.stream(agent_run.ctx) as tool_stream:
                    async for event in tool_stream:
                        if isinstance(event, FunctionToolCallEvent):
                            phase = NOTES_PHASE_MAP.get(event.part.tool_name)
                            if phase:
                                await emit("status", {
                                    "phase": phase,
                                    "message": f"{template_type.value}: {phase.replace('_', ' ')}",
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
                            await emit("tool_call", {
                                "tool_name": event.part.tool_name,
                                "tool_call_id": event.part.tool_call_id,
                                "args": parsed,
                            })
                            tool_start[event.part.tool_call_id] = time.monotonic()
                        elif isinstance(event, FunctionToolResultEvent):
                            content = event.result.content
                            summary = str(content)[:800] if content else ""
                            cid = event.result.tool_call_id
                            start_t = tool_start.pop(cid, None)
                            duration_ms = int((time.monotonic() - start_t) * 1000) if start_t else 0
                            await emit("tool_result", {
                                "tool_name": event.result.tool_name,
                                "tool_call_id": cid,
                                "result_summary": summary,
                                "duration_ms": duration_ms,
                            })
            elif Agent.is_model_request_node(node):
                tid = f"{agent_id}_think_{thinking_counter}"
                active = False
                async with node.stream(agent_run.ctx) as model_stream:
                    async for event in model_stream:
                        if isinstance(event, PartDeltaEvent):
                            delta = event.delta
                            if isinstance(delta, TextPartDelta):
                                if active:
                                    await emit("thinking_end", {
                                        "thinking_id": tid, "summary": "", "full_length": 0,
                                    })
                                    active = False
                                    thinking_counter += 1
                                    tid = f"{agent_id}_think_{thinking_counter}"
                                await emit("text_delta", {"content": delta.content_delta})
                            elif isinstance(delta, ThinkingPartDelta):
                                active = True
                                await emit("thinking_delta", {
                                    "content": delta.content_delta or "",
                                    "thinking_id": tid,
                                })
                if active:
                    await emit("thinking_end", {
                        "thinking_id": tid, "summary": "", "full_length": 0,
                    })
                    thinking_counter += 1

            usage = agent_run.usage()
            total = usage.total_tokens or 0
            prompt_t = usage.request_tokens or 0
            completion_t = usage.response_tokens or 0
            await emit("token_update", {
                "prompt_tokens": prompt_t,
                "completion_tokens": completion_t,
                "thinking_tokens": 0,
                "cumulative": total,
                "cost_estimate": estimate_cost(prompt_t, completion_t, 0, model),
            })

    result = agent_run.result
    save_agent_trace(result, output_dir, f"NOTES_{template_type.value}")

    # Guard against silent no-op success — retryable per PLAN §4 E.1.
    if not deps.wrote_once or not deps.filled_path:
        raise _NoWriteError("Notes agent finished without writing any payloads")

    return _SingleAgentOutcome(
        filled_path=deps.filled_path,
        write_errors=list(deps.write_skip_errors),
        fuzzy_matches=list(deps.write_fuzzy_matches),
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
    return warnings


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

        path = _Path(output_dir) / f"notes_{template_type.value}_failures.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "template": template_type.value,
                    "attempts": attempts,
                },
                indent=2,
                ensure_ascii=False,
            ),
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
) -> NotesAgentResult:
    """Drive the Sheet-12 sub-agent fan-out and write the final workbook.

    Returns a NotesAgentResult shaped the same as single-agent runs so the
    coordinator result list is homogeneous and the merger doesn't care.
    """
    from notes.listofnotes_subcoordinator import run_listofnotes_subcoordinator
    from notes.writer import write_notes_workbook
    from notes_types import NOTES_REGISTRY, notes_template_path

    async def _emit(event_type: str, data: dict) -> None:
        if event_queue is None:
            return
        await event_queue.put({
            "event": event_type,
            "data": {
                **data,
                "agent_id": agent_id,
                "agent_role": NotesTemplateType.LIST_OF_NOTES.value,
            },
        })

    async def _safe_emit(event_type: str, data: dict) -> None:
        """Cancellation-safe emit — see the single-agent variant for rationale
        (peer-review #3). Used only inside ``except CancelledError``."""
        try:
            await _emit(event_type, data)
        except Exception:  # noqa: BLE001
            logger.debug("Dropped %s event during Notes-12 cancellation teardown", event_type)

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
            err = (
                "Notes-12 has no inventory to fan out. Scout's notes-inventory "
                "builder found no note headers (common on scanned PDFs where "
                "PyMuPDF cannot extract text). Nothing to extract — "
                "failing the sheet loudly instead of shipping an untouched template."
            )
            await _emit("error", {"message": err})
            await _emit("complete", {"success": False, "error": err})
            return NotesAgentResult(
                template_type=NotesTemplateType.LIST_OF_NOTES,
                status="failed",
                error=err,
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
            page_hints=page_hints,
        )

        # Total-failure guard: an empty aggregated payload list coming out
        # of a non-empty inventory means every sub-agent lost coverage.
        # Writer treats empty payloads as a no-op success, so without this
        # check the sheet would silently report succeeded with an untouched
        # xlsx. Partial coverage (some payloads present) remains a success
        # per PLAN §4 Checkpoint C.
        if not sub_result.aggregated_payloads:
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
            )

        # Final workbook write — one call with the aggregated payload list.
        # The writer handles row-concatenation (including row 112) and
        # evidence-column placement based on filing_level.
        entry = NOTES_REGISTRY[NotesTemplateType.LIST_OF_NOTES]
        template = str(notes_template_path(NotesTemplateType.LIST_OF_NOTES, level=filing_level))
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
            )

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
    return warnings
