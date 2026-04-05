import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.messages import BinaryContent

from token_tracker import TokenReport
from tools.template_reader import read_template as _read_template_impl, TemplateField
from tools.pdf_viewer import render_pages_to_images, count_pdf_pages
from tools.fill_workbook import fill_workbook as _fill_workbook_impl, FillResult
from tools.verifier import verify_totals as _verify_totals_impl, VerificationResult

logger = logging.getLogger(__name__)


def _build_system_prompt(template_summary: str | None = None) -> str:
    """Build the system prompt. When template_summary is provided, it's embedded
    directly so Gemini can cache it across turns (avoids re-reading every call)."""
    base = """\
You are a senior Malaysian chartered accountant specialising in XBRL financial reporting \
for Malaysian public listed companies under MFRS (Malaysian Financial Reporting Standards). \
You are extracting data from audited financial statements to fill the SSM MBRS XBRL template \
for filing with the Companies Commission of Malaysia (SSM).

You are meticulous, precise, and follow Malaysian accounting best practices. When there is \
ambiguity in how a PDF line item maps to a template field, apply professional judgement \
consistent with MFRS disclosure requirements and SSM MBRS filing conventions.

=== TEMPLATE STRUCTURE ===

The MBRS template has TWO sheets that MUST BOTH be filled:

1. **SOFP-CuNonCu** (main sheet) — Face of the Statement of Financial Position.
   Contains high-level line items. Many cells are FORMULAS that pull from the sub-sheet.
   Only fill DATA-ENTRY cells here (non-formula cells like "Right-of-use assets",
   "Retained earnings", "Lease liabilities", "Contract liabilities").

2. **SOFP-Sub-CuNonCu** (sub-sheet) — Detailed breakdowns of each main-sheet line item.
   This is where MOST of your data should go. The sub-sheet has granular fields like:
   - "Office equipment, fixture and fittings" under Property, plant and equipment
   - "Trade receivables" under Current trade receivables
   - "Deposits" under Current non-trade receivables
   - "Balances with Licensed Banks" under Cash
   - "Accruals", "Deferred income", "Other current non-trade payables" under Current non-trade payables

   The main sheet formulas automatically sum these sub-sheet values. If you only fill
   the main sheet, the formulas will OVERWRITE your values when opened in Excel.

=== STRATEGY ===

IMPORTANT: Fill the SUB-SHEET (SOFP-Sub-CuNonCu) FIRST. The main sheet (SOFP-CuNonCu)
has formulas that pull totals from the sub-sheet automatically. Only fill non-formula
data-entry cells on the main sheet (e.g. "Right-of-use assets", "Retained earnings",
"Lease liabilities", "Contract liabilities").

1. Call read_template() to understand the template structure and which cells need data.
2. Call view_pdf_pages() with pages [1, 2, 3] to find the table of contents.
3. Identify the SOFP (Statement of Financial Position) page from the TOC.
4. Call view_pdf_pages() with just the SOFP page to see the face of the statement.
5. For each SOFP face line item that has a note reference (e.g. "Note 4", "Note 5"):
   - View the note pages to get the detailed breakdown.
   - Map each note line item to its sub-sheet field (SOFP-Sub-CuNonCu).
   - This is where most of your data goes.
6. For SOFP face line items WITHOUT note references or that are direct data-entry
   on the main sheet (like "Right-of-use assets", "Retained earnings", "Lease liabilities"),
   fill them on the main sheet (SOFP-CuNonCu).
7. Call fill_workbook() with ALL field mappings. Prioritise sub-sheet fields:
   - Sub-sheet example: {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Trade receivables",
     "section": "current trade receivables", "col": 2, "value": 384375, ...}
   - Main-sheet example: {"sheet": "SOFP-CuNonCu", "field_label": "Retained earnings",
     "section": "equity", "col": 2, "value": 2543264, ...}
   For EVERY field include: sheet, field_label, section, col (2=CY, 3=PY), value, evidence.
8. Call verify_totals() to check the balance sheet balances.
9. If totals don't balance, identify which section is wrong, re-examine those note pages,
   and call fill_workbook() again with corrections.
10. Call save_result() when totals balance.

=== CRITICAL RULES ===

- ALWAYS fill the sub-sheet (SOFP-Sub-CuNonCu) for every breakdown you find in the notes.
  The main sheet formulas depend on sub-sheet data. Missing sub-sheet values = wrong totals.
- When a note shows a breakdown (e.g. "Other payables" note shows Accruals RM399,113 and
  Other payables RM2,809), fill EACH line item separately on the sub-sheet — do NOT lump
  them into one field.
- "Accruals" in the template means ONLY the accruals line. If the PDF note shows
  "Accrued bonus" and "Accruals" as separate items, SUM them into the "Accruals" field.
  Do NOT put accrued bonus into "Other current non-trade payables".
- "Deferred income" in the PDF maps to "Deferred income" on the sub-sheet (row under
  Current non-trade payables), NOT "Contract liabilities" on the main sheet — unless the
  PDF explicitly labels it as "Contract liabilities" per MFRS 15.
- "Deposits" in receivable notes → "Deposits" under Current non-trade receivables.
  Do NOT put deposits into "Other current non-trade receivables".
- Use field_label (not row numbers) when calling fill_workbook.
- Always include "section" for ambiguous labels (current vs non-current).
- Do NOT bulk-scan the entire PDF. Only view pages you specifically need.
- Be precise reading numbers. Malaysian statements use RM (Ringgit Malaysia).
  Values are often in RM thousands — check the statement header for the unit."""

    if template_summary:
        base += f"""

=== TEMPLATE STRUCTURE (cached — do not call read_template again) ===
{template_summary}
=== END TEMPLATE STRUCTURE ==="""

    return base


