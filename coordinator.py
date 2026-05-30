"""Python coordinator — fans out extraction to N sub-agents concurrently.

No LLM orchestration — plain Python with asyncio.gather. Each sub-agent
runs independently against its own workbook file. The coordinator collects
results and reports per-agent success/failure.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Dict, Set, List, Union

from pricing import estimate_cost

from agent_tracing import save_agent_trace, save_messages_trace
# MAX_AGENT_ITERATIONS is re-exported here (and used as the AgentLoopSpec
# default) because the iteration cap is conceptually the face loop's config,
# and tests/readers reference `coordinator.MAX_AGENT_ITERATIONS`. The loop
# itself now lives in agent_runner (rewrite Phase 2).
from agent_tracing import MAX_AGENT_ITERATIONS  # noqa: F401 (re-export)
from agent_runner import (
    AgentLoopSpec,
    IterationLimitReached,
    run_agent_loop,
)
from statement_types import (
    StatementType,
    get_variant,
    template_path as get_template_path,
    variants_for_standard,
)
from extraction.agent import create_extraction_agent

logger = logging.getLogger(__name__)

# Tool name → pipeline phase (mirrors server.py PHASE_MAP so phase events
# are emitted at the source rather than requiring post-hoc mapping).
PHASE_MAP = {
    "read_template": "reading_template",
    "view_pdf_pages": "viewing_pdf",
    "write_facts": "filling_workbook",
    "verify_totals": "verifying",
    "save_result": "complete",
}


# Per-turn LLM timeout for face agents. Matches NOTES_TURN_TIMEOUT in
# notes/coordinator.py — same failure mode (model stalls mid-stream on
# a single iteration), same threshold. 180s is well above healthy p99
# for one model-request node and catches the minutes-long stalls that
# would otherwise pin the coordinator to `running` until MAX_AGENT_ITERATIONS
# triggers (which only fires *between* iterations, not during one).
FACE_TURN_TIMEOUT: float = 180.0


def _safe_usage_backfill(agent_run, model, label: str) -> tuple[int, float]:
    """Best-effort (tokens, cost) capture from a finished-or-aborted run.

    Telemetry is advisory — a usage-read failure must never mask the real
    result. Used by the success path AND the failure/timeout/iteration-limit
    paths so a failed agent still reports the tokens it actually burned
    (run_id=126 showed 0 tokens on failed agents because only the success
    path backfilled). Returns (0, 0.0) when usage is unreachable.
    """
    try:
        u = agent_run.usage()
        prompt = int(u.request_tokens or 0)
        completion = int(u.response_tokens or 0)
        return int(u.total_tokens or 0), estimate_cost(prompt, completion, 0, model)
    except Exception:  # noqa: BLE001 — telemetry is advisory
        logger.debug("agent token backfill skipped for %s", label)
        return 0, 0.0


def _verify_is_clean(verify_result) -> bool:
    """True when the agent's last verify_totals shows no outstanding problem.

    Used to decide whether a post-write run that hit the iteration cap is
    salvageable. Requires an actual verification — a never-verified run is not
    'clean' even if a workbook exists.
    """
    if verify_result is None:
        return False
    if verify_result.is_balanced is False:
        return False
    if getattr(verify_result, "mandatory_unfilled", None):
        return False
    if getattr(verify_result, "mismatches", None):
        return False
    return True


def _build_event(event_type: str, agent_id: str, agent_role: str, data: dict) -> dict:
    """Construct an SSE-shaped event dict with agent identification."""
    return {
        "event": event_type,
        "data": {**data, "agent_id": agent_id, "agent_role": agent_role},
    }


def build_face_page_hints(ref) -> dict:
    """Build the per-agent ``page_hints`` dict from a scout StatementPageRef.

    Phase 1a adds the structural face-line refs and the `face_read_in_detail`
    flag — consumed by `prompts/__init__._build_scoped_navigation` to render a
    "skip-to-note" index for face extraction agents. Empty list / False fall
    through unchanged (today's behaviour preserved when scout couldn't enrich
    the infopack). Extracted to module scope (peer-review F6) so the wiring
    test exercises the SAME construction the coordinator runs, instead of a
    drifting replica.
    """
    return {
        "face_page": ref.face_page,
        "note_pages": ref.note_pages,
        "face_line_refs": [
            {"label": r.label, "note_num": r.note_num, "section": r.section}
            for r in ref.face_line_refs
        ],
        "face_read_in_detail": ref.face_read_in_detail,
    }


@dataclass
class RunConfig:
    """Configuration for a multi-statement extraction run."""
    pdf_path: str
    output_dir: str
    # Accepts str (PydanticAI resolves it) or a provider-backed Model object
    # (from server._create_proxy_model for enterprise proxy support).
    model: Any = "openai.gpt-5.4"
    statements_to_run: Set[StatementType] = field(default_factory=lambda: set(StatementType))
    variants: Dict[StatementType, str] = field(default_factory=dict)
    # Per-agent model overrides — same typing as model (str or Model object)
    models: Dict[StatementType, Any] = field(default_factory=dict)
    scout_enabled: bool = True
    filing_level: str = "company"
    # Filing standard axis, orthogonal to filing_level. Defaults to "mfrs"
    # so every pre-existing caller, CLI one-liner, and fixture keeps
    # routing through the MFRS template tree unchanged. Set to "mpers"
    # to route through XBRL-template-MPERS/ and enable the SoRE variant.
    filing_standard: str = "mfrs"
    # Canonical mode (Phase B): when both are set, extraction agents project
    # their cell writes into run_concept_facts for this run via the facts
    # API. Both None in legacy mode. db_path is the audit/canonical SQLite DB.
    run_id: Optional[int] = None
    db_path: Optional[str] = None


@dataclass
class AgentResult:
    """Outcome of a single extraction sub-agent."""
    statement_type: StatementType
    variant: str
    # "succeeded" / "failed" / "cancelled" / "skipped". "skipped" is for
    # variants with no template (NotPrepared) — distinct from "failed" so
    # the merge / UI can tell "nothing to do" apart from "tried and broke".
    status: str
    workbook_path: Optional[str] = None
    error: Optional[str] = None
    # Honest-completion flag (2026-05-29): set when the agent finalised the
    # statement via acknowledge_unresolved — the workbook is saved and the
    # data preserved, but a known verify gap (imbalance / unfilled mandatory)
    # was accepted and is flagged for human review. status stays "succeeded"
    # (the extraction DID finalise); this string carries the reason.
    flag: Optional[str] = None
    # End-of-run usage from `agent_run.usage()`. This is the aggregate the
    # coordinator captures on every exit path so server.py can persist into
    # run_agents.total_tokens / total_cost.
    total_tokens: int = 0
    total_cost: float = 0.0
    # v8 per-turn telemetry (docs/PLAN-run-page-and-telemetry.md). `turns` is
    # a list of metric dicts (one per agent.iter() node) keyed to the
    # run_agent_turns columns; the split + counts are run-level rollups
    # derived from those turns. Per-turn token figures are deltas of the
    # cumulative usage PydanticAI exposes after each node — exact for timing
    # and tool activity, best-effort for the prompt/completion split.
    turns: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    turn_count: int = 0
    tool_call_count: int = 0


@dataclass
class CoordinatorResult:
    """Aggregated results from all sub-agents."""
    agent_results: List[AgentResult] = field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        # `skipped` (NotPrepared variants — no template to fill) is a
        # legitimate non-outcome, NOT a failure: a run that asks for SOCI
        # NotPrepared and gets everything else done is fully successful.
        # Only a real `failed`/`cancelled` agent makes a run unsuccessful.
        return all(
            r.status in ("succeeded", "skipped") for r in self.agent_results
        )

    @property
    def skipped(self) -> List[AgentResult]:
        """NotPrepared (no-template) results, surfaced separately so the
        run-complete summary can report them distinctly from failures."""
        return [r for r in self.agent_results if r.status == "skipped"]

    @property
    def workbook_paths(self) -> Dict[StatementType, str]:
        return {
            r.statement_type: r.workbook_path
            for r in self.agent_results
            if r.workbook_path
        }


async def run_extraction(
    config: RunConfig,
    infopack=None,
    event_queue: Optional[asyncio.Queue] = None,
    session_id: Optional[str] = None,
    push_sentinel: bool = True,
) -> CoordinatorResult:
    """Run extraction sub-agents concurrently for all selected statements.

    Args:
        config: Run configuration with PDF path, variants, models, etc.
        infopack: Optional scout infopack with page hints per statement.
                  When None, sub-agents get full PDF access.
        session_id: Session identifier — used by task_registry so individual
                    agents can be cancelled from the abort API.
        push_sentinel: When True (default) a None sentinel is pushed onto
                    `event_queue` in the finally block so the SSE drain
                    loop can exit. When multiplexing with another
                    coordinator (notes), the outer orchestrator pushes the
                    single shared sentinel and passes False here.

    Returns:
        CoordinatorResult with per-agent outcomes.
    """
    import task_registry

    # Maps agent_id -> (asyncio.Task, StatementType, variant) so we can
    # collect results even if individual tasks are cancelled.
    task_map: dict[str, tuple[asyncio.Task, StatementType, str]] = {}

    # NotPrepared variants produce no template — record them as explicit
    # `skipped` results so merge / history / UI can distinguish "nothing
    # to do" from "tried and failed". Silent `continue` (the pre-fix
    # behaviour) left these statements invisible downstream.
    skipped_results: list[AgentResult] = []

    # Sort by canonical enum order (SOFP → SOPL → SOCI → SOCF → SOCIE)
    # so agent_ids and tab order are stable across runs.
    ordered_statements = sorted(config.statements_to_run, key=lambda s: list(StatementType).index(s))
    for idx, stmt_type in enumerate(ordered_statements):
        # Variant resolution: explicit config > scout suggestion > registry default.
        # The scout suggestion is ADVISORY and gets filtered through
        # applies_to_standard — a scout that reads an SoRE-shaped SOCIE on
        # an MFRS run silently falls back to Default rather than crashing
        # the coordinator at template_path() time (peer-review HIGH).
        # Explicit config is NOT filtered here because the server's
        # pre-launch guard already rejected any mismatch; CLI / direct
        # callers that pass a bad explicit variant deserve the clear
        # ValueError from template_path() rather than a silent override.

        dropped_suggestion: Optional[str] = None
        variant = config.variants.get(stmt_type)
        if not variant and infopack is not None and stmt_type in infopack.statements:
            suggested = infopack.statements[stmt_type].variant_suggestion or None
            if suggested:
                try:
                    sv = get_variant(stmt_type, suggested)
                except KeyError:
                    suggested = None
                else:
                    if config.filing_standard not in sv.applies_to_standard:
                        logger.info(
                            "Dropping scout suggestion %s/%s — not applicable "
                            "to standard %s; falling back to registry default",
                            stmt_type.value, suggested, config.filing_standard,
                        )
                        # Capture so a status event can be emitted under the
                        # real agent_id once it's computed below (peer-review
                        # I1: logger.info is invisible to the UI).
                        dropped_suggestion = suggested
                        suggested = None
            variant = suggested
        if not variant:
            applicable = variants_for_standard(stmt_type, config.filing_standard)
            detectable = [v for v in applicable if v.detection_signals]
            variant = (detectable[0].name if detectable else applicable[0].name)

        # Skip NotPrepared variants — no template to fill. Capture as
        # an explicit `skipped` result rather than silently dropping it.
        if variant == "NotPrepared":
            logger.info("Skipping %s — variant is NotPrepared (no template)", stmt_type.value)
            skipped_results.append(AgentResult(
                statement_type=stmt_type,
                variant=variant,
                status="skipped",
                error=None,
            ))
            continue

        model = config.models.get(stmt_type, config.model)

        # Build page hints from infopack if available. Phase 1a adds the
        # structural face-line refs and the face_read_in_detail flag —
        # consumed by prompts/__init__._build_scoped_navigation to render
        # a "skip-to-note" index for face extraction agents. Empty list
        # / False fall through unchanged (today's behaviour preserved
        # when scout couldn't enrich the infopack).
        page_hints = None
        scout_context = None
        if infopack is not None and stmt_type in infopack.statements:
            ref = infopack.statements[stmt_type]
            page_hints = build_face_page_hints(ref)
            # Phase 2 — top-level Infopack context shared by every
            # face statement. The renderer reads None / "unknown" as
            # "scout did not observe" and either omits the line or
            # surfaces a loud verification warning (scale_unit case).
            scout_context = {
                "entity_name": infopack.entity_name,
                "reporting_period_cy": infopack.reporting_period_cy,
                "reporting_period_py": infopack.reporting_period_py,
                "currency": infopack.currency,
                "scale_unit": infopack.scale_unit,
                "consolidation_level": infopack.consolidation_level,
            }

        # Resolve template path for this variant against the requested
        # standard so MPERS runs land on XBRL-template-MPERS/ and SoRE on
        # an MFRS run is rejected at the registry layer rather than silently
        # resolving to a bogus MFRS file.
        tpl_path = str(get_template_path(
            stmt_type,
            variant,
            level=config.filing_level,
            standard=config.filing_standard,
        ))

        # agent_id is the lowercase statement name (e.g. "sofp", "sopl").
        # This is stable across reruns — a single-statement rerun produces
        # the same ID as the original multi-statement run.
        agent_id = stmt_type.value.lower()

        # Surface dropped scout suggestions to the UI so the operator sees
        # why the agent is running Default instead of the scout's suggested
        # variant (peer-review I1). Fire-and-forget — if there's no queue
        # (CLI path) the logger.info above is the only record.
        if dropped_suggestion is not None and event_queue is not None:
            await event_queue.put(_build_event(
                "status",
                agent_id,
                stmt_type.value,
                {
                    "phase": "starting",
                    "message": (
                        f"Scout suggested {stmt_type.value}/{dropped_suggestion} "
                        f"but that variant isn't available on "
                        f"{config.filing_standard.upper()} — "
                        f"running {variant} instead."
                    ),
                },
            ))

        # Create individual tasks so they can be cancelled independently
        task = asyncio.create_task(
            _run_single_agent(
                statement_type=stmt_type,
                variant=variant,
                pdf_path=config.pdf_path,
                template_path=tpl_path,
                model=model,
                output_dir=config.output_dir,
                page_hints=page_hints,
                scout_context=scout_context,
                event_queue=event_queue,
                agent_id=agent_id,
                filing_level=config.filing_level,
                filing_standard=config.filing_standard,
                run_id=getattr(config, "run_id", None),
                db_path=getattr(config, "db_path", None),
            ),
            name=agent_id,
        )
        task_map[agent_id] = (task, stmt_type, variant)

        # Register in global registry so the abort API can find it
        if session_id:
            task_registry.register(session_id, agent_id, task)

    # Wait for all agents to finish (including any that get cancelled).
    # asyncio.wait handles cancelled tasks gracefully — they appear in `done`.
    try:
        if task_map:
            done, _ = await asyncio.wait(
                [t for t, _, _ in task_map.values()],
                return_when=asyncio.ALL_COMPLETED,
            )

        # Collect results from each task
        results: list[AgentResult] = []
        for agent_id, (task, stmt_type, variant) in task_map.items():
            try:
                results.append(task.result())
            except asyncio.CancelledError:
                results.append(AgentResult(
                    statement_type=stmt_type,
                    variant=variant,
                    status="cancelled",
                    error="Cancelled by user",
                ))
            except Exception as e:
                results.append(AgentResult(
                    statement_type=stmt_type,
                    variant=variant,
                    status="failed",
                    error=str(e),
                ))
    except asyncio.CancelledError:
        # Coordinator itself was cancelled (e.g. client disconnect).
        # Cancel all child agent tasks so they don't keep running orphaned.
        for task, _, _ in task_map.values():
            if not task.done():
                task.cancel()
        # Wait briefly for cancellations to propagate
        if task_map:
            await asyncio.wait(
                [t for t, _, _ in task_map.values()],
                timeout=5.0,
            )
        results = []
        raise  # Re-raise so the caller's CancelledError handler runs
    finally:
        # Push sentinel unless the caller is multiplexing us with another
        # coordinator (in which case they push the single shared sentinel
        # after all parallel runs complete).
        if push_sentinel and event_queue is not None:
            await event_queue.put(None)
        # NOTE: task_registry cleanup used to live here
        # (remove_session(session_id)), but that erased notes-agent task
        # references mid-flight whenever face finished before notes. The
        # outer orchestrator now owns session cleanup — see
        # server.run_multi_agent_stream's finally block. When this
        # coordinator runs standalone (push_sentinel=True, no notes
        # multiplex), the caller is still responsible for calling
        # task_registry.remove_session.

    # Prepend skipped (NotPrepared) results so they survive the
    # CancelledError reset above and land in the CoordinatorResult.
    return CoordinatorResult(agent_results=skipped_results + results)


async def _run_single_agent(
    statement_type: StatementType,
    variant: str,
    pdf_path: str,
    template_path: str,
    model: Any,
    output_dir: str,
    page_hints: Optional[Dict] = None,
    scout_context: Optional[Dict] = None,
    event_queue: Optional[asyncio.Queue] = None,
    agent_id: str = "",
    filing_level: str = "company",
    filing_standard: str = "mfrs",
    run_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> AgentResult:
    """Run a single extraction agent, streaming events into event_queue if provided."""
    agent_role = statement_type.value
    # Canonical mode: derive the concept template_id from the template path
    # (deterministic — matches the importer's id) so fact writes link to the
    # right tree. None in legacy mode.
    template_id = None
    if run_id is not None and db_path:
        from concept_model.parser import _derive_template_id
        template_id = _derive_template_id(Path(template_path))

    async def _emit(event_type: str, data: dict) -> None:
        """Push an event into the queue when streaming is active."""
        if event_queue is not None:
            await event_queue.put(_build_event(event_type, agent_id, agent_role, data))

    # v8 telemetry: per-turn metrics captured during the agent.iter() loop
    # below. Attached to every AgentResult via `_finalize`, so the success
    # path AND the salvage/failure paths report whatever turns actually ran.
    # Each dict mirrors the run_agent_turns columns plus `_n_tool_calls`
    # (used only for the run-level rollup, ignored by the DB writer).
    _turn_records: list[dict] = []

    def _finalize(result: AgentResult) -> AgentResult:
        """Attach captured per-turn metrics + rollups to an AgentResult.

        Best-effort: a telemetry-shaping bug must never change the run
        outcome, so any failure here is swallowed and the result is
        returned with whatever was already set."""
        try:
            result.turns = list(_turn_records)
            result.turn_count = len(_turn_records)
            result.tool_call_count = sum(
                int(t.get("_n_tool_calls") or 0) for t in _turn_records
            )
            # Run-level split = sum of the per-turn deltas, which is always
            # internally consistent with the rows we persist.
            result.prompt_tokens = sum(
                int(t.get("prompt_tokens") or 0) for t in _turn_records
            )
            result.completion_tokens = sum(
                int(t.get("completion_tokens") or 0) for t in _turn_records
            )
        except Exception:  # noqa: BLE001 — telemetry is advisory
            logger.debug("turn telemetry finalize skipped for %s", agent_role)
        return result

    def _save_trace_best_effort(run_obj) -> None:
        """Persist the conversation trace from a finished OR partial run.

        On the failure paths `agent_run.result` is None (the run never
        produced a final result), so fall back to the messages accumulated
        on the run's graph state. This keeps failed agents debuggable — the
        trace viewer's most valuable case (peer-review [1]). Fully guarded:
        a trace-save hiccup must never change the agent's outcome."""
        if run_obj is None:
            return
        try:
            res = getattr(run_obj, "result", None)
            if res is not None and hasattr(res, "all_messages"):
                save_agent_trace(res, output_dir, statement_type.value, turns=_turn_records)
                return
            # Partial run — pull whatever messages were exchanged so far.
            msgs = None
            try:
                msgs = run_obj.ctx.state.message_history
            except Exception:  # noqa: BLE001 — defensive: internal shape
                msgs = None
            if msgs:
                save_messages_trace(
                    msgs, output_dir, statement_type.value, turns=_turn_records
                )
        except Exception:  # noqa: BLE001 — telemetry is advisory
            logger.debug("best-effort trace save skipped for %s", agent_role)

    try:
        agent, deps = create_extraction_agent(
            statement_type=statement_type,
            variant=variant,
            pdf_path=pdf_path,
            template_path=template_path,
            model=model,
            output_dir=output_dir,
            page_hints=page_hints,
            scout_context=scout_context,
            filing_level=filing_level,
            filing_standard=filing_standard,
            run_id=run_id,
            db_path=db_path,
            template_id=template_id,
        )

        prompt = (
            f"Extract the {statement_type.value} ({variant}) from the PDF "
            f"and fill the template. Follow the strategy in your system prompt."
        )

        await _emit("status", {"phase": "started", "message": f"Starting {agent_role} extraction..."})

        # Stream the agent.iter() run through the shared runner (rewrite
        # Phase 2): per-turn timeout, iteration cap, tool/model event
        # streaming, token_update, and v8 per-turn telemetry all live in
        # agent_runner.run_agent_loop now. The verify/save gate, trace save,
        # and outcome below stay here — they are face-specific.
        loop_spec = AgentLoopSpec(
            agent_role=agent_role,
            model=model,
            turn_timeout=FACE_TURN_TIMEOUT,
            phase_map=PHASE_MAP,
            phase_message=lambda role, phase: (
                f"{role}: {phase.replace('_', ' ').title()}"
            ),
            set_turn_counter=True,
        )
        async with agent.iter(prompt, deps=deps) as agent_run:
            await run_agent_loop(
                agent_run, deps, loop_spec, _emit, _turn_records,
            )

        # Get the final result — same RunResult as agent.run() returned
        result = agent_run.result

        # Save per-statement conversation trace for debugging/audit. Pass the
        # captured per-turn metrics so the trace lines up token deltas + timing
        # with the verbatim request/response content (v8). Routed through the
        # best-effort helper so the success and failure paths share one writer.
        _save_trace_best_effort(agent_run)

        # Capture end-of-run usage so the run_agents row can be backfilled
        # with real token / cost numbers (gotcha #6 — per-turn zeros are
        # internal). best-effort: if usage is unavailable for any reason,
        # we still return success with zeros rather than failing the run.
        final_tokens, final_cost = _safe_usage_backfill(
            agent_run, model, statement_type.value
        )

        # Peer-review (2026-05-21): coordinator used to return
        # status="succeeded" even when the agent finished without ever
        # calling write_facts (deps.filled_path empty). The save gate
        # in extraction/agent.py blocks save_result without a passing
        # verify, but it can't force the agent to *enter* the gate.
        # A conversational-only end-of-turn would still mark the run
        # green. Require a workbook artifact on the success path.
        if not deps.filled_path:
            err_msg = (
                f"{statement_type.value}: agent finished without writing a "
                "workbook (no write_facts tool call landed)."
            )
            logger.warning(err_msg)
            await _emit("error", {"message": err_msg})
            await _emit("complete", {"success": False, "error": err_msg})
            return _finalize(AgentResult(
                statement_type=statement_type,
                variant=variant,
                status="failed",
                error=err_msg,
                total_tokens=final_tokens,
                total_cost=final_cost,
            ))

        # Peer-review (Edge AFS, 2026-05-28): a workbook on disk is not
        # the same as a completed extraction. If every save_result attempt
        # was refused by the gate (mandatory rows still blank, unbalanced
        # totals) the agent may write a workbook and then end with prose.
        # The merger will still consume the workbook (it reads from disk
        # by filename), but the run_agents row must reflect "workbook
        # written but extraction not finalised" so the run lands as
        # completed_with_errors and the trace surfaces last_save_error.
        if not deps.result_saved:
            err_msg = (
                f"{statement_type.value}: workbook written but save_result "
                "never succeeded — extraction did not finalise."
            )
            if deps.last_save_error:
                err_msg += f" Last refusal: {deps.last_save_error}"
            if deps.last_fill_errors:
                err_msg += (
                    f" Unresolved fill errors: "
                    f"{'; '.join(deps.last_fill_errors)}"
                )
            logger.warning(err_msg)
            await _emit("error", {"message": err_msg, "type": "save_result_not_called"})
            await _emit("complete", {
                "success": False,
                "error": err_msg,
                "workbook_path": deps.filled_path,
            })
            return _finalize(AgentResult(
                statement_type=statement_type,
                variant=variant,
                status="failed",
                workbook_path=deps.filled_path,
                error=err_msg,
                total_tokens=final_tokens,
                total_cost=final_cost,
            ))

        flag = deps.unresolved_summary if deps.completed_with_flag else None
        if flag:
            logger.warning(
                "%s/%s: finalised WITH FLAG — %s",
                statement_type.value, variant, flag,
            )
        await _emit("complete", {
            "success": True,
            "workbook_path": deps.filled_path,
            "flag": flag,
        })

        return _finalize(AgentResult(
            statement_type=statement_type,
            variant=variant,
            status="succeeded",
            workbook_path=deps.filled_path,
            flag=flag,
            total_tokens=final_tokens,
            total_cost=final_cost,
        ))

    except asyncio.TimeoutError:
        # _iter_with_turn_timeout fired: the LLM stalled on a single
        # node iteration past FACE_TURN_TIMEOUT. Mirror the notes
        # coordinator's policy — if a workbook already landed on disk
        # the result is salvageable; otherwise it's a hard failure.
        _tokens, _cost = _safe_usage_backfill(agent_run, model, statement_type.value)
        _save_trace_best_effort(agent_run)
        if deps.filled_path:
            logger.warning(
                "%s/%s: LLM stalled past %ss after write — treating as done "
                "(workbook at %s).",
                statement_type.value, variant, FACE_TURN_TIMEOUT,
                deps.filled_path,
            )
            await _emit("complete", {
                "success": True,
                "workbook_path": deps.filled_path,
                "stalled_after_write": True,
            })
            return _finalize(AgentResult(
                statement_type=statement_type,
                variant=variant,
                status="succeeded",
                workbook_path=deps.filled_path,
                total_tokens=_tokens,
                total_cost=_cost,
            ))
        err_msg = (
            f"{statement_type.value}: LLM stalled past {FACE_TURN_TIMEOUT}s "
            "without writing a workbook."
        )
        logger.warning(err_msg)
        await _emit("error", {"message": err_msg, "type": "turn_timeout"})
        await _emit("complete", {"success": False, "error": err_msg})
        return _finalize(AgentResult(
            statement_type=statement_type,
            variant=variant,
            status="failed",
            error=err_msg,
            total_tokens=_tokens,
            total_cost=_cost,
        ))

    except IterationLimitReached as e:
        # The agent burned its whole iteration budget. If a workbook already
        # landed AND the last verify was clean, the work is done — salvage it
        # as succeeded (parity with the TimeoutError path) rather than throwing
        # away a balanced statement (run_id=126 SOPL). Otherwise hard-fail, but
        # still surface the structured "Hit iteration limit" message.
        _tokens, _cost = _safe_usage_backfill(agent_run, model, statement_type.value)
        _save_trace_best_effort(agent_run)
        if deps.filled_path and _verify_is_clean(deps.last_verify_result):
            logger.warning(
                "%s/%s: hit iteration cap after a clean write — salvaging "
                "(workbook at %s).",
                statement_type.value, variant, deps.filled_path,
            )
            await _emit("complete", {
                "success": True,
                "workbook_path": deps.filled_path,
                "iteration_capped_after_write": True,
            })
            return _finalize(AgentResult(
                statement_type=statement_type,
                variant=variant,
                status="succeeded",
                workbook_path=deps.filled_path,
                total_tokens=_tokens,
                total_cost=_cost,
            ))
        logger.warning("Agent %s/%s hit iteration limit", statement_type.value, variant)
        await _emit("error", {"message": str(e)})
        await _emit("complete", {"success": False, "error": str(e)})
        return _finalize(AgentResult(
            statement_type=statement_type,
            variant=variant,
            status="failed",
            error=str(e),
            total_tokens=_tokens,
            total_cost=_cost,
        ))

    except asyncio.CancelledError:
        # Per-agent cancellation from the abort API. CancelledError is a
        # BaseException in Python 3.9+, so it must be caught separately.
        logger.info("Agent %s/%s cancelled by user", statement_type.value, variant)
        _save_trace_best_effort(locals().get("agent_run"))
        await _emit("complete", {"success": False, "error": "Cancelled by user"})
        return _finalize(AgentResult(
            statement_type=statement_type,
            variant=variant,
            status="cancelled",
            error="Cancelled by user",
        ))

    except Exception as e:
        logger.exception("Agent %s/%s failed", statement_type.value, variant,
                         extra={"statement_type": statement_type.value, "variant": variant})
        # Backfill tokens if the run got far enough to bind agent_run (the
        # failure may have happened before agent.iter() opened, e.g. in
        # create_extraction_agent — guard accordingly).
        _run = locals().get("agent_run")
        _tokens, _cost = (
            _safe_usage_backfill(_run, model, statement_type.value)
            if _run is not None else (0, 0.0)
        )
        _save_trace_best_effort(_run)
        await _emit("error", {"message": str(e)})
        await _emit("complete", {"success": False, "error": str(e)})
        return _finalize(AgentResult(
            statement_type=statement_type,
            variant=variant,
            status="failed",
            error=str(e),
            total_tokens=_tokens,
            total_cost=_cost,
        ))
