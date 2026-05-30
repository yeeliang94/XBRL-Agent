"""Generic extraction agent factory — one agent per statement type.

Replaces the SOFP-specific create_sofp_agent with a parametric factory that works
for all 5 statement types. Each agent gets a statement-specific system prompt built
from the prompts/ directory, the same set of tools (calculator, view_pdf_pages,
write_facts, verify_totals, save_result, read_template), and optional page
hints from scout.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Union, List, Tuple, Set, Dict

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.messages import BinaryContent
from pydantic_ai.settings import ModelSettings

from statement_types import StatementType
from token_tracker import TokenReport
from tools.calculator import calculator_result_json as _calculator_impl
from tools.template_reader import read_template as _read_template_impl, TemplateField
from tools.pdf_viewer import render_pages_to_png_bytes, count_pdf_pages
from tools.fill_workbook import fill_workbook as _fill_workbook_impl, FactWrite
from tools.verifier import verify_statement as _verify_statement_impl
from extraction.history_processors import strip_stale_images, strip_duplicate_template
from prompts import render_prompt

logger = logging.getLogger(__name__)


class ExtractionDeps:
    """Dependencies carried through the agent's tool calls."""

    def __init__(
        self,
        pdf_path: str,
        template_path: str,
        model: str,
        output_dir: str,
        token_report: TokenReport,
        statement_type: StatementType,
        variant: str,
        page_hints: Optional[dict] = None,
        filing_level: str = "company",
        filing_standard: str = "mfrs",
        run_id: Optional[int] = None,
        db_path: Optional[str] = None,
        template_id: Optional[str] = None,
    ):
        self.pdf_path = pdf_path
        self.template_path = template_path
        self.model = model
        self.output_dir = output_dir
        self.token_report = token_report
        self.statement_type = statement_type
        self.variant = variant
        self.page_hints = page_hints
        self.filing_level = filing_level
        # Canonical mode (Phase B): when run_id + db_path + template_id are
        # all set, fill_workbook also projects each resolved cell write into
        # run_concept_facts so the DB becomes the authoritative fact store.
        # All None in legacy mode — the tool stays xlsx-only.
        self.run_id = run_id
        self.db_path = db_path
        self.template_id = template_id
        # Filing standard axis — surfaced to prompts so MPERS-specific
        # overlays (Phase 6.2) can inject MPERS-vs-MFRS labelling. Not used
        # for behaviour changes in Phase 2; this is wiring-only.
        self.filing_standard = filing_standard
        # Per-statement output filename for workbook isolation
        self.filled_filename = f"{statement_type.value}_filled.xlsx"
        # Mutable state
        self.template_fields: list[TemplateField] = []
        self.pdf_page_count = 0
        self.turn_counter = 0
        self.filled_path: str = ""
        # Phase 1.3: save_result gating. verify_totals populates this with
        # the most recent VerificationResult; fill_workbook clears it so
        # the agent cannot call fill_workbook then save_result without a
        # fresh verification pass. None = no verify has run yet.
        self.last_verify_result = None
        # Count the number of turns after which save_result will accept
        # a "forced" save even if verify failed — used only by the edge
        # case where the PDF genuinely cannot be balanced (gotcha #6).
        self.save_attempts = 0
        # Peer-review (Edge AFS, 2026-05-28): coordinator success contract.
        # `filled_path` alone is too weak — an agent can write a workbook,
        # have every save_result attempt refused by the gate, and end the
        # run with prose. `result_saved` flips to True only inside a
        # successful save_result path, after `{stmt}_result.json` lands on
        # disk. `last_save_error` carries the most recent gate-refusal
        # message so the coordinator can attribute the failure. The
        # `last_fill_errors` list accumulates unresolved blocking errors
        # from fill_workbook so a partial success doesn't ride through
        # silently.
        self.result_saved: bool = False
        self.result_json_path: Optional[str] = None
        self.last_save_error: Optional[str] = None
        self.last_fill_errors: list[str] = []
        # Honest-completion path (2026-05-29): the save gate blocks on any
        # imbalance / unfilled-mandatory, but prompts (gotcha #17) tell the
        # agent that some discrepancies are genuinely in the source and it
        # should "finish honestly with the gap flagged". Those two contracts
        # collided — a compliant agent had no legal way to finalise (can't
        # plug a catch-all, can't overwrite a formula total, can't save). The
        # agent may now re-call save_result with `acknowledge_unresolved=True`
        # AFTER re-examining; the gate then opens and `completed_with_flag`
        # records that the statement finalised with a known, audited gap so
        # the coordinator surfaces it instead of hard-failing + discarding the
        # extracted data.
        self.completed_with_flag: bool = False
        self.unresolved_summary: Optional[str] = None
        # Peer-review hardening: the honest-completion hatch only opens after
        # the agent has already been refused for THIS gap (so it has seen the
        # "re-examine / don't plug" guidance) AND supplies its own non-empty
        # reason. `seen_unresolved_refusal` flips True the first time the gate
        # refuses for a balance / mandatory gap; `unresolved_reason` is the
        # agent's own words (kept separate from the verifier-derived summary).
        self.seen_unresolved_refusal: bool = False
        self.unresolved_reason: Optional[str] = None
        # Rewrite Phase 4.1 (store-first): the fact store is the PRIMARY,
        # transactional write — a projection-CALL failure (project_writes
        # raising: DB error, etc.) is now FATAL, not a swallowed best-effort
        # log, because a run that "succeeds" with facts silently missing is
        # a half-populated lie (the download/Concepts UI render from the DB).
        # `_project_facts_if_canonical` sets this when the projection call
        # raises; the coordinator's success contract refuses to mark the
        # statement succeeded while it is True. NOTE: this is the infra-
        # failure path only — `proj.has_gaps` (some cells didn't map to a
        # concept, e.g. row-1 date cells) stays advisory, never fatal.
        self.projection_failed: bool = False
        self.projection_error: Optional[str] = None


