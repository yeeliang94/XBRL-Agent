"""Shared helpers for cross-check implementations.

All checks need to open a workbook, find a sheet by partial name, and look
up a cell value by its column-A label. This module centralises that logic.
"""
from __future__ import annotations

from typing import Optional

import openpyxl

# Reuse the formula evaluator from the verifier — it handles cross-sheet
# references and weighted sums found in MBRS templates.
from tools.verifier import _resolve_cell_value


def open_workbook(path: str) -> openpyxl.Workbook:
    """Open a workbook in data_only=False mode (preserves formulas for evaluation)."""
    return openpyxl.load_workbook(path, data_only=False)


def find_sheet(wb: openpyxl.Workbook, *candidates: str):
    """Return the first sheet whose name matches one of the candidates.

    Matching is case-insensitive. Returns None if no match found.
    """
    for name in wb.sheetnames:
        for cand in candidates:
            if name.lower() == cand.lower():
                return wb[name]
    return None


def find_value_by_label(
    ws,
    label_substr: str,
    col: int = 2,
    wb: openpyxl.Workbook = None,
) -> Optional[float]:
    """Scan column A for a row whose label contains `label_substr` (case-insensitive),
    then return the numeric value from the specified column on that row.

    When `wb` is provided, formula cells are evaluated recursively (required for
    real MBRS templates where total rows are always formulas). Without `wb`,
    formula cells return None.

    Args:
        ws: openpyxl worksheet.
        label_substr: substring to match in column A (case-insensitive, stripped of leading *).
        col: which column to read the value from (2=B for CY, 3=C for PY, etc.).
        wb: the parent workbook — needed for formula evaluation.

    Returns:
        The float value, or None if label not found or cell is empty.
    """
    target = label_substr.strip().lower()

    # Two-pass approach: exact match first, then substring containment.
    # This avoids greedy matches where "assets" (a section header) matches
    # before "total assets" (the actual total row).
    exact_row = None
    substr_row = None
    for row in ws.iter_rows(min_col=1, max_col=1):
        cell = row[0]
        if cell.value is None:
            continue
        normalized = str(cell.value).strip().lstrip("*").strip().lower()
        if normalized == target:
            exact_row = cell.row
            break
        if substr_row is None and (target in normalized or normalized in target):
            substr_row = cell.row

    match_row = exact_row or substr_row
    if match_row is None:
        return None

    val_cell = ws.cell(row=match_row, column=col)
    raw = val_cell.value
    if raw is None:
        return None

    # Formula cell — evaluate it if we have the workbook
    if isinstance(raw, str) and raw.startswith("="):
        if wb is None:
            return None
        from openpyxl.utils import get_column_letter
        cell_ref = f"{get_column_letter(col)}{match_row}"
        return _resolve_cell_value(wb, ws.title, cell_ref)

    try:
        return float(raw)
    except (ValueError, TypeError):
        return None
