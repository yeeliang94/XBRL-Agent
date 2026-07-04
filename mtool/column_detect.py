"""Phase 4 — best-effort column-map detection for an uploaded mTool template.

Reads a template's cells (via the offline_fill zip reader — no Excel) and
proposes, per sheet, which column holds the row labels and which columns hold
each value role. This is a CONVENIENCE that the operator confirms, NOT a silent
authority: it returns a ``confidence`` and ``notes`` per sheet, and the caller
must fall back to an explicit operator-supplied map when confidence is low.

Heuristic (deliberately simple, testable against both our A/B/C layout and the
real mTool D/E/F layout):

* **Label column** = the column with the most text (shared-string) cells among
  the first columns — labels are text, values are numbers/empties.
* **Value columns** = the columns immediately to the right of the label column,
  assigned positionally to the sheet's roles in canonical period/scope order
  (CY before PY; Group before Company). mTool and our templates both lay value
  columns out left-to-right in that order.

The positional assumption is why this stays advisory: a template with gap
columns or a different order would mis-map, so the endpoint lets the operator
override with an explicit map.
"""
from __future__ import annotations

from typing import Any

from mtool.offline_fill import (
    col_to_idx,
    get_shared_strings,
    get_sheet_paths,
    load_workbook_entries,
    read_sheet_cells,
)

# Canonical left-to-right order of value roles across a template row.
_ROLE_ORDER = [
    "group_current_year",
    "group_prior_year",
    "current_year",
    "company_current_year",
    "prior_year",
    "company_prior_year",
]


def _idx_to_col(idx: int) -> str:
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _pick_label_column(cells: dict) -> tuple[str | None, int, int]:
    """Return (column_letter, text_count, runner_up_count) for the column with
    the most text cells. Ties/scarcity are surfaced via the counts."""
    counts: dict[str, int] = {}
    for row_cells in cells.values():
        for col, (kind, _text) in row_cells.items():
            if kind == "S":
                counts[col] = counts.get(col, 0) + 1
    if not counts:
        return None, 0, 0
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    best_col, best_n = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    return best_col, best_n, runner_up


def _order_roles(roles) -> list[str]:
    known = [r for r in _ROLE_ORDER if r in roles]
    # Any unrecognised role keeps its incoming order after the known ones.
    unknown = [r for r in roles if r not in _ROLE_ORDER]
    return known + unknown


def detect_column_map(
    template_path: str,
    doc: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Propose a column map for every sheet in ``doc``.

    Returns ``{sheet: {"label_column", "columns": {role: col}, "confidence":
    "high"|"low", "notes": [...]}}``. A sheet not present in the template gets
    ``label_column=None`` and a note. The caller decides whether ``low``
    confidence is acceptable or should trigger an operator-supplied map.
    """
    _, data, _ = load_workbook_entries(template_path)
    sheet_paths = get_sheet_paths(data)
    sst = get_shared_strings(data)

    out: dict[str, dict[str, Any]] = {}
    for sheet, cfg in doc.get("sheets", {}).items():
        notes: list[str] = []
        entry = sheet_paths.get(sheet)
        if entry is None:
            out[sheet] = {"label_column": None, "columns": {},
                          "confidence": "low",
                          "notes": [f"sheet {sheet!r} not in template"]}
            continue
        cells = read_sheet_cells(data[entry], sst)
        label_col, text_n, runner_up = _pick_label_column(cells)
        roles = _order_roles(list(cfg.get("columns", {})))
        columns: dict[str, str] = {}
        if label_col is not None:
            start = col_to_idx(label_col) + 1
            for offset, role in enumerate(roles):
                columns[role] = _idx_to_col(start + offset)

        confidence = "high"
        if label_col is None:
            confidence = "low"
            notes.append("no text column found to use as labels")
        elif text_n < 5:
            confidence = "low"
            notes.append(f"label column {label_col!r} has only {text_n} "
                         "text cells")
        elif runner_up and text_n < runner_up * 2:
            confidence = "low"
            notes.append(f"label column {label_col!r} ({text_n} text cells) "
                         f"is not clearly ahead of the next ({runner_up})")
        if columns:
            notes.append(
                "value columns assigned positionally right of "
                f"{label_col}: " + ", ".join(
                    f"{r}={c}" for r, c in columns.items()))

        out[sheet] = {"label_column": label_col, "columns": columns,
                      "confidence": confidence, "notes": notes}
    return out


def overall_confidence(column_map: dict[str, dict[str, Any]]) -> str:
    """'high' only if every sheet detected at high confidence."""
    if not column_map:
        return "low"
    return "high" if all(
        s.get("confidence") == "high" for s in column_map.values()) else "low"