def _render_single_page(pdf_path: str, page_num: int, dpi: int = 200) -> tuple[int, bytes]:
    """Render one PDF page to PNG bytes. Called in parallel by view_pdf_pages."""
    images = render_pages_to_png_bytes(pdf_path, start=page_num, end=page_num, dpi=dpi)
    return page_num, images[0]


# Phase 1.3: iteration budget at which a forced save becomes legal even if
# verify hasn't cleared. Mirrors CLAUDE.md gotcha #6 reasoning — some PDFs
# are genuinely un-balanceable and blocking save forever would hit
# MAX_AGENT_ITERATIONS=50 every time.
#
# Peer-review I1: this was previously a tool-call counter (save_attempts >= 3)
# which let an agent bypass the gate after ~6 iterations — nowhere near the
# "last-resort" intent in the plan. Gating now on the coordinator's real
# iteration counter (deps.turn_counter) makes the hatch fire at iteration
# 47+, consistent with plan §1.3: "allow a 'force save' flag if
# MAX_AGENT_ITERATIONS-3 already reached".
_FORCE_SAVE_ITER_MARGIN = 3


def _is_force_save_allowed(deps: "ExtractionDeps") -> bool:
    """Return True when the agent has consumed enough iteration budget
    that blocking another save would just waste the rest of it.

    Prefers `deps.turn_counter` (coordinator-driven, accurate). When the
    counter is zero — test harnesses that drive the gate without a real
    coordinator, or a first-save race before any iter node fires — we
    fall back to the crude save-attempts counter so those paths keep
    working. The fallback floor of 50 (larger than any normal retry
    budget) ensures test harnesses must opt in explicitly rather than
    tripping the hatch accidentally.
    """
    from agent_tracing import MAX_AGENT_ITERATIONS

    iter_based = deps.turn_counter >= MAX_AGENT_ITERATIONS - _FORCE_SAVE_ITER_MARGIN
    attempts_fallback = deps.turn_counter == 0 and deps.save_attempts >= 50
    return iter_based or attempts_fallback


