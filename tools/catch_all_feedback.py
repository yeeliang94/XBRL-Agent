"""Advisory write_facts feedback when a figure lands in an ``Other …`` row.

A subtotal ties however its classes are split, so ``verify_totals`` cannot see
a figure parked in a catch-all while a dedicated row beside it stays empty.
This names those empty rows so the agent can re-check its choice. It never
changes or blocks a write.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from prompts._sign_conventions import _parse_total_formula, _template_cache_key

_MAX_ROWS_LISTED = 25
# Matrix layouts address cells by row and column, so one column's formula does
# not describe a row's class family.
_MATRIX_SHEET_MARKERS = ("socie", "sore")
# "Other" here names a concept or a counterparty-bound row, not a residual bucket.
_NOT_CATCH_ALL_PREFIXES = ("other comprehensive",)
_NOT_CATCH_ALL_PHRASES = (" due from ", " due to ")
# These totals add up a whole statement or section, so the rows under them are
# not alternative homes for the same item.
_STATEMENT_TOTAL_PREFIXES = (
    "net cash",
    "profit",
    "cash and cash equivalents at",
    "total assets",
    "total liabilities",
    "total current assets",
    "total non-current assets",
    "total current liabilities",
    "total non-current liabilities",
    "assets other than",
    "liabilities other than",
)


def _plain_label(label: str) -> str:
    return label.lstrip("* ").casefold()


def _is_catch_all(label: str) -> bool:
    plain = _plain_label(label)
    return (
        plain.startswith(("other ", "miscellaneous "))
        and not plain.startswith(_NOT_CATCH_ALL_PREFIXES)
        and not any(phrase in plain for phrase in _NOT_CATCH_ALL_PHRASES)
    )


@lru_cache(maxsize=64)
def _load_structure(
    resolved_path: str,
    _mtime_ns: int,
    _size: int,
) -> dict[str, tuple[dict[int, str], dict[int, tuple[int, ...]]]]:
    """Return ``{sheet: (labels_by_row, child_rows_by_total_row)}``."""
    wb = load_workbook(resolved_path, data_only=False, read_only=True)
    try:
        structure = {}
        for ws in wb.worksheets:
            labels: dict[int, str] = {}
            children: dict[int, tuple[int, ...]] = {}
            for row, (raw_label, formula) in enumerate(
                ws.iter_rows(min_col=1, max_col=2, values_only=True),
                start=1,
            ):
                if raw_label:
                    labels[row] = str(raw_label).strip()
                if isinstance(formula, str):
                    terms = _parse_total_formula(formula)
                    if terms:
                        children[row] = tuple(term[2] for term in terms)
            structure[ws.title] = (labels, children)
        return structure
    finally:
        wb.close()


def _leaf_rows(
    total_row: int,
    children: dict[int, tuple[int, ...]],
    seen: set[int],
) -> list[int]:
    leaves: list[int] = []
    for row in children.get(total_row, ()):
        if row in seen:
            continue
        seen.add(row)
        if row in children:
            leaves.extend(_leaf_rows(row, children, seen))
        else:
            leaves.append(row)
    return leaves


def _family_total(
    row: int,
    labels: dict[int, str],
    children: dict[int, tuple[int, ...]],
) -> int | None:
    """Return the smallest non-statement total that adds up ``row``."""
    totals = [
        total for total, kids in children.items()
        if row in kids
        and not _plain_label(labels.get(total, "")).startswith(_STATEMENT_TOTAL_PREFIXES)
    ]
    return min(totals, key=lambda total: len(children[total]), default=None)


def catch_all_warnings_for_resolved_writes(
    template_path: str | Path,
    output_path: str | Path,
    resolved_writes: list[dict],
    already_warned: set[tuple[str, int, int]],
) -> list[str]:
    """Name the empty rows beside each non-zero ``Other …`` write, once per cell."""
    template = Path(template_path)
    if not resolved_writes or not template.exists():
        return []
    structure = _load_structure(*_template_cache_key(template))

    amounts_by_target: dict[tuple[str, int, int], dict[int, float]] = {}
    for write in resolved_writes:
        value = write.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value == 0:
            continue
        sheet = str(write.get("sheet") or "")
        row, col = write.get("row"), write.get("col")
        if (
            sheet not in structure
            or any(marker in sheet.casefold() for marker in _MATRIX_SHEET_MARKERS)
            or not isinstance(row, int)
            or not isinstance(col, int)
            or (sheet, row, col) in already_warned
        ):
            continue
        labels, children = structure[sheet]
        if not _is_catch_all(labels.get(row, "")):
            continue
        family = _family_total(row, labels, children)
        if family is not None:
            amounts_by_target.setdefault((sheet, row, family), {})[col] = value
    if not amounts_by_target:
        return []

    warnings: list[str] = []
    filled = load_workbook(output_path, data_only=False, read_only=True)
    try:
        for (sheet, row, family), amounts in amounts_by_target.items():
            labels, children = structure[sheet]
            occupied: dict[int, set[int]] = {}
            for col in amounts:
                occupied[col] = {
                    number
                    for number, (cell,) in enumerate(
                        filled[sheet].iter_rows(min_col=col, max_col=col, values_only=True),
                        start=1,
                    )
                    if cell is not None
                }
            empty = [
                labels[leaf]
                for leaf in _leaf_rows(family, children, {row})
                if leaf in labels
                and any(leaf not in rows for rows in occupied.values())
            ]
            if not empty:
                continue
            already_warned.update((sheet, row, col) for col in amounts)
            written = ", ".join(
                f"{get_column_letter(col)}{row} = {value:,}"
                for col, value in sorted(amounts.items())
            )
            listed = "; ".join(empty[:_MAX_ROWS_LISTED])
            if len(empty) > _MAX_ROWS_LISTED:
                listed += f"; and {len(empty) - _MAX_ROWS_LISTED} more"
            warnings.append(
                f"Catch-all check for {sheet} '{labels[row]}' ({written}): rows "
                f"under '{labels[family]}' with no value in at least one of "
                f"those columns: {listed}. The value was written unchanged. "
                "Check it once against these rows. Keep it if the source labels "
                "it other, if no row's definition covers it, or if a search "
                "already recorded in evidence left the deciding fact unknown."
            )
    finally:
        filled.close()
    return warnings
