from __future__ import annotations

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


## create_sofp_agent was removed in Phase 11.3 — use
## extraction.agent.create_extraction_agent() via the coordinator instead.


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