def _project_facts_if_canonical(deps: "ExtractionDeps", result) -> Optional[str]:
    """Project a write_facts result's resolved writes into run_concept_facts.

    The fact store is the primary, transactional truth (rewrite Phase 4.1).
    Returns an advisory warning string when some cells didn't map to a concept
    (``proj.has_gaps`` — normal for row-1 date cells), or None on a clean pass.

    Canonical mode is always active downstream of the mandatory-bootstrap
    (run_id + db_path + template_id are threaded through on every run).

    **Projection-CALL failure is FATAL.** If ``project_writes`` itself raises
    (a DB error, a corrupt template_id, etc.) we set ``deps.projection_failed``
    so the coordinator refuses to mark the statement succeeded — a run must not
    report success with facts silently missing, because the download and the
    Concepts UI render from the DB, not from the agent's scratch xlsx. This is
    the infra-failure path ONLY; ``has_gaps`` stays advisory.
    """
    if not (deps.run_id is not None and deps.db_path and deps.template_id):
        return None
    if not result.resolved_writes:
        return None
    # Each call reflects the outcome of THIS write_facts attempt: a later
    # successful re-write clears a transient earlier failure (reset before the
    # attempt so the most recent projection result wins).
    deps.projection_failed = False
    deps.projection_error = None
    try:
        from concept_model.cell_resolver import project_writes
        proj = project_writes(
            deps.db_path,
            deps.run_id,
            deps.template_id,
            result.resolved_writes,
            filing_level=deps.filing_level,
        )
    except Exception as e:
        logger.exception(
            "canonical fact projection failed for %s — marking FATAL",
            deps.statement_type.value,
        )
        deps.projection_failed = True
        deps.projection_error = f"{type(e).__name__}: {e}"
        return (
            "Canonical projection FAILED: the fact store write did not land "
            "(see logs). This run cannot finalise — the download renders from "
            "the database, so a missing projection means missing data."
        )

    if proj.has_gaps:
        logger.warning(
            "%s: canonical projection — %d projected, %d skipped, %d rejected: "
            "skipped=%s rejected=%s",
            deps.statement_type.value, proj.projected,
            len(proj.skipped), len(proj.rejected), proj.skipped, proj.rejected,
        )
        parts = [f"Canonical projection: {proj.projected} fact(s) saved"]
        if proj.skipped:
            parts.append(f"{len(proj.skipped)} cell(s) unmapped to a concept")
        if proj.rejected:
            parts.append(f"{len(proj.rejected)} rejected by the facts API")
        return "; ".join(parts) + "."
    return None


