"""Shared helpers for cross-check implementations.

All checks need to open a workbook, find a sheet by partial name, and look
up a cell value by its column-A label. This module centralises that logic.
"""
from __future__ import annotations

from typing import Optional, Sequence, Union

import openpyxl

# Reuse the formula evaluator from the verifier — it handles cross-sheet
# references and weighted sums found in MBRS templates.
from tools.verifier import _resolve_cell_value

# SOCIE column constants (pre-MBRS layout)
_SOCIE_NCI_COL = 23       # W — Non-controlling interests
_SOCIE_TOTAL_COL = 24     # X — Total
_SOCIE_RETAINED_COL = 3   # C — Retained earnings


def has_nci_data(ws, start_row: int = 1) -> bool:
    """Check whether the SOCIE sheet has actual NCI data filled in.

    The template's NCI column (W) contains metadata strings (e.g. the header
    'Non-controlling interests' in row 2) and formula scaffolding.  This
    function only counts **numeric** non-zero values — strings and formulas
    are ignored, so metadata rows don't trigger false positives.
    """
    for row in range(start_row, ws.max_row + 1):
        val = ws.cell(row=row, column=_SOCIE_NCI_COL).value
        if val is None or val == 0:
            continue
        if isinstance(val, str):
            continue
        return True
    return False


def socie_column(ws, start_row: int = 1) -> int:
    """Return the correct SOCIE read column: Total (X=24) if NCI data exists,
    Retained earnings (C=3) otherwise."""
    return _SOCIE_TOTAL_COL if has_nci_data(ws, start_row) else _SOCIE_RETAINED_COL


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
    label_substr: Union[str, Sequence[str]],
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
        label_substr: substring to match in column A (case-insensitive, stripped of
            leading *). May also be a sequence of candidate labels — each is tried
            in order until one yields a value. Use this for fields whose wording
            differs across template variants (e.g. SOFP cash row is "cash and cash
            equivalents" in CuNonCu but "total cash and bank balances" in OrdOfLiq).
        col: which column to read the value from (2=B for CY, 3=C for PY, etc.).
        wb: the parent workbook — needed for formula evaluation.

    Returns:
        The float value, or None if no candidate label was found or cells were empty.
    """
    candidates: list[str]
    if isinstance(label_substr, str):
        candidates = [label_substr]
    else:
        candidates = list(label_substr)

    for candidate in candidates:
        target = candidate.strip().lower()

        # Collect all matching rows: exact matches first, then substring matches.
        # Multiple rows can share the same label (e.g. SOCF has a data-entry row
        # and a formula row both labelled "Cash and cash equivalents at end of period").
        # We try each match in priority order, skipping rows with no value.
        exact_rows: list[int] = []
        substr_rows: list[int] = []
        for row in ws.iter_rows(min_col=1, max_col=1):
            cell = row[0]
            if cell.value is None:
                continue
            normalized = str(cell.value).strip().lstrip("*").strip().lower()
            if normalized == target:
                exact_rows.append(cell.row)
            elif target in normalized or normalized in target:
                substr_rows.append(cell.row)

        # Try exact matches first, then substring matches
        for match_row in exact_rows + substr_rows:
            val_cell = ws.cell(row=match_row, column=col)
            raw = val_cell.value
            if raw is None:
                continue

            # Formula cell — evaluate it if we have the workbook
            if isinstance(raw, str) and raw.startswith("="):
                if wb is None:
                    continue
                from openpyxl.utils import get_column_letter
                cell_ref = f"{get_column_letter(col)}{match_row}"
                resolved = _resolve_cell_value(wb, ws.title, cell_ref)
                if resolved is not None:
                    return resolved
                continue

            try:
                return float(raw)
            except (ValueError, TypeError):
                continue

    return None
