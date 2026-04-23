"""Notes post-validator agent factory (Phase 5).

Runs once after the notes pass + merge, with access to the MERGED
workbook (so it can see Sheet 11 and Sheet 12 together). Its job is to
detect and resolve cross-sheet duplication between "Material Accounting
Policies" (Sheet 11) and "List of Notes" (Sheet 12).

Tools:
    - view_pdf_pages — same tool the face agents use
    - read_cell       — read current contents of a cell in the merged wb
    - rewrite_cell    — overwrite a data-entry cell (or clear it)
    - flag_duplication — record an agent note for the audit log

The detection of candidate duplicates is done in Python (Step 5.3 / 5.4)
and the list is injected into the agent prompt. The agent applies the
"Material Accounting Policies → Sheet 11, otherwise → Sheet 12" rule
itself (per PLAN D3) using the PDF as the source of truth.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, List, Optional, Union

import openpyxl
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from notes.writer import payload_sidecar_path
from tools.pdf_viewer import count_pdf_pages, render_pages_to_png_bytes

logger = logging.getLogger(__name__)

# Char shingle size + Jaccard threshold for the content-overlap fallback
# used when a payload has no source_note_refs. Tuned high enough to avoid
# flagging "income tax" showing up in both the policy and a disclosure,
# low enough to catch a pasted paragraph appearing on both sheets.
_SHINGLE_SIZE = 5
_OVERLAP_THRESHOLD = 0.5

# Defense-in-depth: the validator is chartered to reconcile cross-sheet
# duplication between Sheet 11 (policies) and Sheet 12 (list of notes).
# It must never touch face-statement sheets or other notes sheets —
# an agent confusion could otherwise overwrite numeric cells with prose.
# Prompt says "never write prose to Sheet 13 or 14"; this allowlist
# enforces it in code so a mis-targeted rewrite fails loudly.
_REWRITE_ALLOWED_SHEETS: frozenset[str] = frozenset({
    "Notes-SummaryofAccPol",
    "Notes-Listofnotes",
})


class NotesValidatorAgentDeps:
    """Dependencies carried through the post-validator's tool calls."""

    def __init__(
        self,
        merged_workbook_path: str,
        pdf_path: str,
        sidecar_paths: List[str],
        filing_level: str,
        filing_standard: str,
        output_dir: str,
        model: Any,
    ):
        self.merged_workbook_path = merged_workbook_path
        self.pdf_path = pdf_path
        self.sidecar_paths = list(sidecar_paths)
        self.filing_level = filing_level
        self.filing_standard = filing_standard
        self.output_dir = output_dir
        self.model = model
        self.pdf_page_count = 0
        # Agent-recorded corrections + rationales so operators can audit
        # what the validator chose to do. One file lands at
        # `notes_validator_log.json` next to the merged workbook.
        self.correction_log: list[dict] = []
        self.writes_performed = 0


# ---------------------------------------------------------------------------
# Pure detection helpers (exported for unit testing)
# ---------------------------------------------------------------------------

def load_sidecar_entries(sidecar_paths: List[str]) -> list[dict]:
    """Concatenate per-template sidecar JSON files into a flat list.

    Missing or malformed sidecars are skipped with a warning. The return
    shape is `[{"sheet": str, "row": int, "col": int, "source_note_refs":
    list[str], "content_preview": str}, ...]`.
    """
    out: list[dict] = []
    for path in sidecar_paths:
        p = Path(path)
        if not p.exists():
            logger.warning("Sidecar missing: %s", p)
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Sidecar unreadable: %s", p, exc_info=True)
            continue
        if isinstance(data, list):
            out.extend(e for e in data if isinstance(e, dict))
    return out


def detect_cross_sheet_duplicates_by_ref(
    entries: list[dict],
    sheet_11: str = "Notes-SummaryofAccPol",
    sheet_12: str = "Notes-Listofnotes",
) -> list[dict]:
    """Return sidecar entries that share a `source_note_refs` value across
    Sheet 11 and Sheet 12.

    Each duplicate result has shape:
      {"note_ref": str, "sheet_11": entry, "sheet_12": entry}

    Skips entries with empty `source_note_refs`; use
    `detect_cross_sheet_overlap_candidates` for that fallback.
    """
    by_ref_sheet: dict[str, dict[str, list[dict]]] = {}
    for e in entries:
        sheet = e.get("sheet", "")
        refs = e.get("source_note_refs") or []
        for ref in refs:
            by_ref_sheet.setdefault(ref, {}).setdefault(sheet, []).append(e)

    duplicates: list[dict] = []
    for ref, by_sheet in by_ref_sheet.items():
        s11 = by_sheet.get(sheet_11, [])
        s12 = by_sheet.get(sheet_12, [])
        if s11 and s12:
            for a in s11:
                for b in s12:
                    duplicates.append({
                        "note_ref": ref,
                        "sheet_11": a,
                        "sheet_12": b,
                    })
    return duplicates


