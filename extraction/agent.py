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

from statement_types import StatementType
from token_tracker import TokenReport
from tools.template_reader import read_template as _read_template_impl, TemplateField
from tools.pdf_viewer import render_pages_to_images, count_pdf_pages
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
        allowed_pages: Optional[set[int]] = None,
    ):
        self.pdf_path = pdf_path
        self.template_path = template_path
        self.model = model
        self.output_dir = output_dir
        self.token_report = token_report
        self.statement_type = statement_type
        self.variant = variant
        self.page_hints = page_hints
        self.allowed_pages = allowed_pages
        # Per-statement output filename for workbook isolation
        self.filled_filename = f"{statement_type.value}_filled.xlsx"
        # Mutable state
        self.template_fields: list[TemplateField] = []
        self.pdf_page_count = 0
        self.turn_counter = 0
        self.filled_path: str = ""


def _render_single_page(pdf_path: str, page_num: int, output_dir: str, dpi: int = 200) -> tuple[int, bytes]:
    """Render one PDF page to PNG bytes. Called in parallel by view_pdf_pages."""
    images = render_pages_to_images(pdf_path, start=page_num, end=page_num, output_dir=output_dir, dpi=dpi)
    return page_num, images[0].read_bytes()


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
    allowed_pages: Optional[set[int]] = None,
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
        page_hints: Dict from scout with face_page and note_pages.
        allowed_pages: Set of PDF pages this agent may access (None = all).
    """
    if output_dir is None:
        output_dir = str(Path(__file__).resolve().parent.parent / "output")

    # When scout provides page hints, scope the agent to those pages unless the
    # caller explicitly overrides the allowed set.
    if page_hints and allowed_pages is None:
        derived_allowed_pages: set[int] = set()

        face_page = page_hints.get("face_page")
        if isinstance(face_page, int):
            derived_allowed_pages.add(face_page)

        note_pages = page_hints.get("note_pages", [])
        derived_allowed_pages.update(p for p in note_pages if isinstance(p, int))

        if derived_allowed_pages:
            allowed_pages = derived_allowed_pages

    token_report = TokenReport()
    deps = ExtractionDeps(
        pdf_path=pdf_path,
        template_path=template_path,
        model=model,
        output_dir=output_dir,
        token_report=token_report,
        statement_type=statement_type,
        variant=variant,
        page_hints=page_hints,
        allowed_pages=allowed_pages,
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
    )

    agent = Agent(
        model,
        deps_type=ExtractionDeps,
        system_prompt=system_prompt,
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

        allowed_pages = ctx.deps.allowed_pages
        render_pages = [p for p in requested_pages if p not in invalid_pages]
        disallowed_pages: list[int] = []
        if allowed_pages is not None:
            disallowed_pages = sorted({p for p in render_pages if p not in allowed_pages})
            render_pages = [p for p in render_pages if p in allowed_pages]

        # Avoid duplicate work if the model requests the same page multiple times.
        render_pages = sorted(set(render_pages))

        out_dir = str(Path(ctx.deps.output_dir) / "images")
        results: List[Union[str, BinaryContent]] = []

        if invalid_pages:
            results.append(
                f"Skipped invalid page(s) {invalid_pages}. Valid PDF page range is 1-{total_pages}."
            )
        if disallowed_pages:
            allowed_sorted = sorted(allowed_pages)
            results.append(
                f"Skipped disallowed page(s) {disallowed_pages}. You may only view scout-approved pages {allowed_sorted}."
            )
        if not render_pages:
            results.append("No pages were rendered from this request.")
            return results

        rendered: dict[int, bytes] = {}
        with ThreadPoolExecutor(max_workers=min(len(render_pages), 8)) as pool:
            futures = {
                pool.submit(_render_single_page, ctx.deps.pdf_path, p, out_dir): p
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
        )
        if result.success:
            ctx.deps.filled_path = output_path

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
        )

        lines = []
        lines.append(f"Balanced: {result.is_balanced}")
        lines.append(f"Matches PDF: {result.matches_pdf}")
        lines.append(f"Computed totals: {json.dumps(result.computed_totals, indent=2)}")
        if result.mismatches:
            lines.append(f"Mismatches: {json.dumps(result.mismatches, indent=2)}")
        if result.feedback:
            lines.append(f"\nAction required:\n{result.feedback}")
        return "\n".join(lines)

    @agent.tool
    def save_result(ctx: RunContext[ExtractionDeps], fields_json: str) -> str:
        """Save extraction results (JSON + cost report) to the output directory."""
        fields = json.loads(fields_json)
        stmt_prefix = ctx.deps.statement_type.value
        json_path = Path(ctx.deps.output_dir) / f"{stmt_prefix}_result.json"
        json_path.write_text(json.dumps(fields, indent=2), encoding="utf-8")

        report = ctx.deps.token_report.format_table()
        report_path = Path(ctx.deps.output_dir) / f"{stmt_prefix}_cost_report.txt"
        report_path.write_text(report, encoding="utf-8")

        return f"Results saved to {json_path}\nCost report saved to {report_path}\n\n{report}"

    return agent, deps