def _check_save_gate(
    deps: "ExtractionDeps",
    acknowledge_unresolved: bool = False,
    acknowledge_reason: str = "",
) -> Optional[str]:
    """Return an error string if save_result must be blocked; None if OK.

    The gate blocks when (a) verify_totals has never run on the current
    workbook, or (b) the last run flagged an imbalance or an unfilled
    mandatory row. When the agent is within `_FORCE_SAVE_ITER_MARGIN`
    iterations of `MAX_AGENT_ITERATIONS` the gate opens as a last-resort
    escape hatch — a log line records the forced save so the run's
    audit trail captures it.

    Honest-completion path (2026-05-29): a verify-flagged gap is NOT always
    an extraction error — the source statement may genuinely not reconcile,
    or the only row that would close it is a protected formula cell. The
    prompts (gotcha #17) tell the agent to finish honestly with the gap
    flagged in that case. When `acknowledge_unresolved=True` AND at least one
    verify has run (so the gap is known, not blind), the gate opens and the
    statement finalises flagged. The "verify never ran" block is NOT
    bypassable this way — acknowledging requires a verification to acknowledge.
    """
    result = deps.last_verify_result
    forced_allowed = _is_force_save_allowed(deps)

    if result is None:
        if forced_allowed:
            logger.warning(
                "%s: save_result forced through without verify_totals "
                "(iter %d, save_attempts=%d)",
                deps.statement_type.value, deps.turn_counter,
                deps.save_attempts,
            )
            return None
        return (
            "save_result refused: verify_totals has not been called on the "
            "current workbook. Run verify_totals first, then retry save_result."
        )

    # `is_balanced` is None for statement types with no applicable balance
    # identity (e.g. SOPL/SOCI when no attribution rows exist). Treat None
    # as "not blocking" — we have nothing to gate against — but still
    # block when False.
    balance_bad = result.is_balanced is False
    mandatory_bad = bool(result.mandatory_unfilled)
    if not balance_bad and not mandatory_bad:
        return None

    if forced_allowed:
        logger.warning(
            "%s: save_result forced through despite verify gaps "
            "(iter %d, balanced=%s, unfilled=%s)",
            deps.statement_type.value,
            deps.turn_counter,
            result.is_balanced,
            result.mandatory_unfilled,
        )
        return None

    # Honest-completion path: the agent has re-examined and asserts the gap
    # is genuine (in the source, or unclosable without overwriting a formula
    # cell). Open the gate and record the flag so the statement finalises with
    # an audited imbalance rather than hard-failing and discarding the data.
    #
    # Two guardrails keep this from becoming a lazy bypass (peer-review):
    #   1. the agent must already have been refused for this gap
    #      (`seen_unresolved_refusal`) — so it has seen the "re-examine /
    #      never plug a catch-all" guidance before it can acknowledge; and
    #   2. it must supply a non-empty reason of its own.
    if acknowledge_unresolved:
        reason = (acknowledge_reason or "").strip()
        if not deps.seen_unresolved_refusal:
            # First contact with the gap — refuse once (which sets the flag
            # below and surfaces the guidance) before honouring an ack.
            pass
        elif not reason:
            return (
                "save_result refused: acknowledge_unresolved=true requires a "
                "non-empty unresolved_reason explaining why the gap is genuine "
                "(which note you re-read, why it cannot reconcile, or which "
                "formula cell blocks the close). Add it and retry."
            )
        else:
            summary_bits: list[str] = []
            if balance_bad:
                summary_bits.append(result.feedback or "unbalanced totals")
            if mandatory_bad:
                summary_bits.append(
                    "unfilled mandatory rows: "
                    + json.dumps(result.mandatory_unfilled)
                )
            deps.completed_with_flag = True
            deps.unresolved_summary = "; ".join(summary_bits) or "verify gap"
            deps.unresolved_reason = reason
            logger.warning(
                "%s: save_result finalised WITH FLAG via acknowledge_unresolved "
                "(iter %d, balanced=%s, unfilled=%s, reason=%r)",
                deps.statement_type.value,
                deps.turn_counter,
                result.is_balanced,
                result.mandatory_unfilled,
                reason,
            )
            return None

    # Record that the agent has now been told about this gap, so a follow-up
    # acknowledge_unresolved is allowed to finalise it.
    deps.seen_unresolved_refusal = True

    # Compose a targeted error message so the agent knows exactly what to fix.
    # Combined "Action required:" block (peer-review S7) — two separate
    # blocks could leave the agent unsure which issue to address first.
    parts: list[str] = ["save_result refused: the most recent verify_totals "
                        "flagged issues that must be resolved before save."]
    issues: list[str] = []
    if balance_bad:
        issues.append(f"- Balance: {result.feedback or 'unbalanced totals'}")
    if mandatory_bad:
        issues.append(
            f"- Unfilled mandatory rows: "
            f"{json.dumps(result.mandatory_unfilled)}"
        )
    parts.extend(issues)
    parts.append("Correct the issues with fill_workbook, re-run "
                 "verify_totals, then retry save_result.")
    # Tell the agent about the honest-completion escape hatch (gotcha #17):
    # if the gap is genuinely in the source, or the only row that would close
    # it is a protected formula cell, do NOT plug a catch-all — instead
    # re-call save_result with acknowledge_unresolved=true to finalise flagged.
    parts.append(
        "If you have re-examined the PDF and the discrepancy is genuinely in "
        "the source (or the only row that would close it is a protected "
        "formula cell), do NOT plug a catch-all row. Instead call save_result "
        "again with acknowledge_unresolved=true AND unresolved_reason=\"...\" "
        "(explain which note you re-read and why it cannot reconcile) to "
        "finalise with the gap flagged for review."
    )
    return "\n".join(parts)