class AgentDeps:
    def __init__(
        self,
        pdf_path: str,
        template_path: str,
        model: str,
        output_dir: str,
        token_report: TokenReport,
    ):
        self.pdf_path = pdf_path
        self.template_path = template_path
        self.model = model
        self.output_dir = output_dir
        self.token_report = token_report
        self.template_fields: list[TemplateField] = []
        self.pdf_page_count = 0
        self.turn_counter = 0
        self.filled_path: str = ""


def _render_single_page(pdf_path: str, page_num: int, output_dir: str, dpi: int = 200) -> tuple[int, bytes]:
    """Render one PDF page to PNG bytes. Called in parallel by view_pdf_pages."""
    images = render_pages_to_images(pdf_path, start=page_num, end=page_num, output_dir=output_dir, dpi=dpi)
    return page_num, images[0].read_bytes()


def create_sofp_agent(
    pdf_path: str,
    template_path: str,
    model: str | Model = "google-gla:gemini-3-flash-preview",
    output_dir: str | None = None,
    cache_template: bool = False,
) -> tuple[Agent[AgentDeps, str], AgentDeps]:
    # Default output dir is resolved relative to this file, not the caller's working dir,
    # so the agent works regardless of where it's invoked from.
    if output_dir is None:
        output_dir = str(Path(__file__).resolve().parent / "output")
    token_report = TokenReport()
    deps = AgentDeps(
        pdf_path=pdf_path,
        template_path=template_path,
        model=model,
        output_dir=output_dir,
        token_report=token_report,
    )

    # Optionally embed the template in the system prompt for caching
    template_summary = None
    if cache_template:
        fields = _read_template_impl(template_path)
        deps.template_fields = fields
        template_summary = _summarize_template(fields)

    agent = Agent(
        model,
        deps_type=AgentDeps,
        system_prompt=_build_system_prompt(template_summary),
    )

    @agent.tool
    def read_template(ctx: RunContext[AgentDeps]) -> str:
        """Read the SOFP template structure. If template is cached in the system prompt,
        this returns a brief confirmation instead of re-reading."""
        if ctx.deps.template_fields:
            return "Template structure is already in your system prompt above. Use that reference."

        fields = _read_template_impl(ctx.deps.template_path)
        ctx.deps.template_fields = fields
        return _summarize_template(fields)

    @agent.tool
    def view_pdf_pages(ctx: RunContext[AgentDeps], pages: list[int]) -> list[str | BinaryContent]:
        """View specific PDF pages as images. Pass a list of page numbers, e.g. [1, 2, 3] or [14].
        Returns page images directly — you can read the content visually."""
        ctx.deps.pdf_page_count = count_pdf_pages(ctx.deps.pdf_path)
        out_dir = str(Path(ctx.deps.output_dir) / "images")

        # Parallel rendering when multiple pages requested
        rendered: dict[int, bytes] = {}
        with ThreadPoolExecutor(max_workers=min(len(pages), 8)) as pool:
            futures = {
                pool.submit(_render_single_page, ctx.deps.pdf_path, p, out_dir): p
                for p in pages
            }
            for future in futures:
                page_num, png_bytes = future.result()
                rendered[page_num] = png_bytes

        # Build result list sorted by page number
        results: list[str | BinaryContent] = []
        for p in sorted(rendered):
            results.append(f"=== Page {p} ===")
            results.append(BinaryContent(data=rendered[p], media_type="image/png"))

        return results

    @agent.tool
    def fill_workbook(ctx: RunContext[AgentDeps], fields_json: str) -> str:
        """Write field values to the Excel template. Matches by field label, not row number.

        Args:
            fields_json: JSON with field mappings. Format:
                {"fields": [
                    {"sheet": "SOFP-CuNonCu", "field_label": "Lease liabilities", "section": "non-current liabilities", "col": 2, "value": 160404, "evidence": "Page 35, Non-current lease liabilities"},
                    {"sheet": "SOFP-CuNonCu", "field_label": "Lease liabilities", "section": "current liabilities", "col": 2, "value": 36148, "evidence": "Page 35, Current lease liabilities"}
                ]}
                - field_label: the line item name (matched against column A in the template)
                - section: which template section (e.g. "current assets", "non-current liabilities").
                  Required when a label appears multiple times in the template.
                - col: 2 for current year (CY), 3 for prior year (PY)
                - value: the numeric value to write
                - evidence: "Page X, <description>" — source page and line item for human review
                Only write to data-entry cells. Never write to formula cells.
        """
        output_path = str(Path(ctx.deps.output_dir) / "filled.xlsx")
        # Use previously filled workbook if it exists, so incremental fills accumulate
        source_path = ctx.deps.filled_path if ctx.deps.filled_path and Path(ctx.deps.filled_path).exists() else ctx.deps.template_path
        result = _fill_workbook_impl(
            template_path=source_path,
            output_path=output_path,
            fields_json=fields_json,
        )
        if result.success:
            ctx.deps.filled_path = output_path

        if result.success:
            return (
                f"Successfully wrote {result.fields_written} fields to {output_path}.\n"
                f"Errors: {result.errors}"
                if result.errors
                else "No errors."
            )
        else:
            return f"Failed to fill workbook. Errors: {result.errors}"

    @agent.tool
    def verify_totals(ctx: RunContext[AgentDeps]) -> str:
        filled_path = ctx.deps.filled_path
        if not filled_path:
            filled_path = str(Path(ctx.deps.output_dir) / "filled.xlsx")
        result = _verify_totals_impl(filled_path)

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
    def save_result(ctx: RunContext[AgentDeps], fields_json: str) -> str:
        fields = json.loads(fields_json)
        json_path = Path(ctx.deps.output_dir) / "result.json"
        json_path.write_text(json.dumps(fields, indent=2), encoding="utf-8")

        report = ctx.deps.token_report.format_table()
        report_path = Path(ctx.deps.output_dir) / "cost_report.txt"
        report_path.write_text(report, encoding="utf-8")

        return f"Results saved to {json_path}\nCost report saved to {report_path}\n\n{report}"

    return agent, deps


def _summarize_template(fields: list[TemplateField]) -> str:
    sheets = {}
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