def _char_shingles(text: str, n: int = _SHINGLE_SIZE) -> set[str]:
    """Return normalised character n-grams for a Jaccard overlap check."""
    normalised = " ".join(text.lower().split())
    if len(normalised) < n:
        return {normalised} if normalised else set()
    return {normalised[i : i + n] for i in range(len(normalised) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def detect_cross_sheet_overlap_candidates(
    entries: list[dict],
    sheet_11: str = "Notes-SummaryofAccPol",
    sheet_12: str = "Notes-Listofnotes",
    threshold: float = _OVERLAP_THRESHOLD,
) -> list[dict]:
    """Fallback: detect cross-sheet candidates when `source_note_refs` is
    absent on one or both sides.

    Uses char n-gram Jaccard over the sidecar `content_preview` fields.
    Only flags pairs where at least one side has no `source_note_refs`
    (ref-based detection is always preferred when data is available).
    """
    s11 = [e for e in entries if e.get("sheet") == sheet_11]
    s12 = [e for e in entries if e.get("sheet") == sheet_12]
    candidates: list[dict] = []
    for a in s11:
        a_refs = set(a.get("source_note_refs") or [])
        sh_a = _char_shingles(a.get("content_preview", "") or "")
        for b in s12:
            b_refs = set(b.get("source_note_refs") or [])
            # Skip the ref-based detection's territory.
            if a_refs & b_refs:
                continue
            sh_b = _char_shingles(b.get("content_preview", "") or "")
            score = _jaccard(sh_a, sh_b)
            if score >= threshold:
                candidates.append({
                    "score": round(score, 3),
                    "sheet_11": a,
                    "sheet_12": b,
                })
    return candidates


_SHEET_11_TAB = "Notes-SummaryofAccPol"
_SHEET_12_TAB = "Notes-Listofnotes"


def build_validator_prompt_body(
    duplicates: list[dict],
    overlap_candidates: list[dict],
    filing_level: str,
    filing_standard: str,
) -> str:
    """Render the dynamic portion of the validator prompt — the list of
    candidates the agent must resolve. Surfaces the literal worksheet tab
    names (`Notes-SummaryofAccPol`, `Notes-Listofnotes`) so the agent's
    first `read_cell` / `rewrite_cell` call uses the real sheet names
    `_rewrite_cell_impl` will accept (peer-review Codex P2)."""
    lines: list[str] = [
        "=== CROSS-SHEET DUPLICATE CANDIDATES ===",
        "",
        f"filing_level: {filing_level}",
        f"filing_standard: {filing_standard}",
        "",
        "Worksheet tab names to pass to read_cell / rewrite_cell:",
        f"  • Sheet 11 → {_SHEET_11_TAB!r}",
        f"  • Sheet 12 → {_SHEET_12_TAB!r}",
        "",
    ]
    if not duplicates and not overlap_candidates:
        lines.append(
            "No candidates — no ref-based duplicates and no overlap fallback "
            "hits. You may exit immediately with a short confirmation."
        )
        return "\n".join(lines)

    def _fmt_pages(entry: dict) -> str:
        pages = entry.get("source_pages") or []
        if not pages:
            return "pages: (none — use scout hints or scan the PDF)"
        return f"pages: {sorted(set(pages))}"

    if duplicates:
        lines.append(
            "REF-BASED DUPLICATES (same source_note_refs value on both sheets):"
        )
        for d in duplicates:
            a = d["sheet_11"]
            b = d["sheet_12"]
            lines.append(
                f"  • Note ref {d['note_ref']!r}: "
                f"Sheet 11 ({_SHEET_11_TAB!r}) row {a.get('row')} vs "
                f"Sheet 12 ({_SHEET_12_TAB!r}) row {b.get('row')}"
            )
            lines.append(f"    Sheet 11 {_fmt_pages(a)}")
            lines.append(
                f"    Sheet 11 preview: {a.get('content_preview', '')!r}"
            )
            lines.append(f"    Sheet 12 {_fmt_pages(b)}")
            lines.append(
                f"    Sheet 12 preview: {b.get('content_preview', '')!r}"
            )
        lines.append("")

    if overlap_candidates:
        lines.append(
            "OVERLAP FALLBACK (content-similarity candidates without matching "
            "refs — treat as probable duplicates and verify against the PDF):"
        )
        for c in overlap_candidates:
            a = c["sheet_11"]
            b = c["sheet_12"]
            lines.append(
                f"  • Score {c['score']}: "
                f"Sheet 11 ({_SHEET_11_TAB!r}) row {a.get('row')} vs "
                f"Sheet 12 ({_SHEET_12_TAB!r}) row {b.get('row')}"
            )
            lines.append(f"    Sheet 11 {_fmt_pages(a)}")
            lines.append(
                f"    Sheet 11 preview: {a.get('content_preview', '')!r}"
            )
            lines.append(f"    Sheet 12 {_fmt_pages(b)}")
            lines.append(
                f"    Sheet 12 preview: {b.get('content_preview', '')!r}"
            )
        lines.append("")

    lines.append(
        "For each candidate, view the relevant PDF pages and apply the "
        "heading rule: headings like 'Material Accounting Policies', "
        "'Significant Accounting Policies', or 'Summary of Material "
        "Accounting Policies' belong on Sheet 11; everything else belongs "
        "on Sheet 12. Rewrite the wrong cell (set content to empty string "
        f"to delete) — pass sheet={_SHEET_11_TAB!r} or {_SHEET_12_TAB!r} "
        "verbatim — then call `flag_duplication` to record your reasoning."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "notes_validator.md"


def _render_single_page(pdf_path: str, page_num: int, dpi: int = 200):
    images = render_pages_to_png_bytes(pdf_path, start=page_num, end=page_num, dpi=dpi)
    return page_num, images[0]


def create_notes_validator_agent(
    merged_workbook_path: str,
    pdf_path: str,
    sidecar_paths: List[str],
    filing_level: str,
    filing_standard: str,
    model: Union[str, Model],
    output_dir: str,
) -> tuple[Agent[NotesValidatorAgentDeps, str], NotesValidatorAgentDeps, dict]:
    """Build the notes post-validator agent.

    Returns (agent, deps, context) where `context` is a dict describing
    what the detectors found before the run — useful for test assertions
    and for the coordinator to short-circuit when there are no candidates.
    """
    deps = NotesValidatorAgentDeps(
        merged_workbook_path=merged_workbook_path,
        pdf_path=pdf_path,
        sidecar_paths=sidecar_paths,
        filing_level=filing_level,
        filing_standard=filing_standard,
        output_dir=output_dir,
        model=model,
    )

    entries = load_sidecar_entries(sidecar_paths)
    duplicates = detect_cross_sheet_duplicates_by_ref(entries)
    overlap = detect_cross_sheet_overlap_candidates(entries)

    base_prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    dynamic_prompt = build_validator_prompt_body(
        duplicates, overlap, filing_level, filing_standard,
    )
    system_prompt = f"{base_prompt}\n\n{dynamic_prompt}"

    agent = Agent(
        model,
        deps_type=NotesValidatorAgentDeps,
        system_prompt=system_prompt,
        model_settings=ModelSettings(temperature=1.0),
    )

    @agent.tool
    def view_pdf_pages(
        ctx: RunContext[NotesValidatorAgentDeps], pages: List[int],
    ) -> List[Union[str, BinaryContent]]:
        """View specific PDF pages as images."""
        ctx.deps.pdf_page_count = count_pdf_pages(ctx.deps.pdf_path)
        total_pages = ctx.deps.pdf_page_count
        requested = [p for p in pages if isinstance(p, int)]
        invalid = sorted({p for p in requested if p < 1 or p > total_pages})
        render_pages = sorted(set(p for p in requested if p not in invalid))

        results: List[Union[str, BinaryContent]] = []
        if invalid:
            results.append(
                f"Skipped invalid page(s) {invalid}. Valid range is 1-{total_pages}."
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
    def read_cell(
        ctx: RunContext[NotesValidatorAgentDeps], sheet: str, row: int, col: int,
    ) -> str:
        """Read the current value of a merged-workbook cell."""
        wb = openpyxl.load_workbook(ctx.deps.merged_workbook_path)
        try:
            if sheet not in wb.sheetnames:
                return f"Sheet {sheet!r} not found. Available: {wb.sheetnames}"
            ws = wb[sheet]
            value = ws.cell(row=row, column=col).value
            return json.dumps({"sheet": sheet, "row": row, "col": col,
                               "value": value})
        finally:
            wb.close()

    @agent.tool
    def rewrite_cell(
        ctx: RunContext[NotesValidatorAgentDeps],
        sheet: str,
        row: int,
        col: int,
        content: str,
        evidence: Optional[str] = None,
    ) -> str:
        """Overwrite a data-entry cell in the merged workbook.

        Pass `content=""` to delete. Refuses to write to formula cells
        — those are pure template scaffolding and should never be
        touched by the validator.
        """
        return _rewrite_cell_impl(
            merged_workbook_path=ctx.deps.merged_workbook_path,
            filing_level=ctx.deps.filing_level,
            sheet=sheet,
            row=row,
            col=col,
            content=content,
            evidence=evidence,
            deps=ctx.deps,
        )

    @agent.tool
    def flag_duplication(
        ctx: RunContext[NotesValidatorAgentDeps],
        note_ref: str,
        decision: str,
        rationale: str,
    ) -> str:
        """Record that a cross-sheet duplicate has been resolved.

        ``decision`` should be one of: "kept_on_sheet_11", "kept_on_sheet_12",
        "deleted_from_both", "no_action". ``rationale`` is the agent's
        free-text explanation of its reasoning — stored for audit.
        """
        ctx.deps.correction_log.append({
            "note_ref": note_ref,
            "decision": decision,
            "rationale": rationale,
        })
        return f"Recorded decision for note {note_ref}: {decision}"

    context = {
        "duplicates": duplicates,
        "overlap_candidates": overlap,
        "entry_count": len(entries),
    }
    return agent, deps, context


def _rewrite_cell_impl(
    merged_workbook_path: str,
    filing_level: str,
    sheet: str,
    row: int,
    col: int,
    content: str,
    evidence: Optional[str],
    deps: NotesValidatorAgentDeps,
) -> str:
    """Inner implementation so tests can exercise the tool without standing
    up a full RunContext. Also clears the evidence column when content
    becomes empty, so a deletion leaves no orphan citation behind."""
    from notes.writer import evidence_col_for

    # Defense-in-depth: refuse any sheet outside the validator's charter.
    # The prompt restricts the agent to Sheet 11 + Sheet 12, but a
    # hallucinated sheet name should fail loudly rather than corrupt
    # a face statement or a numeric notes sheet.
    if sheet not in _REWRITE_ALLOWED_SHEETS:
        return (
            f"Refusing to rewrite out-of-scope sheet {sheet!r} — "
            f"validator is restricted to "
            f"{sorted(_REWRITE_ALLOWED_SHEETS)}"
        )

    wb = openpyxl.load_workbook(merged_workbook_path)
    try:
        if sheet not in wb.sheetnames:
            return f"Sheet {sheet!r} not found"
        ws = wb[sheet]
        target = ws.cell(row=row, column=col)
        if isinstance(target.value, str) and target.value.startswith("="):
            return (
                f"Refusing to overwrite formula cell "
                f"{target.coordinate} (formula: {target.value!r})"
            )
        # Empty string => explicit deletion (plan Step 5.2).
        target.value = content if content else None

        # Sync the evidence column. On deletion we always clear so the
        # audit trail can't keep a citation pointing at nothing. On an
        # update we apply the supplied evidence (if any) or leave the
        # existing value untouched.
        ev_col = evidence_col_for(filing_level)
        if col == 2:  # only sync when we're rewriting the primary value col
            ev_cell = ws.cell(row=row, column=ev_col)
            if not content:
                ev_cell.value = None
            elif evidence is not None:
                ev_cell.value = evidence
        wb.save(merged_workbook_path)
        deps.writes_performed += 1
    finally:
        wb.close()

    deps.correction_log.append({
        "operation": "rewrite_cell",
        "sheet": sheet,
        "row": row,
        "col": col,
        "deleted": not content,
        "evidence": evidence,
    })
    action = "cleared" if not content else "wrote"
    return f"OK — {action} {sheet}!R{row}C{col}"
