"""Generic extraction agent factory — one agent per statement type.

Replaces the SOFP-specific create_sofp_agent with a parametric factory that works
for all 5 statement types. Each agent gets a statement-specific system prompt built
from the prompts/ directory, the same set of tools (view_pdf_pages, fill_workbook,
verify_totals, save_result, read_template), and optional page hints from scout.
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
from tools.template_reader import read_template as _read_template_impl, TemplateField
from tools.pdf_viewer import render_pages_to_png_bytes, count_pdf_pages
from tools.fill_workbook import fill_workbook as _fill_workbook_impl
from tools.verifier import verify_statement as _verify_statement_impl
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


def _check_save_gate(deps: "ExtractionDeps") -> Optional[str]:
    """Return an error string if save_result must be blocked; None if OK.

    The gate blocks when (a) verify_totals has never run on the current
    workbook, or (b) the last run flagged an imbalance or an unfilled
    mandatory row. When the agent is within `_FORCE_SAVE_ITER_MARGIN`
    iterations of `MAX_AGENT_ITERATIONS` the gate opens as a last-resort
    escape hatch — a log line records the forced save so the run's
    audit trail captures it.
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
    lines.append(f"Computed totals: {json.dumps(result.computed_totals, indent=2)}")
    if result.mismatches:
        lines.append(f"Mismatches: {json.dumps(result.mismatches, indent=2)}")
    if result.mandatory_unfilled:
        lines.append(
            "Mandatory fields unfilled: "
            + json.dumps(result.mandatory_unfilled, indent=2)
        )
    actions: list[str] = []
    if result.feedback:
        actions.append(result.feedback)
    if result.mandatory_unfilled:
        actions.append(
            "One or more mandatory ('*'-prefixed) rows are blank. "
            "View the relevant PDF pages and fill the listed rows "
            "before calling save_result."
        )
    if actions:
        lines.append("\nAction required:\n" + "\n\n".join(actions))
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
                "formula": f.formula[:60] if f.formula else None,
            }
        )

    lines = []
    for sheet_name, info in sheets.items():
        lines.append(f"\n=== Sheet: {sheet_name} ===")
        lines.append(
            f"Total cells: {info['total']} | Data entry: {info['data_entry']} | Formulas: {info['formula']}"
        )
        for r in info["rows"]:
            status = "DATA_ENTRY" if r["is_data_entry"] else f"FORMULA: {r['formula']}"
            lines.append(
                f"  {r['coord']:>5} (row {r['row']:>3}): {r['label']:<60} [{status}]"
            )

    return "\n".join(lines)


def create_extraction_agent(
    statement_type: StatementType,
    variant: str,
    pdf_path: str,
    template_path: str,
    model: Union[str, Model] = "google-gla:gemini-3-flash-preview",
    output_dir: Optional[str] = None,
    cache_template: bool = False,
    page_hints: Optional[dict] = None,
    filing_level: str = "company",
    filing_standard: str = "mfrs",
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
    )

    # --- Tools ---

    @agent.tool
    def read_template(ctx: RunContext[ExtractionDeps]) -> str:
        """Read the template structure. Returns the full template summary
        (cached after the first call so repeated calls are free)."""
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
    def fill_workbook(ctx: RunContext[ExtractionDeps], fields_json: str) -> str:
        """Write field values to the Excel template.

        Args:
            fields_json: JSON with field mappings. Two modes supported:

                Label matching (most statements):
                {"fields": [{"sheet": "...", "field_label": "...", "section": "...",
                  "col": 2, "value": 123, "evidence": "Page X, description"}, ...]}
                - col: 2 for current year (CY), 3 for prior year (PY)

                Explicit cell coordinates (SOCIE matrix and other complex layouts):
                {"fields": [{"sheet": "...", "row": 6, "col": 3, "value": 123,
                  "evidence": "..."}, ...]}
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
            fields_json=fields_json,
            filing_level=ctx.deps.filing_level,
        )
        if result.success:
            ctx.deps.filled_path = output_path
            # Phase 1.3: any write invalidates the previous verification.
            # Forces the agent to call verify_totals again before save.
            ctx.deps.last_verify_result = None

        if result.success:
            msg = f"Successfully wrote {result.fields_written} fields to {output_path}."
            if result.errors:
                msg += f"\nErrors: {result.errors}"
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
        )
        # Phase 1.3: remember the last verification so save_result can
        # refuse to finalise if the agent skipped or failed verification.
        ctx.deps.last_verify_result = result
        return _format_verify_result(result)

    @agent.tool
    def save_result(ctx: RunContext[ExtractionDeps], fields_json: str) -> str:
        """Save extraction results (JSON + cost report) to the output directory.

        Phase 1.3: refuses to finalise unless the most recent verification
        passed AND no mandatory (`*`) rows are unfilled. If verify_totals
        hasn't been called since the last fill_workbook, the save is
        blocked — the agent is told to re-verify.
        """
        ctx.deps.save_attempts += 1
        gate_error = _check_save_gate(ctx.deps)
        if gate_error is not None:
            return gate_error
        fields = json.loads(fields_json)
        stmt_prefix = ctx.deps.statement_type.value
        json_path = Path(ctx.deps.output_dir) / f"{stmt_prefix}_result.json"
        json_path.write_text(json.dumps(fields, indent=2), encoding="utf-8")

        report = ctx.deps.token_report.format_table()
        report_path = Path(ctx.deps.output_dir) / f"{stmt_prefix}_cost_report.txt"
        report_path.write_text(report, encoding="utf-8")

        return f"Results saved to {json_path}\nCost report saved to {report_path}\n\n{report}"

    return agent, deps