def _format_verify_result(result) -> str:
    """Render a VerificationResult for the agent-visible tool output.

    Isolated as a module-level helper so Step 1.2 (`mandatory_unfilled`
    surfacing) and Step 1.3 (`save_result` gating) can both introspect
    the same formatting without standing up a full agent run in tests.

    Peer-review S7: balance-imbalance feedback + mandatory-unfilled
    guidance now share a single "Action required:" block. Two separate
    blocks left the agent ambiguous about priority and produced
    tool-result summaries where the same header appeared twice.
    """
    lines: list[str] = []
    lines.append(f"Balanced: {result.is_balanced}")
    lines.append(f"Matches PDF: {result.matches_pdf}")
    # Phase 4 (token-cost): the full computed_totals dump is only useful when
    # the agent has to debug an imbalance. On the balanced path the agent acts
    # on nothing in it, so omit it to stop re-billing the dump every turn.
    # Failure-path detail (mismatches, mandatory_unfilled, feedback) stays
    # fully intact below so self-correction is unaffected.
    if not result.is_balanced:
        lines.append(f"Computed totals: {json.dumps(result.computed_totals, indent=2)}")
    if result.mismatches:
        lines.append(f"Mismatches: {json.dumps(result.mismatches, indent=2)}")
    if result.mandatory_unfilled:
        lines.append(
            "Mandatory fields unfilled: "
            + json.dumps(result.mandatory_unfilled, indent=2)
        )
    # Only treat the verifier feedback as an action when something is actually
    # wrong. The non-SOFP verifiers reuse `feedback` to carry a SUCCESS message
    # (e.g. "SOPL attribution check passed."); routing that under "Action
    # required:" told the agent to fix a clean statement and contributed to the
    # run_id=126 SOPL loop. A problem exists iff there are mismatches, an
    # explicit imbalance, or unfilled mandatory rows.
    has_problem = (
        bool(result.mismatches)
        or result.is_balanced is False
        or bool(result.mandatory_unfilled)
    )
    actions: list[str] = []
    if result.feedback and has_problem:
        actions.append(result.feedback)
    if result.mandatory_unfilled:
        actions.append(
            "One or more mandatory ('*'-prefixed) rows are blank. "
            "View the relevant PDF pages and fill the listed rows "
            "before calling save_result."
        )
    if actions:
        lines.append("\nAction required:\n" + "\n\n".join(actions))
    elif result.feedback:
        # Clean verification — surface the verifier's note as status, not a
        # demand for action, so the agent moves on to save_result.
        lines.append(f"Status: {result.feedback}")
    return "\n".join(lines)


def _summarize_template(fields: list[TemplateField]) -> str:
    """Convert template fields into human-readable structure summary."""
    sheets: dict = {}
    for f in fields:
        if f.sheet not in sheets:
            sheets[f.sheet] = {"total": 0, "formula": 0, "data_entry": 0, "rows": []}
        sheets[f.sheet]["total"] += 1
        if f.has_formula:
            sheets[f.sheet]["formula"] += 1
        else:
            sheets[f.sheet]["data_entry"] += 1
        sheets[f.sheet]["rows"].append(
            {
                "coord": f.coordinate,
                "row": f.row,
                "label": f.label[:80],
                "is_data_entry": f.is_data_entry,
                "is_abstract": getattr(f, "is_abstract", False),
                "formula": f.formula[:60] if f.formula else None,
            }
        )

    lines = []
    for sheet_name, info in sheets.items():
        lines.append(f"\n=== Sheet: {sheet_name} ===")
        lines.append(
            f"Total cells: {info['total']} | Data entry: {info['data_entry']} | Formulas: {info['formula']}"
        )
        # Bug A: surface ABSTRACT explicitly so the agent's read_template
        # summary stops calling section-header rows DATA_ENTRY. Without this
        # the agent saw e.g. "Interest income" tagged DATA_ENTRY and wrote
        # numeric values onto the abstract concept instead of the leaf rows
        # below. The writer will refuse abstract writes regardless, but
        # surfacing it here gives the agent the hint up front.
        for r in info["rows"]:
            if r["is_abstract"]:
                status = "ABSTRACT (section header — do not write)"
            elif r["is_data_entry"]:
                status = "DATA_ENTRY"
            else:
                status = f"FORMULA: {r['formula']}"
            lines.append(
                f"  {r['coord']:>5} (row {r['row']:>3}): {r['label']:<60} [{status}]"
            )

    return "\n".join(lines)


