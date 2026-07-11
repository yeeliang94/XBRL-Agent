"""Python coordinator — fans out extraction to N sub-agents concurrently.

No LLM orchestration — plain Python with asyncio.gather. Each sub-agent
runs independently against its own workbook file. The coordinator collects
results and reports per-agent success/failure.
"""

from __future__ import annotations

import asyncio
import logging
import os
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
    RetryPolicy,
    TokenBudgetExceeded,
    WallclockExceeded,
    build_agent_event,
    make_emitter,
    resolve_token_budget,
    run_agent_loop,
    run_agent_with_retries,
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

# Item 18: grace period the Stop-All cancel path waits for children to
# acknowledge cancellation before declaring any survivor a possible leak.
# Module-level so the wedged-task pinning test can shrink it.
CANCEL_GRACE_PERIOD_S: float = 5.0


def _resolve_face_wallclock() -> float:
    """XBRL_FACE_WALLCLOCK_S: positive seconds; 0/negative disables.

    Item 6: the face loop bounds each turn (180s) and the turn count (40)
    but 40 slow-but-compliant turns was legally ~2 hours per agent. Same
    resolver semantics as XBRL_CORRECTION_WALLCLOCK_S (server.py).
    """
    raw = os.environ.get("XBRL_FACE_WALLCLOCK_S", "")
    if not raw:
        return 1800.0
    try:
        v = float(raw)
        return v if v > 0 else float("inf")
    except ValueError:
        return 1800.0


FACE_WALLCLOCK_TIMEOUT: float = _resolve_face_wallclock()


def _is_transient_error(e: BaseException) -> bool:
    """Shared transient-error predicate for the face retry path (item 10).

    True for provider 429s and connection-class errors — the only classes
    ``_run_single_agent_attempt`` re-raises and the retry wrapper in
    ``_run_single_agent`` retries. One definition for both sites (code-review
    fix, 2026-06-13) so the classifications can't drift apart.
    """
    import httpx
    from notes._rate_limit import is_rate_limit_error

    return bool(
        is_rate_limit_error(e)
        or isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout))
    )


def _safe_usage_backfill(agent_run, model, label: str) -> tuple[int, float]:
    """Best-effort (tokens, cost) capture from a finished-or-aborted run.

    Telemetry is advisory — a usage-read failure must never mask the real
    result. Used by the success path AND the failure/timeout/iteration-limit
    paths so a failed agent still reports the tokens it actually burned
    (run_id=126 showed 0 tokens on failed agents because only the success
    path backfilled). Returns (0, 0.0) when usage is unreachable.
    """
    try:
        u = agent_run.usage
        prompt = int(u.input_tokens or 0)
        completion = int(u.output_tokens or 0)
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
    # Presentation denomination the user declares for the source statements
    # ("units" | "thousands" | "millions"). Threaded to the agents so they treat
    # the scale as authoritative instead of guessing it from the PDF header.
    # Default "thousands" (RM '000) keeps every pre-existing caller unchanged.
    denomination: str = "thousands"
    # Canonical mode (Phase B): when both are set, extraction agents project
    # their cell writes into run_concept_facts for this run via the facts
    # API. Both None in legacy mode. db_path is the audit/canonical SQLite DB.
    run_id: Optional[int] = None
    db_path: Optional[str] = None
    # Gold-standard eval (v16): the benchmark this run is graded against, or
    # None. The coordinator does not use it (grading runs in the server at
    # run completion); it rides along so the server's grading hook can read it
    # off the resolved config.
    benchmark_id: Optional[int] = None
    # Item 28 — per-entity advisory memory. When the server matched this run's
    # entity to a prior completed run, it sets an entity_memory.PriorYearAdvisory
    # here. The coordinator renders its per-statement payload into the prompt
    # (advisory only — see entity_memory.py). None when no match / disabled.
    prior_year_advisory: Any = None


