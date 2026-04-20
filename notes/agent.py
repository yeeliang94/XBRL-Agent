"""Notes agent factory — analogous to extraction.agent.create_extraction_agent.

One agent per notes template. Reuses the shared PDF-viewer and template
reader; adds a notes-specific write tool that accepts NotesPayload JSON
and lands rows through `notes.writer.write_notes_workbook`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Union

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from notes.payload import NotesPayload
from notes.writer import evidence_col_letter, write_notes_workbook
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


# Fallback rendered when prompts/_notes_base.md is missing. Keeps the
# agent functional (and loudly visible in the system prompt) instead of
# crashing the whole pipeline on a misdeployment. Not expected to be hit
# in a healthy repo — the real file is under version control.
_BASE_PROMPT_FALLBACK = (
    "You are a notes-filling agent. The shared base prompt "
    "(prompts/_notes_base.md) is missing from this deployment; "
    "follow the per-template task section below and emit payloads "
    "with evidence."
)


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


def _render_page_hints_block(page_hints: list[int]) -> Optional[str]:
    """Render a SUGGESTED-STARTING-PAGES block for the system prompt.

    Used when scout couldn't build a full notes inventory (typical for
    scanned PDFs where PyMuPDF returns empty text). The hints come from
    the face-statement scout scores — each face_page + note_pages union.
    Rendered as "start here" guidance, NOT a hard restriction: the agent
    is still allowed to open any page via view_pdf_pages. We explicitly
    tell the agent not to blind-sweep pages 1-N when a hint block is
    present, because that sweep was the single biggest runtime cost we
    observed in production runs (33+ pages rendered for 15 output rows).
    """
    if not page_hints:
        return None
    pages_str = ", ".join(str(p) for p in page_hints)
    return (
        "=== SUGGESTED STARTING PAGES ===\n"
        f"Scout identified these PDF pages as likely containing face "
        f"statements and note references: {pages_str}.\n"
        "Start with view_pdf_pages on these pages (in small batches of "
        "3-5 at a time) before exploring elsewhere. Do NOT sweep the "
        "document from page 1; target the neighbourhoods around these "
        "hints first and only expand if the content isn't found."
    )


def _render_column_rules(filing_level: str) -> str:
    ev = evidence_col_letter(filing_level)
    if filing_level == "group":
        return (
            "=== COLUMN RULES (Group filing) ===\n"
            "- Prose rows: write `content` -- the writer places it in col B "
            "(Group CY). Leave col C / D / E empty for prose.\n"
            "- Numeric rows (Sheets 13, 14): provide `numeric_values` with "
            "keys `group_cy`, `group_py`, `company_cy`, `company_py`. The "
            "writer fills cols B, C, D, E respectively.\n"
            f"- Evidence always lands in col {ev}."
        )
    return (
        "=== COLUMN RULES (Company filing) ===\n"
        "- Prose rows: write `content` -- the writer places it in col B.\n"
        "- Numeric rows: provide `numeric_values` with `company_cy` and "
        "`company_py` (or the generic `cy` / `py` aliases).\n"
        f"- Evidence always lands in col {ev}."
    )


def render_notes_prompt(
    template_type: NotesTemplateType,
    filing_level: str,
    inventory: list[NoteInventoryEntry],
    page_hints: Optional[list[int]] = None,
) -> str:
    """Compose the system prompt for a notes agent.

    ``page_hints`` is a sorted unique list of PDF pages the face-statement
    scout already identified as note-bearing. When the inventory is empty
    (scanned PDFs), these hints are the agent's only signal for where to
    start looking — without them it falls back to scanning page 1 onward.
    """
    try:
        base = _load_prompt("_notes_base.md")
    except FileNotFoundError:
        logger.error("prompts/_notes_base.md missing -- using fallback")
        base = _BASE_PROMPT_FALLBACK
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
    # Hints are orthogonal to the inventory — both may be present, and
    # the agent treats them as complementary (inventory = what notes
    # exist; hints = where those notes likely live). Emit hints last
    # so they stay fresh in the prompt's tail, where LLMs tend to
    # weight instructions more heavily.
    hints_block = _render_page_hints_block(page_hints or [])
    if hints_block is not None:
        parts.append(hints_block)
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
    # True once this run has landed at least one successful write. Gates
    # the "reuse the filled workbook as the source for subsequent writes"
    # logic so a stale `filled.xlsx` from an earlier run in the same
    # output_dir doesn't get layered on top of.
    wrote_once: bool = False
    # Sheet-12 sub-agent mode: when set, write_notes appends to this list
    # instead of writing a workbook, and save_result is a no-op. The
    # sub-coordinator owns the final aggregation + workbook write.
    payload_sink: Optional[list] = None
    sub_agent_id: Optional[str] = None
    # Per-sheet write diagnostics accumulated across every write_notes
    # invocation — the agent may call the tool multiple times and we want
    # the UNION of skip-errors and fuzzy matches, not just the last call's.
    # Peer-review [HIGH]: the coordinator reads these into
    # ``NotesAgentResult.warnings`` for single-sheet templates so partial
    # or dirty successes don't masquerade as clean successes.
    write_skip_errors: list[str] = field(default_factory=list)
    # (requested_label, chosen_label, score) — only entries where score < 1.0
    write_fuzzy_matches: list[tuple[str, str, float]] = field(default_factory=list)


def _render_single_page(pdf_path: str, page_num: int, dpi: int = 200) -> tuple[int, bytes]:
    images = render_pages_to_png_bytes(pdf_path, start=page_num, end=page_num, dpi=dpi)
    return page_num, images[0]


async def _render_pages_async(pdf_path: str, pages: list[int]) -> dict[int, bytes]:
    """Render pages concurrently without blocking the event loop.

    Uses `asyncio.to_thread` (default thread pool) instead of a per-call
    `ThreadPoolExecutor`, which both avoids the per-call thread spin-up
    and keeps the rendering truly non-blocking from the coordinator's
    perspective.
    """
    async def _one(pn: int) -> tuple[int, bytes]:
        return await asyncio.to_thread(_render_single_page, pdf_path, pn)

    rendered: dict[int, bytes] = {}
    for coro in asyncio.as_completed([_one(pn) for pn in pages]):
        pn, png = await coro
        rendered[pn] = png
    return rendered


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
    page_hints: Optional[list[int]] = None,
) -> tuple[Agent[NotesDeps, str], NotesDeps]:
    """Create a notes agent for a single template type.

    ``page_hints`` — optional list of 1-indexed PDF pages derived from
    scout's face-statement refs. Passed through to the system prompt so
    the agent starts looking near the relevant pages instead of sweeping
    the whole document, which is especially important on scanned PDFs
    where scout's deterministic inventory builder yields nothing.
    """
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
        page_hints=page_hints,
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
    async def read_template(ctx: RunContext[NotesDeps]) -> str:
        """Read the template row labels. Cached after the first call."""
        if not ctx.deps.template_fields:
            # openpyxl load is synchronous and slow enough to block other
            # sub-agents running on the same event loop; off-thread it.
            ctx.deps.template_fields = await asyncio.to_thread(
                _read_template_impl, ctx.deps.template_path,
            )
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
    async def view_pdf_pages(
        ctx: RunContext[NotesDeps], pages: List[int],
    ) -> List[Union[str, BinaryContent]]:
        """Render PDF pages to images. Pass a list of 1-indexed page numbers."""
        if ctx.deps.pdf_page_count == 0:
            ctx.deps.pdf_page_count = await asyncio.to_thread(
                count_pdf_pages, ctx.deps.pdf_path,
            )
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

        rendered = await _render_pages_async(ctx.deps.pdf_path, render_pages)

        for pn in sorted(rendered):
            results.append(f"=== Page {pn} ===")
            results.append(BinaryContent(data=rendered[pn], media_type="image/png"))
        return results

    @agent.tool
    async def write_notes(ctx: RunContext[NotesDeps], payloads_json: str) -> str:
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
            # Guard first so a non-dict entry (model hallucinated a string
            # instead of an object) is reported as a parse error rather
            # than crashing the whole tool with TypeError.
            if not isinstance(raw, dict):
                errors.append(f"Invalid payload (expected object, got {type(raw).__name__}): {raw!r}")
                continue
            try:
                payloads.append(NotesPayload(
                    chosen_row_label=raw["chosen_row_label"],
                    content=raw.get("content", "") or "",
                    evidence=raw.get("evidence", "") or "",
                    source_pages=[int(p) for p in raw.get("source_pages", []) or []],
                    numeric_values=raw.get("numeric_values"),
                    sub_agent_id=ctx.deps.sub_agent_id,
                ))
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                errors.append(f"Invalid payload {raw!r}: {e}")

        # Sub-agent mode: hand payloads to the sub-coordinator and skip the
        # workbook write. The sub-coordinator aggregates across sub-agents
        # (including row-112 unmatched concatenation) and does one final
        # write through notes.writer.write_notes_workbook.
        if ctx.deps.payload_sink is not None:
            ctx.deps.payload_sink.extend(payloads)
            msg = f"Collected {len(payloads)} payload(s) for sub-coordinator."
            if errors:
                msg += "\nParse errors: " + "; ".join(errors)
            return msg

        output_path = str(Path(ctx.deps.output_dir) / ctx.deps.filled_filename)
        # Use already-filled workbook if we've written once in THIS run;
        # otherwise start from the pristine template. The `wrote_once` flag
        # gates the reuse so a stale `filled.xlsx` left in output_dir by a
        # previous run is overwritten on the first write of this run
        # instead of silently layered on top.
        source_path = (
            ctx.deps.filled_path
            if ctx.deps.wrote_once and ctx.deps.filled_path
               and Path(ctx.deps.filled_path).exists()
            else ctx.deps.template_path
        )
        result = await asyncio.to_thread(
            write_notes_workbook,
            template_path=source_path,
            payloads=payloads,
            output_path=output_path,
            filing_level=ctx.deps.filing_level,
            sheet_name=ctx.deps.sheet_name,
        )
        if result.success:
            ctx.deps.filled_path = output_path
            ctx.deps.wrote_once = True

        # Accumulate structured diagnostics so the coordinator can lift
        # them into NotesAgentResult.warnings for history/UI. The tool-
        # result string below covers the model-facing view; this is the
        # machine-readable mirror (peer-review [HIGH]).
        if result.errors:
            ctx.deps.write_skip_errors.extend(result.errors)
        if result.fuzzy_matches:
            ctx.deps.write_fuzzy_matches.extend(result.fuzzy_matches)

        msg = (
            f"Wrote {result.rows_written} row(s) to "
            f"{ctx.deps.sheet_name}."
        )
        if errors:
            msg += "\nParse errors: " + "; ".join(errors)
        if result.errors:
            msg += "\nWriter errors: " + "; ".join(result.errors)
        if result.fuzzy_matches:
            preview = "; ".join(
                f"'{req}'->'{chosen}' ({score:.2f})"
                for req, chosen, score in result.fuzzy_matches[:5]
            )
            more = f" (+{len(result.fuzzy_matches) - 5} more)" if len(result.fuzzy_matches) > 5 else ""
            msg += f"\nFuzzy matches: {preview}{more}"
        return msg

    @agent.tool
    async def save_result(ctx: RunContext[NotesDeps], payloads_json: str) -> str:
        """Persist the final payload list + token report to the output dir."""
        # Sub-agent mode: the sub-coordinator owns final persistence --
        # don't race on NOTES_{type}_result.json file writes.
        if ctx.deps.payload_sink is not None:
            return "Sub-agent mode -- sub-coordinator will persist."
        try:
            parsed = json.loads(payloads_json)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"
        prefix = f"NOTES_{ctx.deps.template_type.value}"
        json_path = Path(ctx.deps.output_dir) / f"{prefix}_result.json"
        report = ctx.deps.token_report.format_table()
        report_path = Path(ctx.deps.output_dir) / f"{prefix}_cost_report.txt"
        await asyncio.to_thread(
            json_path.write_text,
            json.dumps(parsed, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        await asyncio.to_thread(report_path.write_text, report, encoding="utf-8")
        return f"Saved {json_path.name}\n{report}"

    return agent, deps
