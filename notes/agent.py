"""Notes agent factory — analogous to extraction.agent.create_extraction_agent.

One agent per notes template. Reuses the shared PDF-viewer and template
reader; adds a notes-specific write tool that accepts NotesPayload JSON
and lands rows through `notes.writer.write_notes_workbook`.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Union

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from notes.payload import NotesPayload
from notes.writer import write_notes_workbook
from notes_types import (
    NOTES_REGISTRY,
    NotesTemplateType,
    notes_template_path,
)
from scout.notes_discoverer import NoteInventoryEntry
from token_tracker import TokenReport
from tools.pdf_viewer import count_pdf_pages, render_pages_to_png_bytes
from tools.template_reader import TemplateField, read_template as _read_template_impl

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

_TEMPLATE_PROMPT_FILES: dict[NotesTemplateType, str] = {
    NotesTemplateType.CORP_INFO: "notes_corporate_info.md",
    NotesTemplateType.ACC_POLICIES: "notes_accounting_policies.md",
    NotesTemplateType.LIST_OF_NOTES: "notes_listofnotes.md",
    NotesTemplateType.ISSUED_CAPITAL: "notes_issued_capital.md",
    NotesTemplateType.RELATED_PARTY: "notes_related_party.md",
}


def _load_prompt(filename: str) -> str:
    return (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


def _render_inventory_preview(inventory: list[NoteInventoryEntry]) -> str:
    if not inventory:
        return (
            "No notes inventory was provided. Use view_pdf_pages to locate "
            "the notes section and identify relevant notes yourself."
        )
    lines = [f"Scout identified {len(inventory)} notes in the PDF:"]
    for e in inventory:
        start, end = e.page_range
        pages = f"p.{start}" if start == end else f"pp.{start}-{end}"
        lines.append(f"  Note {e.note_num}: {e.title} ({pages})")
    return "\n".join(lines)


def _render_column_rules(filing_level: str) -> str:
    if filing_level == "group":
        return (
            "=== COLUMN RULES (Group filing) ===\n"
            "- Prose rows: write `content` — the writer places it in col B "
            "(Group CY). Leave col C / D / E empty for prose.\n"
            "- Numeric rows (Sheets 13, 14): provide `numeric_values` with "
            "keys `group_cy`, `group_py`, `company_cy`, `company_py`. The "
            "writer fills cols B, C, D, E respectively.\n"
            "- Evidence always lands in col F."
        )
    return (
        "=== COLUMN RULES (Company filing) ===\n"
        "- Prose rows: write `content` — the writer places it in col B.\n"
        "- Numeric rows: provide `numeric_values` with `company_cy` and "
        "`company_py` (or the generic `cy` / `py` aliases).\n"
        "- Evidence always lands in col D."
    )


def render_notes_prompt(
    template_type: NotesTemplateType,
    filing_level: str,
    inventory: list[NoteInventoryEntry],
) -> str:
    """Compose the system prompt for a notes agent."""
    base = _load_prompt("_notes_base.md")
    try:
        specific = _load_prompt(_TEMPLATE_PROMPT_FILES[template_type])
    except FileNotFoundError:
        specific = f"=== TASK: {template_type.value} ===\nNo per-template prompt defined yet."

    entry = NOTES_REGISTRY[template_type]
    sheet_line = (
        f"=== TARGET ===\n"
        f"Template: {entry.template_filename}\n"
        f"Sheet:    {entry.sheet_name}\n"
        f"Filing level: {filing_level}"
    )

    parts = [
        base,
        sheet_line,
        _render_column_rules(filing_level),
        specific,
        "=== INVENTORY ===\n" + _render_inventory_preview(inventory),
    ]
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Deps
# ---------------------------------------------------------------------------

@dataclass
class NotesDeps:
    pdf_path: str
    template_path: str
    model: Any
    output_dir: str
    token_report: TokenReport
    template_type: NotesTemplateType
    sheet_name: str
    filing_level: str
    inventory: list[NoteInventoryEntry] = field(default_factory=list)
    # Mutable runtime state
    template_fields: list[TemplateField] = field(default_factory=list)
    pdf_page_count: int = 0
    filled_path: str = ""
    filled_filename: str = ""


def _render_single_page(pdf_path: str, page_num: int, dpi: int = 200) -> tuple[int, bytes]:
    images = render_pages_to_png_bytes(pdf_path, start=page_num, end=page_num, dpi=dpi)
    return page_num, images[0]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_notes_agent(
    template_type: NotesTemplateType,
    pdf_path: str,
    inventory: list[NoteInventoryEntry],
    filing_level: str,
    model: Union[str, Model],
    output_dir: Optional[str] = None,
) -> tuple[Agent[NotesDeps, str], NotesDeps]:
    """Create a notes agent for a single template type."""
    if output_dir is None:
        output_dir = str(Path(__file__).resolve().parent.parent / "output")

    entry = NOTES_REGISTRY[template_type]
    template_path_str = str(notes_template_path(template_type, level=filing_level))
    filled_filename = f"NOTES_{template_type.value}_filled.xlsx"

    deps = NotesDeps(
        pdf_path=pdf_path,
        template_path=template_path_str,
        model=model,
        output_dir=output_dir,
        token_report=TokenReport(model=model),
        template_type=template_type,
        sheet_name=entry.sheet_name,
        filing_level=filing_level,
        inventory=list(inventory),
        filled_filename=filled_filename,
    )

    system_prompt = render_notes_prompt(
        template_type=template_type,
        filing_level=filing_level,
        inventory=inventory,
    )

    # Pin temperature=1.0 (CLAUDE.md gotcha #5).
    agent = Agent(
        model,
        deps_type=NotesDeps,
        system_prompt=system_prompt,
        model_settings=ModelSettings(temperature=1.0),
    )

    # --- Tools ---

    @agent.tool
    def read_template(ctx: RunContext[NotesDeps]) -> str:
        """Read the template row labels. Cached after the first call."""
        if not ctx.deps.template_fields:
            ctx.deps.template_fields = _read_template_impl(ctx.deps.template_path)
        # Return a compact label list keyed by row — the agent only cares
        # about the col-A labels it may target.
        lines = []
        for f in ctx.deps.template_fields:
            if f.sheet != ctx.deps.sheet_name:
                continue
            if f.col != 1 or not f.value:
                continue
            lines.append(f"  row {f.row:>3}: {f.value}")
        return f"Sheet: {ctx.deps.sheet_name}\nLabels (col A):\n" + "\n".join(lines)

    @agent.tool
    def view_pdf_pages(
        ctx: RunContext[NotesDeps], pages: List[int],
    ) -> List[Union[str, BinaryContent]]:
        """Render PDF pages to images. Pass a list of 1-indexed page numbers."""
        ctx.deps.pdf_page_count = count_pdf_pages(ctx.deps.pdf_path)
        total = ctx.deps.pdf_page_count
        requested = [p for p in pages if isinstance(p, int)]
        invalid = sorted({p for p in requested if p < 1 or p > total})
        render_pages = sorted(set(p for p in requested if p not in invalid))

        results: List[Union[str, BinaryContent]] = []
        if invalid:
            results.append(
                f"Skipped invalid page(s) {invalid}. Valid range is 1-{total}."
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
                pn, png = future.result()
                rendered[pn] = png

        for pn in sorted(rendered):
            results.append(f"=== Page {pn} ===")
            results.append(BinaryContent(data=rendered[pn], media_type="image/png"))
        return results

    @agent.tool
    def write_notes(ctx: RunContext[NotesDeps], payloads_json: str) -> str:
        """Write a batch of NotesPayload entries to this template's sheet.

        Args:
            payloads_json: JSON with either {"payloads": [...]} or a bare
                list of payload objects. Each object needs chosen_row_label,
                content (or numeric_values), evidence, and source_pages.
        """
        try:
            parsed = json.loads(payloads_json)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"

        items = parsed["payloads"] if isinstance(parsed, dict) and "payloads" in parsed else parsed
        if not isinstance(items, list):
            return 'Expected a list of payloads or {"payloads": [...]}'

        payloads: list[NotesPayload] = []
        errors: list[str] = []
        for raw in items:
            try:
                payloads.append(NotesPayload(
                    chosen_row_label=raw["chosen_row_label"],
                    content=raw.get("content", "") or "",
                    evidence=raw.get("evidence", "") or "",
                    source_pages=[int(p) for p in raw.get("source_pages", []) or []],
                    numeric_values=raw.get("numeric_values"),
                ))
            except (KeyError, ValueError) as e:
                errors.append(f"Invalid payload {raw}: {e}")

        output_path = str(Path(ctx.deps.output_dir) / ctx.deps.filled_filename)
        # Use already-filled workbook if we've written once before; otherwise
        # start from the pristine template.
        source_path = (
            ctx.deps.filled_path
            if ctx.deps.filled_path and Path(ctx.deps.filled_path).exists()
            else ctx.deps.template_path
        )
        result = write_notes_workbook(
            template_path=source_path,
            payloads=payloads,
            output_path=output_path,
            filing_level=ctx.deps.filing_level,
            sheet_name=ctx.deps.sheet_name,
        )
        if result.success:
            ctx.deps.filled_path = output_path

        msg = (
            f"Wrote {result.rows_written} row(s) to "
            f"{ctx.deps.sheet_name}."
        )
        if errors:
            msg += "\nParse errors: " + "; ".join(errors)
        if result.errors:
            msg += "\nWriter errors: " + "; ".join(result.errors)
        return msg

    @agent.tool
    def save_result(ctx: RunContext[NotesDeps], payloads_json: str) -> str:
        """Persist the final payload list + token report to the output dir."""
        try:
            parsed = json.loads(payloads_json)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"
        prefix = f"NOTES_{ctx.deps.template_type.value}"
        json_path = Path(ctx.deps.output_dir) / f"{prefix}_result.json"
        json_path.write_text(
            json.dumps(parsed, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        report = ctx.deps.token_report.format_table()
        report_path = Path(ctx.deps.output_dir) / f"{prefix}_cost_report.txt"
        report_path.write_text(report, encoding="utf-8")
        return f"Saved {json_path.name}\n{report}"

    return agent, deps
