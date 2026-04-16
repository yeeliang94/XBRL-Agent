"""Writes a list of NotesPayload entries into an MBRS notes workbook.

Column layout (PLAN Section 2 #6):

  Company filing (4-col template):
    A=label, B=value, C=prior-year-value, D=source/evidence
    - Prose rows  → content to B, evidence to D (C left empty).
    - Numeric rows → values to B (CY) and C (PY), evidence to D.

  Group filing (6-col template):
    A=label, B=Group-CY, C=Group-PY, D=Company-CY, E=Company-PY, F=source
    - Prose rows  → content to B only (C/D/E empty), evidence to F.
    - Numeric rows → 4 values to B/C/D/E per role, evidence to F.

Char-limit guard: Excel caps cells at 32,767 chars. We truncate well below
that and append a footer pointing at the source pages.

Row resolution is fuzzy, label-based (same pattern as tools/fill_workbook.py):
normalise both sides (strip leading '*', lowercase) and exact-match first,
then SequenceMatcher fallback at ~0.7 similarity.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import openpyxl

from notes.payload import NotesPayload

logger = logging.getLogger(__name__)


# Excel's hard limit is 32,767 chars; we keep ~2K of headroom for a footer.
CELL_CHAR_LIMIT = 30_000

# Fuzzy-match threshold for label resolution (mirrors tools/fill_workbook.py).
_FUZZY_THRESHOLD = 0.7


@dataclass
class NotesWriteResult:
    success: bool
    rows_written: int = 0
    output_path: str = ""
    errors: list[str] = field(default_factory=list)


def write_notes_workbook(
    template_path: str,
    payloads: list[NotesPayload],
    output_path: str,
    filing_level: str,
    sheet_name: str,
) -> NotesWriteResult:
    """Write NotesPayload entries to the given sheet of a notes template."""
    tpl = Path(template_path)
    if not tpl.exists():
        return NotesWriteResult(
            success=False,
            output_path="",
            errors=[f"Template not found: {template_path}"],
        )

    wb = openpyxl.load_workbook(template_path)
    if sheet_name not in wb.sheetnames:
        wb.close()
        return NotesWriteResult(
            success=False,
            output_path="",
            errors=[
                f"Sheet '{sheet_name}' not found in template "
                f"(have: {wb.sheetnames})"
            ],
        )

    ws = wb[sheet_name]
    label_index = _build_label_index(ws)

    # Concatenate duplicate labels so Sheet-12 "Disclosure of other notes"
    # can collect multiple unmatched notes into a single cell.
    rows_consumed: dict[int, list[NotesPayload]] = {}
    errors: list[str] = []

    for payload in payloads:
        row = _resolve_row(label_index, payload.chosen_row_label)
        if row is None:
            errors.append(
                f"No matching row for label '{payload.chosen_row_label}' in sheet '{sheet_name}'"
            )
            continue
        rows_consumed.setdefault(row, []).append(payload)

    evidence_col = _evidence_col(filing_level)

    rows_written = 0
    for row, row_payloads in rows_consumed.items():
        combined = _combine_payloads(row_payloads)
        if _write_row(ws, row, combined, filing_level, evidence_col, errors):
            rows_written += 1

    try:
        wb.save(output_path)
    finally:
        wb.close()

    # Success if we wrote anything OR if payloads was empty (caller asked
    # for a no-op write — hand back an untouched copy of the template).
    success = rows_written > 0 or not payloads
    return NotesWriteResult(
        success=success,
        rows_written=rows_written,
        output_path=output_path,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Row resolution
# ---------------------------------------------------------------------------

@dataclass
class _LabelEntry:
    normalized: str
    row: int


def _build_label_index(ws) -> list[_LabelEntry]:
    entries: list[_LabelEntry] = []
    for row in range(1, ws.max_row + 1):
        v = ws.cell(row=row, column=1).value
        if v is None:
            continue
        entries.append(_LabelEntry(normalized=_normalize(str(v)), row=row))
    return entries


def _normalize(s: str) -> str:
    return s.strip().lstrip("*").strip().lower()


def _resolve_row(entries: list[_LabelEntry], label: str) -> Optional[int]:
    target = _normalize(label)
    for e in entries:
        if e.normalized == target:
            return e.row
    # Fuzzy fallback
    best_score = 0.0
    best_row: Optional[int] = None
    for e in entries:
        score = SequenceMatcher(None, target, e.normalized).ratio()
        if score > best_score:
            best_score = score
            best_row = e.row
    if best_score >= _FUZZY_THRESHOLD:
        return best_row
    return None


# ---------------------------------------------------------------------------
# Concatenation + writing
# ---------------------------------------------------------------------------

def _combine_payloads(payloads: list[NotesPayload]) -> NotesPayload:
    """Merge multiple payloads targeting the same row.

    Prose: concatenate content with blank line separators. Evidence is a
    semicolon-joined list. Numeric: last-write-wins (multiple numeric
    payloads for one row is a bug upstream — a warning is logged).
    """
    if len(payloads) == 1:
        return payloads[0]

    # Numeric: warn and take first set of values.
    numeric_values = None
    numeric_payloads = [p for p in payloads if p.numeric_values]
    if numeric_payloads:
        numeric_values = numeric_payloads[0].numeric_values
        if len(numeric_payloads) > 1:
            logger.warning(
                "Multiple numeric payloads for row '%s' — using first",
                payloads[0].chosen_row_label,
            )

    contents = [p.content.strip() for p in payloads if p.content.strip()]
    content = "\n\n".join(contents)

    evidence_parts = [p.evidence.strip() for p in payloads if p.evidence.strip()]
    evidence = "; ".join(evidence_parts)

    all_pages: list[int] = []
    seen: set[int] = set()
    for p in payloads:
        for pg in p.source_pages:
            if pg not in seen:
                seen.add(pg)
                all_pages.append(pg)

    return NotesPayload(
        chosen_row_label=payloads[0].chosen_row_label,
        content=content,
        evidence=evidence,
        source_pages=all_pages,
        numeric_values=numeric_values,
        sub_agent_id=payloads[0].sub_agent_id,
    )


def _evidence_col(filing_level: str) -> int:
    # Company: D=4; Group: F=6.
    return 6 if filing_level == "group" else 4


def _write_row(
    ws,
    row: int,
    payload: NotesPayload,
    filing_level: str,
    evidence_col: int,
    errors: list[str],
) -> bool:
    # Refuse to overwrite formula cells in any write target.
    write_cols: list[tuple[int, object]] = []
    if payload.numeric_values:
        # Structured numeric — fill all four value cols for group, B+C for company.
        nv = payload.numeric_values
        if filing_level == "group":
            write_cols.extend([
                (2, nv.get("group_cy")),
                (3, nv.get("group_py")),
                (4, nv.get("company_cy")),
                (5, nv.get("company_py")),
            ])
        else:
            write_cols.extend([
                (2, nv.get("company_cy", nv.get("cy"))),
                (3, nv.get("company_py", nv.get("py"))),
            ])
    else:
        text = _truncate_with_footer(payload.content, payload.source_pages)
        # Prose — content goes to col B only (Group-CY for group, CY for company).
        # Group filings intentionally leave C/D/E empty for prose (PLAN Section 2 #6).
        write_cols.append((2, text))

    wrote_anything = False
    for col, value in write_cols:
        if value is None or value == "":
            continue
        cell = ws.cell(row=row, column=col)
        if isinstance(cell.value, str) and cell.value.startswith("="):
            errors.append(
                f"Refusing to overwrite formula cell {cell.coordinate}: {cell.value}"
            )
            continue
        cell.value = value
        wrote_anything = True

    if payload.evidence:
        ev_cell = ws.cell(row=row, column=evidence_col)
        if isinstance(ev_cell.value, str) and ev_cell.value.startswith("="):
            errors.append(
                f"Refusing to overwrite evidence formula cell {ev_cell.coordinate}"
            )
        else:
            ev_cell.value = payload.evidence

    return wrote_anything


def _truncate_with_footer(text: str, source_pages: list[int]) -> str:
    if len(text) <= CELL_CHAR_LIMIT:
        return text
    pages_str = ", ".join(str(p) for p in source_pages) if source_pages else "—"
    footer = f"\n\n[truncated — see PDF pages {pages_str}]"
    head_len = CELL_CHAR_LIMIT - len(footer)
    return text[:head_len] + footer