def create_extraction_agent(
    statement_type: StatementType,
    variant: str,
    pdf_path: str,
    template_path: str,
    model: Union[str, Model] = "openai.gpt-5.4",
    output_dir: Optional[str] = None,
    cache_template: bool = False,
    page_hints: Optional[dict] = None,
    scout_context: Optional[dict] = None,
    filing_level: str = "company",
    filing_standard: str = "mfrs",
    run_id: Optional[int] = None,
    db_path: Optional[str] = None,
    template_id: Optional[str] = None,
) -> tuple[Agent[ExtractionDeps, str], ExtractionDeps]:
    """Create an extraction agent for any statement type.

    Args:
        statement_type: Which financial statement (SOFP, SOPL, etc.)
        variant: Which variant (CuNonCu, Function, Indirect, etc.)
        pdf_path: Path to the source PDF.
        template_path: Path to the XBRL Excel template.
        model: LLM model name or PydanticAI Model object.
        output_dir: Where to write output files.
        cache_template: If True, embed template structure in system prompt.
        page_hints: Dict from scout with face_page and note_pages (soft guidance only).
    """
    if output_dir is None:
        output_dir = str(Path(__file__).resolve().parent.parent / "output")

    token_report = TokenReport(model=model)
    deps = ExtractionDeps(
        pdf_path=pdf_path,
        template_path=template_path,
        model=model,
        output_dir=output_dir,
        token_report=token_report,
        statement_type=statement_type,
        variant=variant,
        page_hints=page_hints,
        filing_level=filing_level,
        filing_standard=filing_standard,
        run_id=run_id,
        db_path=db_path,
        template_id=template_id,
    )

    # Optionally embed template in system prompt for caching
    template_summary = None
    if cache_template:
        fields = _read_template_impl(template_path)
        deps.template_fields = fields
        template_summary = _summarize_template(fields)

    system_prompt = render_prompt(
        statement_type=statement_type,
        variant=variant,
        template_summary=template_summary,
        page_hints=page_hints,
        filing_level=filing_level,
        filing_standard=filing_standard,
        # RUN-REVIEW P2-2: pass the live template path so SOCF/SoRE
        # prompts get a per-row sign-from-formula block injected.
        template_path=template_path,
        # Phase 2 — entity / period / unit context from scout. None /
        # empty dict means no scout enrichment and the renderer omits
        # the block (today's behaviour preserved).
        scout_context=scout_context,
    )

    # Pin temperature=1.0 explicitly. CLAUDE.md gotcha #5: Gemini 3 through
    # the enterprise proxy requires T=1.0 — lower values cause failures or
    # infinite loops. Relying on upstream defaults was fine in practice but
    # brittle across provider/SDK versions (peer-review I2).
    agent = Agent(
        model,
        deps_type=ExtractionDeps,
        system_prompt=system_prompt,
        model_settings=ModelSettings(temperature=1.0),
        # Token-cost reduction: strip re-billed payloads from the outbound
        # request each turn — stale page images and the repeated template
        # summary. Pure functions over the message list; see
        # extraction/history_processors.py and docs/Archive/PLAN-token-cost-reduction.md.
        history_processors=[strip_stale_images, strip_duplicate_template],
    )

    # --- Tools ---

    @agent.tool
    def calculator(ctx: RunContext[ExtractionDeps], expression: str) -> str:
        """Evaluate arithmetic exactly.

        Use this for subtotal checks and reconciliations after reading numbers
        from the PDF. Supports numbers, parentheses, unary signs, and + - * /.
        Use explicit negatives such as -123; accounting parentheses are
        treated as ordinary grouping.
        """
        return _calculator_impl(expression)

    @agent.tool
    def read_template(ctx: RunContext[ExtractionDeps]) -> str:
        """Read the template structure. Returns the full template summary
        (cached after the first call so repeated calls are free)."""
        # Phase 2 (token-cost): when the summary is already embedded in the
        # system prompt (cache_template=True), don't return a second full copy —
        # that would land the ~12k-token summary twice. Point the agent at the
        # copy it already has.
        if cache_template:
            return "Template structure already embedded in the system prompt above."
        if not ctx.deps.template_fields:
            ctx.deps.template_fields = _read_template_impl(ctx.deps.template_path)
        return _summarize_template(ctx.deps.template_fields)

    @agent.tool
    def view_pdf_pages(ctx: RunContext[ExtractionDeps], pages: List[int]) -> List[Union[str, BinaryContent]]:
        """View specific PDF pages as images. Pass a list of page numbers, e.g. [1, 2, 3].
        Returns page images directly — you can read the content visually."""
        ctx.deps.pdf_page_count = count_pdf_pages(ctx.deps.pdf_path)
        total_pages = ctx.deps.pdf_page_count

        requested_pages = [p for p in pages if isinstance(p, int)]
        invalid_pages = sorted({p for p in requested_pages if p < 1 or p > total_pages})
        render_pages = sorted(set(p for p in requested_pages if p not in invalid_pages))

        results: List[Union[str, BinaryContent]] = []

        if invalid_pages:
            results.append(
                f"Skipped invalid page(s) {invalid_pages}. Valid PDF page range is 1-{total_pages}."
            )
        if not render_pages:
            results.append("No pages were rendered from this request.")
            return results

        rendered: dict[int, bytes] = {}
        with ThreadPoolExecutor(max_workers=min(len(render_pages), 8)) as pool:
            futures = {
                pool.submit(_render_single_page, ctx.deps.pdf_path, p): p
                for p in render_pages
            }
            for future in futures:
                page_num, png_bytes = future.result()
                rendered[page_num] = png_bytes

        for p in sorted(rendered):
            results.append(f"=== Page {p} ===")
            results.append(BinaryContent(data=rendered[p], media_type="image/png"))

        return results

    @agent.tool
    def write_facts(ctx: RunContext[ExtractionDeps], facts: List[FactWrite]) -> str:
        """Write extracted values to the statement's cells.

        Each entry in ``facts`` is one cell write. ``evidence`` is REQUIRED on
        every entry (the PDF page + a short quote) — it is the audit trail.

        Two addressing modes:

            Label matching (most statements) — set ``field_label`` (and
            ``section`` to disambiguate duplicate labels):
                {"sheet": "...", "field_label": "...", "section": "...",
                 "col": 2, "value": 123, "evidence": "Page X, '<quote>'"}
              - col: 2 for current year (CY), 3 for prior year (PY)

            Explicit cell coordinates (SOCIE matrix and other complex layouts)
            — set ``row`` instead of ``field_label``:
                {"sheet": "...", "row": 6, "col": 3, "value": 123,
                 "evidence": "..."}
              - row: the 1-indexed row number from read_template()
              - col: any column number (B=2, C=3, D=4, ... X=24)

        Only write to data-entry cells. Never write to formula cells.
        """
        output_path = str(Path(ctx.deps.output_dir) / ctx.deps.filled_filename)
        source_path = (
            ctx.deps.filled_path
            if ctx.deps.filled_path and Path(ctx.deps.filled_path).exists()
            else ctx.deps.template_path
        )
        result = _fill_workbook_impl(
            template_path=source_path,
            output_path=output_path,
            facts=facts,
            filing_level=ctx.deps.filing_level,
        )
        if result.success:
            ctx.deps.filled_path = output_path
            # Phase 1.3: any write invalidates the previous verification.
            # Forces the agent to call verify_totals again before save.
            ctx.deps.last_verify_result = None
            # Peer-review (Edge AFS): a fresh write also invalidates the
            # previous save — the JSON on disk no longer matches the
            # workbook content. The agent must call save_result again.
            ctx.deps.result_saved = False
            # Peer-review: a fresh write may have closed the gap the agent
            # previously acknowledged. Clear the flag so a subsequent clean
            # save doesn't stamp a stale `_unresolved_flag`. A genuinely
            # still-broken statement re-acknowledges (cheap) after re-verify.
            ctx.deps.completed_with_flag = False
            ctx.deps.unresolved_summary = None
            ctx.deps.unresolved_reason = None
            # Track unresolved blocking errors from this fill so the
            # coordinator can see them even if a later save_result lands.
            # Empty list when the fill is clean.
            ctx.deps.last_fill_errors = list(result.errors)
            projection_warning = _project_facts_if_canonical(ctx.deps, result)

        if result.success:
            msg = f"Successfully wrote {result.fields_written} fields to {output_path}."
            # Phase 4 (token-cost): collapse the error/warning arrays to a
            # count + one-line summary instead of dumping the raw list repr
            # every turn. The messages themselves are kept (double-booking
            # warnings must still surface enough for the agent to act —
            # RUN-REVIEW P1-1), just rendered compactly on a single line.
            if result.errors:
                msg += f"\n{len(result.errors)} error(s): " + "; ".join(result.errors)
            if result.warnings:
                msg += f"\n{len(result.warnings)} warning(s): " + "; ".join(result.warnings)
            # Canonical mode: surface any facts that didn't make it into the
            # DB so the gap isn't silent (peer-review HIGH). Advisory — the
            # xlsx write still succeeded.
            if projection_warning:
                msg += f"\n{projection_warning}"
            return msg
        else:
            return f"Failed to fill workbook. Errors: {result.errors}"

    @agent.tool
    def verify_totals(ctx: RunContext[ExtractionDeps]) -> str:
        """Verify the filled workbook — checks balance/consistency for this statement."""
        filled_path = ctx.deps.filled_path
        if not filled_path:
            # Per-statement workbook (multi-agent) takes priority over legacy shared name
            stmt_path = Path(ctx.deps.output_dir) / f"{ctx.deps.statement_type.value}_filled.xlsx"
            if stmt_path.exists():
                filled_path = str(stmt_path)
            else:
                return "No filled workbook found yet. Run fill_workbook first."
        result = _verify_statement_impl(
            filled_path,
            ctx.deps.statement_type,
            ctx.deps.variant,
            filing_level=ctx.deps.filing_level,
            filing_standard=ctx.deps.filing_standard,
        )
        # Phase 1.3: remember the last verification so save_result can
        # refuse to finalise if the agent skipped or failed verification.
        ctx.deps.last_verify_result = result
        return _format_verify_result(result)

    @agent.tool
    def save_result(
        ctx: RunContext[ExtractionDeps],
        fields_json: str,
        acknowledge_unresolved: bool = False,
        unresolved_reason: str = "",
    ) -> str:
        """Save extraction results (JSON + cost report) to the output directory.

        Phase 1.3: refuses to finalise unless the most recent verification
        passed AND no mandatory (`*`) rows are unfilled. If verify_totals
        hasn't been called since the last fill_workbook, the save is
        blocked — the agent is told to re-verify.

        Set ``acknowledge_unresolved=True`` (with a non-empty
        ``unresolved_reason``) ONLY when you have re-examined the PDF and the
        verify gap is genuinely in the source (or the only row that would close
        it is a protected formula cell). This finalises the statement WITH the
        gap flagged for human review (gotcha #17) instead of plugging a
        catch-all row. It is honoured only after the gate has already refused
        the same gap once. Never use it to skip legitimate corrections.
        """
        ctx.deps.save_attempts += 1
        gate_error = _check_save_gate(
            ctx.deps, acknowledge_unresolved, unresolved_reason
        )
        if gate_error is not None:
            ctx.deps.last_save_error = gate_error
            return gate_error
        fields = json.loads(fields_json)
        # Stamp the audited-gap metadata onto the persisted result so the
        # download / review surface can show WHY it was finalised flagged.
        if ctx.deps.completed_with_flag and isinstance(fields, dict):
            fields.setdefault("_unresolved_flag", ctx.deps.unresolved_summary)
            fields.setdefault("_unresolved_reason", ctx.deps.unresolved_reason)
        stmt_prefix = ctx.deps.statement_type.value
        json_path = Path(ctx.deps.output_dir) / f"{stmt_prefix}_result.json"
        json_path.write_text(json.dumps(fields, indent=2), encoding="utf-8")

        report = ctx.deps.token_report.format_table()
        report_path = Path(ctx.deps.output_dir) / f"{stmt_prefix}_cost_report.txt"
        report_path.write_text(report, encoding="utf-8")

        # Peer-review (Edge AFS): record that save actually succeeded so the
        # coordinator can distinguish "workbook exists" from "extraction
        # declared complete". `last_save_error` is cleared because we are no
        # longer in a refused state.
        ctx.deps.result_saved = True
        ctx.deps.result_json_path = str(json_path)
        ctx.deps.last_save_error = None

        # Phase 4 (token-cost): write the cost-report body to file only —
        # the agent does not act on it, so don't re-bill it in the tool return.
        if ctx.deps.completed_with_flag:
            return (
                f"Results saved to {json_path} WITH A FLAGGED GAP "
                f"({ctx.deps.unresolved_summary}). The statement is finalised "
                f"for human review. Cost report saved to {report_path}."
            )
        return f"Results saved to {json_path}. Cost report saved to {report_path}."

    return agent, deps