# Item 9 (PLAN-orchestration-hardening): structured failure taxonomy for
# run_agents.error_type (schema v17). One value per failure CLASS so
# post-mortems and the Telemetry tab can group without string-grepping the
# free-text error. No CHECK constraint in the DB on purpose — adding a new
# value must not require a migration (same rationale as runs.status).
ERROR_TYPE_TURN_TIMEOUT = "turn_timeout"
ERROR_TYPE_ITERATION_CAPPED = "iteration_capped"
ERROR_TYPE_WALLCLOCK = "wallclock"
ERROR_TYPE_TOKEN_BUDGET = "token_budget_exceeded"
ERROR_TYPE_PROJECTION_FAILED = "projection_failed"
ERROR_TYPE_SAVE_GATE_REFUSED = "save_gate_refused"
ERROR_TYPE_TOOL_EXCEPTION = "tool_exception"
ERROR_TYPE_CANCELLED = "cancelled"
ERROR_TYPE_NO_WRITE = "no_write"
# A transient error (429 / connection-class) that exhausted its retry budget —
# distinct from tool_exception so post-mortems can tell "flaky provider, out of
# retries" apart from "a tool threw".
ERROR_TYPE_TRANSIENT_EXHAUSTED = "transient_exhausted"


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
    # Item 9: machine-readable failure class (one of the ERROR_TYPE_*
    # constants above). None on success; persisted to run_agents.error_type
    # (schema v17) so History can group failures without string-grepping.
    error_type: Optional[str] = None
    # Honest-completion flag (2026-05-29): set when the agent finalised the
    # statement via acknowledge_unresolved — the workbook is saved and the
    # data preserved, but a known verify gap (imbalance / unfilled mandatory)
    # was accepted and is flagged for human review. status stays "succeeded"
    # (the extraction DID finalise); this string carries the reason.
    flag: Optional[str] = None
    # End-of-run usage from `agent_run.usage`. This is the aggregate the
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
    # v15 cache telemetry (§6 rec 1): cumulative cache-read / cache-write
    # tokens for this agent, summed from the per-turn deltas. cache_read > 0
    # is the proof that prompt caching is hitting; cache_write is tracked so
    # cost accounting sees the (Anthropic-priced) write side too.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Item 23: advisory warnings — currently the face-coverage receipts
    # (scout-observed face lines the agent left unaccounted). Surfaced on the
    # live `complete` SSE event (the UI records it) AND attached here on the
    # returned result. Never affects status; an empty list is the norm.
    warnings: list = field(default_factory=list)


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

    # Scale-unit reconciliation (Plan 1) — run ONCE before the per-statement
    # loop because infopack.scale_unit + the prior-year advisory are the same
    # across every statement. A confidently-wrong unit silently 1000x's every
    # value, so we cross-check scout against the matched prior-year run (and the
    # declared denomination) before any agent sees it. On an authoritative
    # conflict the value is coerced to "unknown", re-arming the loud VERIFY
    # prompt. Advisory-only otherwise; see scout/scale_reconcile.py.
    resolved_scale_unit: Optional[str] = None
    if infopack is not None:
        from scout.scale_reconcile import reconcile_scale_unit

        prior_adv = getattr(config, "prior_year_advisory", None)
        prior_scale = getattr(prior_adv, "scale_unit", None) if prior_adv else None
        reconciled = reconcile_scale_unit(
            infopack.scale_unit,
            prior_scale,
            getattr(config, "denomination", None),
        )
        resolved_scale_unit = reconciled.resolved_unit
        if reconciled.conflict_note is not None:
            logger.warning(
                "scale_unit conflict (%s): %s",
                reconciled.severity,
                reconciled.conflict_note,
            )
            if event_queue is not None:
                # Deliberately NOT via build_agent_event: this is a run-level
                # scout warning, not a per-agent event. build_agent_event
                # injects agent_id/agent_role, and the frontend creates a tab for ANY
                # event carrying agent identity — a "scout" tab would appear
                # next to the statement tabs (Codex review P2). Emitting a bare
                # event routes it to the run-level warnings banner instead.
                await event_queue.put({
                    "event": "scale_conflict",
                    "data": {
                        "severity": reconciled.severity,
                        "scout_scale_unit": infopack.scale_unit,
                        "resolved_scale_unit": resolved_scale_unit,
                        "message": reconciled.conflict_note,
                    },
                })

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
                # Plan 1: the reconciled unit (coerced to "unknown" on an
                # authoritative prior-year conflict), not scout's raw claim.
                "scale_unit": resolved_scale_unit
                if resolved_scale_unit is not None
                else infopack.scale_unit,
                "consolidation_level": infopack.consolidation_level,
            }

        # Item 28 — attach the matched prior-year advisory (if any) under a
        # namespaced key so the prompt renderer can surface it without any new
        # threading. Kept advisory-only; see entity_memory.py + render_prompt.
        # getattr keeps test/CLI configs that predate the field working.
        prior_advisory = getattr(config, "prior_year_advisory", None)
        if prior_advisory is not None:
            if scout_context is None:
                scout_context = {}
            scout_context["_prior_year"] = prior_advisory.to_prompt_dict(
                stmt_type.value
            )

        # Citation hygiene: thread the scout-measured printed-folio↔PDF-page
        # offset so the face prompt tells the agent to cite PDF page indices
        # (notes agents already get this). Rides inside scout_context like
        # _prior_year, so no signature changes down the agent-creation chain.
        page_offset = getattr(infopack, "page_offset", 0) if infopack is not None else 0
        if page_offset:
            if scout_context is None:
                scout_context = {}
            scout_context["page_offset"] = page_offset

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
            await event_queue.put(build_agent_event(
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
                denomination=getattr(config, "denomination", "thousands"),
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
            _, still_pending = await asyncio.wait(
                [t for t, _, _ in task_map.values()],
                timeout=CANCEL_GRACE_PERIOD_S,
            )
            # Item 18 (PLAN-orchestration-hardening): a task wedged in an
            # uninterruptible call survives the grace period and leaks
            # silently. We can't force-kill it, but we CAN make it visible.
            # Logging only — no new awaits that can raise (gotcha #10: the
            # cancel handler must never double-fault).
            for t in still_pending:
                logger.warning(
                    "cancellation timeout: task %s (session %s) still "
                    "pending after %.0fs — possible leak",
                    t.get_name(), session_id or "<none>",
                    CANCEL_GRACE_PERIOD_S,
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
    denomination: str = "thousands",
    run_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> AgentResult:
    """Run one extraction agent with transient-error retry (item 10).

    Each attempt is whole (fresh agent + deps — never resume a half-run
    conversation, matching the notes loop). Only errors classified
    transient retry: provider 429s consume the shared rate-limit budget
    with honoured retry-after backoff; connection-class errors get exactly
    one retry. Generic exceptions keep the fail-fast behaviour — face
    retries re-bill a large PDF context, so there is no blanket budget.
    The DB fact projection is idempotent per write-batch (gotcha #21
    Phase B), so a retried attempt re-projecting is safe.
    """
    import httpx
    from notes._rate_limit import (
        RATE_LIMIT_MAX_RETRIES,
        compute_backoff_delay,
        is_rate_limit_error,
    )

    agent_role = statement_type.value

    # Face contract: safe_emit swallows CancelledError only (awaiting
    # queue.put inside an active cancellation can itself be cancelled — the
    # notes loop's peer-review #3 — and that must not trap the structured
    # terminal return). A genuine Exception still surfaces.
    _emit, _safe_emit = make_emitter(event_queue, agent_id, agent_role)

    # Code-review fix (2026-06-13): tokens/cost burned by FAILED transient
    # attempts. The attempt annotates its usage onto the re-raised exception
    # (``_xbrl_attempt_tokens`` / ``_xbrl_attempt_cost``); accumulated here
    # and added to the final AgentResult on every exit path, so retried
    # statements don't under-report provider spend.
    failed_attempt_tokens = 0
    failed_attempt_cost = 0.0

    def _with_prior_attempt_usage(result: AgentResult) -> AgentResult:
        if failed_attempt_tokens or failed_attempt_cost:
            result.total_tokens = int(result.total_tokens or 0) + failed_attempt_tokens
            result.total_cost = float(result.total_cost or 0.0) + failed_attempt_cost
        return result

    def _accumulate_failed_attempt_usage(e: BaseException) -> None:
        nonlocal failed_attempt_tokens, failed_attempt_cost
        failed_attempt_tokens += int(getattr(e, "_xbrl_attempt_tokens", 0) or 0)
        failed_attempt_cost += float(getattr(e, "_xbrl_attempt_cost", 0.0) or 0.0)

    def _clear_failed_attempt_facts() -> None:
        """Retry hygiene (peer-review HIGH, 2026-06-12): ``write_facts``
        projections are UPSERTS — a fact only the failed attempt wrote
        would silently survive into the fresh attempt's export (the
        download renders from the DB). Clear this statement's template-
        scoped facts so the retried attempt is authoritative. Raises on
        failure: shipping stale facts silently is worse than failing the
        statement (same philosophy as the projection_failed gate)."""
        if run_id is not None and db_path:
            from concept_model.parser import _derive_template_id
            from concept_model.facts_api import clear_facts_for_template
            template_id = _derive_template_id(Path(template_path))
            cleared = clear_facts_for_template(db_path, run_id, template_id)
            if cleared:
                logger.info(
                    "%s: cleared %d stale fact(s) from the failed attempt "
                    "before retrying", agent_role, cleared,
                )
        # Code-review fix (2026-06-13): also drop the failed attempt's
        # scratch workbook. A Stop-All during the retry window partial-merges
        # whatever {stmt}_filled.xlsx is on disk (gotcha #10) — which would
        # be a workbook whose DB facts were just cleared above (split-brain).
        # Best-effort: the retried attempt rewrites the file anyway.
        try:
            scratch = Path(output_dir) / f"{statement_type.value}_filled.xlsx"
            if scratch.exists():
                scratch.unlink()
                logger.info(
                    "%s: removed the failed attempt's scratch workbook %s "
                    "before retrying", agent_role, scratch,
                )
        except OSError:
            logger.warning(
                "%s: could not remove the failed attempt's scratch workbook",
                agent_role, exc_info=True,
            )

    async def _attempt(_retry_index: int) -> AgentResult:
        # _run_single_agent_attempt is unchanged — a whole attempt (fresh
        # agent + deps) that returns a structured AgentResult on every path
        # except errors classified transient (429s + connection-class), which
        # it re-raises so the scaffold can decide whether to retry.
        return await _run_single_agent_attempt(
            statement_type=statement_type,
            variant=variant,
            pdf_path=pdf_path,
            template_path=template_path,
            model=model,
            output_dir=output_dir,
            page_hints=page_hints,
            scout_context=scout_context,
            event_queue=event_queue,
            agent_id=agent_id,
            filing_level=filing_level,
            filing_standard=filing_standard,
            denomination=denomination,
            run_id=run_id,
            db_path=db_path,
        )

    async def _on_retry(total_attempts: int, last_error: Optional[str]) -> None:
        await _emit("status", {
            "phase": "reading_template",
            "message": (
                f"{agent_role}: retrying (attempt {total_attempts}) "
                f"— last error: {last_error or 'unknown'}"
            ),
        })

    async def _make_cancelled() -> AgentResult:
        # The scaffold already ran _clear_failed_attempt_facts (guarded) before
        # calling this — the Stop-All partial merge must not ship the discarded
        # attempt's facts/scratch (gotcha #10).
        await _safe_emit("complete", {
            "success": False, "error": "Cancelled by user",
        })
        return AgentResult(
            statement_type=statement_type,
            variant=variant,
            status="cancelled",
            error="Cancelled by user",
            error_type=ERROR_TYPE_CANCELLED,
        )

    async def _make_terminal(e: BaseException, last_error: str) -> AgentResult:
        # Budget exhausted — terminal structured failure. A transient error
        # that ran out of retries is classified distinctly from a genuine tool
        # exception (only transient errors are re-raised to the scaffold, so
        # this is transient_exhausted in practice; the fallback guards a future
        # non-transient re-raise — e.g. a cleanup error).
        terminal_error_type = (
            ERROR_TYPE_TRANSIENT_EXHAUSTED if _is_transient_error(e)
            else ERROR_TYPE_TOOL_EXCEPTION
        )
        logger.exception(
            "Face agent %s failed: %s", agent_role, last_error,
            exc_info=e,
        )
        await _safe_emit("error", {"message": last_error})
        await _safe_emit("complete", {"success": False, "error": last_error})
        return AgentResult(
            statement_type=statement_type,
            variant=variant,
            status="failed",
            error=last_error,
            error_type=terminal_error_type,
        )

    # Face budgets: 1 connection retry + RATE_LIMIT_MAX_RETRIES rate-limit
    # retries; no generic budget (face retries re-bill a large PDF context, so
    # a genuine code error fails fast — the attempt already converted it to a
    # structured result and never re-raised it here).
    policy = RetryPolicy(
        rate_limit_retries=RATE_LIMIT_MAX_RETRIES,
        connection_retries=1,
        generic_retries=0,
        is_rate_limit=is_rate_limit_error,
        is_connection=lambda e: isinstance(
            e, (httpx.ConnectError, httpx.ConnectTimeout)
        ),
        compute_backoff=compute_backoff_delay,
    )
    return await run_agent_with_retries(
        attempt=_attempt,
        policy=policy,
        make_terminal=_make_terminal,
        make_cancelled=_make_cancelled,
        discard_attempt_cleanup=_clear_failed_attempt_facts,
        on_retry=_on_retry,
        on_attempt_error=_accumulate_failed_attempt_usage,
        annotate_usage=_with_prior_attempt_usage,
        label=f"Face agent {agent_role}",
    )


async def _run_single_agent_attempt(
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
    denomination: str = "thousands",
    run_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> AgentResult:
    """One whole extraction attempt, streaming events into event_queue.

    Raises (rather than converting) errors classified transient so the
    retry wrapper in ``_run_single_agent`` can decide (item 10); every
    other outcome is returned as a structured AgentResult.
    """
    agent_role = statement_type.value
    # Canonical mode: derive the concept template_id from the template path
    # (deterministic — matches the importer's id) so fact writes link to the
    # right tree. None in legacy mode.
    template_id = None
    if run_id is not None and db_path:
        from concept_model.parser import _derive_template_id
        template_id = _derive_template_id(Path(template_path))

    # Push events into the queue when streaming is active (the attempt body
    # has no cancellation-teardown path, so only the plain emit is needed).
    _emit, _ = make_emitter(event_queue, agent_id, agent_role)

    # v8 telemetry: per-turn metrics captured during the agent.iter() loop
    # below. Attached to every AgentResult via `_finalize`, so the success
    # path AND the salvage/failure paths report whatever turns actually ran.
    # Each dict mirrors the run_agent_turns columns plus `_n_tool_calls`
    # (used only for the run-level rollup, ignored by the DB writer).
    _turn_records: list[dict] = []

    def _face_warnings() -> list:
        """Coverage warnings for scout-observed face lines the agent never
        accounted for (item 23). Pure read of deps state with no side effects,
        so it is safe to call BEFORE emitting the `complete` event — that's how
        the warnings reach the live SSE stream the UI records, instead of being
        computed in _finalize after `complete` already fired (the prior bug:
        they only ever reached the server log). Returns [] when scout supplied
        no face_line_refs. Best-effort: never raises."""
        try:
            from extraction.coverage import face_coverage_warnings
            refs = getattr(deps, "face_line_refs", None) or []
            if not refs:
                return []
            return face_coverage_warnings(
                refs, getattr(deps, "face_coverage_receipt", None)
            )
        except Exception:  # noqa: BLE001 — advisory
            logger.debug("face coverage warnings skipped for %s", agent_role)
            return []

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
            # v15 cache rollups — summed from the same per-turn deltas so the
            # run_agents row stays internally consistent with run_agent_turns.
            result.cache_read_tokens = sum(
                int(t.get("cache_read_tokens") or 0) for t in _turn_records
            )
            result.cache_write_tokens = sum(
                int(t.get("cache_write_tokens") or 0) for t in _turn_records
            )
        except Exception:  # noqa: BLE001 — telemetry is advisory
            logger.debug("turn telemetry finalize skipped for %s", agent_role)
        # Item 23: attach face-coverage warnings (scout-observed lines the agent
        # never accounted for). Computed from deps state so the no-receipt case
        # (every line unaccounted) is captured too. The success/salvage paths
        # already put these on the live `complete` SSE event before calling
        # _finalize; recompute here so the failure paths also carry them on the
        # returned AgentResult. Best-effort, never fatal.
        warns = _face_warnings()
        if warns:
            result.warnings = warns
            for w in warns:
                logger.warning("%s coverage: %s", agent_role, w)
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

    async def _salvage_or_fail(
        deps,
        agent_run,
        *,
        reason: str,
        fail_message: str,
        salvage_log: str,
        salvage_event_key: str,
        require_clean_verify: bool,
    ) -> AgentResult:
        """Shared exit for the bounded-abort paths — turn timeout, iteration
        cap, wall-clock (item 6), token budget (item 7).

        If a workbook landed on disk (and the last verify was clean, where
        the bound demands it) the work is done — salvage as succeeded
        rather than throwing away a balanced statement. Otherwise fail with
        the structured ``reason`` (item 9 error_type). The fatal-projection
        gate (rewrite Phase 4.1) is honoured on every path: a workbook
        whose facts never reached the DB is not salvageable, because the
        download renders from the DB.
        """
        _tokens, _cost = _safe_usage_backfill(agent_run, model, statement_type.value)
        _save_trace_best_effort(agent_run)
        if deps.projection_failed:
            err_msg = (
                f"{statement_type.value}: fact-store projection failed — "
                f"{deps.projection_error or 'see logs'}. Cannot salvage a "
                "bounded-abort run whose facts never reached the database."
            )
            logger.error(err_msg)
            await _emit("error", {"message": err_msg, "type": "projection_failed"})
            await _emit("complete", {
                "success": False, "error": err_msg,
                "workbook_path": deps.filled_path,
            })
            return _finalize(AgentResult(
                statement_type=statement_type, variant=variant, status="failed",
                workbook_path=deps.filled_path, error=err_msg,
                error_type=ERROR_TYPE_PROJECTION_FAILED,
                total_tokens=_tokens, total_cost=_cost,
            ))
        salvageable = bool(deps.filled_path) and (
            _verify_is_clean(deps.last_verify_result)
            if require_clean_verify else True
        )
        if salvageable:
            logger.warning(
                "%s/%s: %s — salvaging (workbook at %s).",
                statement_type.value, variant, salvage_log, deps.filled_path,
            )
            await _emit("complete", {
                "success": True,
                "workbook_path": deps.filled_path,
                salvage_event_key: True,
                # Item 23: surface coverage warnings on the live event here too,
                # so a salvaged statement reports unaccounted face lines.
                "warnings": _face_warnings(),
            })
            return _finalize(AgentResult(
                statement_type=statement_type,
                variant=variant,
                status="succeeded",
                workbook_path=deps.filled_path,
                total_tokens=_tokens,
                total_cost=_cost,
            ))
        logger.warning(fail_message)
        await _emit("error", {"message": fail_message, "type": reason})
        await _emit("complete", {"success": False, "error": fail_message})
        return _finalize(AgentResult(
            statement_type=statement_type,
            variant=variant,
            status="failed",
            error=fail_message,
            error_type=reason,
            total_tokens=_tokens,
            total_cost=_cost,
        ))

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
            denomination=denomination,
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
            # Item 6: whole-run wall-clock cap (the 40-turn + 180s/turn
            # guards alone legally allowed ~2h per agent). Item 7: opt-in
            # cumulative token ceiling. Both read at call time so test
            # monkeypatches of the module constant / env var take effect.
            wallclock_timeout=FACE_WALLCLOCK_TIMEOUT,
            token_budget=resolve_token_budget(),
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
                error_type=ERROR_TYPE_NO_WRITE,
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
                error_type=ERROR_TYPE_SAVE_GATE_REFUSED,
                total_tokens=final_tokens,
                total_cost=final_cost,
            ))

        # Rewrite Phase 4.1 (store-first): the fact store is the primary,
        # transactional write. If a projection CALL failed (DB error, etc.)
        # the DB is missing facts the agent believes it wrote — and since the
        # download + Concepts UI render from the DB, a "succeeded" status here
        # would be a half-populated lie. Fail the statement instead. This is
        # the infra-failure path only; unmapped cells (has_gaps) are advisory
        # and never reach this flag.
        if deps.projection_failed:
            err_msg = (
                f"{statement_type.value}: fact-store projection failed — "
                f"{deps.projection_error or 'see logs'}. The run cannot "
                "finalise this statement because the download renders from "
                "the database, not the scratch workbook."
            )
            logger.error(err_msg)
            await _emit("error", {"message": err_msg, "type": "projection_failed"})
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
                error_type=ERROR_TYPE_PROJECTION_FAILED,
                total_tokens=final_tokens,
                total_cost=final_cost,
            ))

        flag = deps.unresolved_summary if deps.completed_with_flag else None
        if flag:
            logger.warning(
                "%s/%s: finalised WITH FLAG — %s",
                statement_type.value, variant, flag,
            )
        coverage_warnings = _face_warnings()
        await _emit("complete", {
            "success": True,
            "workbook_path": deps.filled_path,
            "flag": flag,
            # Item 23: ride the live SSE stream the UI records (not just the
            # server log). Empty list is the norm — scout supplied no
            # face_line_refs, or the agent accounted for every one.
            "warnings": coverage_warnings,
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
        # iter_with_turn_timeout fired: the LLM stalled on a single node
        # iteration past FACE_TURN_TIMEOUT. Mirror the notes coordinator's
        # policy — a workbook already on disk is salvageable (no clean-
        # verify requirement, preserving the pre-item-6 behaviour).
        return await _salvage_or_fail(
            deps, agent_run,
            reason=ERROR_TYPE_TURN_TIMEOUT,
            fail_message=(
                f"{statement_type.value}: LLM stalled past "
                f"{FACE_TURN_TIMEOUT}s without writing a workbook."
            ),
            salvage_log=(
                f"LLM stalled past {FACE_TURN_TIMEOUT}s after write — "
                f"treating as done"
            ),
            salvage_event_key="stalled_after_write",
            require_clean_verify=False,
        )

    except IterationLimitReached as e:
        # The agent burned its whole iteration budget. If a workbook landed
        # AND the last verify was clean, salvage (run_id=126 SOPL) rather
        # than throwing away a balanced statement.
        logger.warning("Agent %s/%s hit iteration limit", statement_type.value, variant)
        return await _salvage_or_fail(
            deps, agent_run,
            reason=ERROR_TYPE_ITERATION_CAPPED,
            fail_message=str(e),
            salvage_log="hit iteration cap after a clean write",
            salvage_event_key="iteration_capped_after_write",
            require_clean_verify=True,
        )

    except WallclockExceeded as e:
        # Item 6: the whole-run wall-clock cap (FACE_WALLCLOCK_TIMEOUT /
        # XBRL_FACE_WALLCLOCK_S). Expiry after a clean write keeps the
        # user's workbook (the Stop-All partial-merge philosophy).
        logger.warning(
            "Agent %s/%s exceeded wall-clock cap", statement_type.value, variant,
        )
        return await _salvage_or_fail(
            deps, agent_run,
            reason=ERROR_TYPE_WALLCLOCK,
            fail_message=str(e),
            salvage_log="exceeded wall-clock cap after a clean write",
            salvage_event_key="wallclock_after_write",
            require_clean_verify=True,
        )

    except TokenBudgetExceeded as e:
        # Item 7: cumulative token budget (XBRL_MAX_TOKENS_PER_AGENT).
        # Same salvage-or-fail handling as the iteration cap.
        logger.warning(
            "Agent %s/%s exceeded token budget", statement_type.value, variant,
        )
        return await _salvage_or_fail(
            deps, agent_run,
            reason=ERROR_TYPE_TOKEN_BUDGET,
            fail_message=str(e),
            salvage_log="crossed token budget after a clean write",
            salvage_event_key="token_budget_exceeded_after_write",
            require_clean_verify=True,
        )

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
            error_type=ERROR_TYPE_CANCELLED,
        ))

    except Exception as e:
        # Item 10: errors classified transient (provider 429s, connection-
        # class — the shared `_is_transient_error` predicate) re-raise so
        # the retry wrapper can run a fresh whole attempt. Save the partial
        # trace first — the retry overwrites it, so the LAST attempt's
        # trace is what survives. The attempt's tokens/cost are annotated
        # onto the exception so the wrapper can accumulate them into the
        # final AgentResult (retried statements must not under-report
        # provider spend — code-review fix, 2026-06-13).
        if _is_transient_error(e):
            _run = locals().get("agent_run")
            _save_trace_best_effort(_run)
            if _run is not None:
                _t, _c = _safe_usage_backfill(_run, model, statement_type.value)
                try:
                    e._xbrl_attempt_tokens = _t  # type: ignore[attr-defined]
                    e._xbrl_attempt_cost = _c  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001 — telemetry is advisory
                    pass
            raise

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
            error_type=ERROR_TYPE_TOOL_EXCEPTION,
            total_tokens=_tokens,
            total_cost=_cost,
        ))
