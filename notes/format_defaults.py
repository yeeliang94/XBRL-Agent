"""Deterministic house-style floor for notes-table formatting.

When a notes payload carries no usable ``format_ops`` observation (the
extraction-time formatting sidecar — docs/PLAN-notes-format-sidecar.md),
the writer falls back to this module: a zero-LLM synthesis of the
accountant convention most Malaysian AFS tables follow —

- borderless body, no cell fills (the review panel's theme would otherwise
  paint a default grid + grey header that most source tables don't show),
- amount columns right-aligned (including their year / currency-caption
  header cells, mirroring the formatter prompt's caption-alignment rule),
- summation rules (single line above, double line below) under ONLY the
  amount columns of "total" rows — never across the label column.

The output is a list of the SAME constrained operations the AI formatter
emits (the ``notes/format_patch.py`` vocabulary), consumed through the same
``apply_cell_operations`` gates — so there is exactly ONE code path that
mutates cell styling, and the floor can never express anything the
sanitiser would reject.

Kill switch: ``XBRL_NOTES_HOUSE_STYLE`` (default ON), read at call time so
tests can toggle it — same pattern as ``XBRL_FACT_BASED_CHECKS``.
"""
from __future__ import annotations

import os
import re
from typing import Any

from bs4 import BeautifulSoup, Tag


def house_style_enabled() -> bool:
    """The floor's kill switch — default ON; ``0``/``false``/``off`` disable."""
    raw = os.environ.get("XBRL_NOTES_HOUSE_STYLE", "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


# Python twin of NUMERIC_CELL_RE in web/src/lib/tableAlign.ts (the shared
# frontend numeric-cell heuristic): thousands-separated values (`1,595`),
# parenthesised negatives (`(95)`), bare dashes used for an empty year
# column (`—` / `–` / `-`), decimals, and a leading minus. Keep the two in
# sync — both decide "does this cell read like an accountant number".
NUMERIC_TEXT_RE = re.compile(
    r"^\(?\s*-?\s*[\d,]+(?:\.\d+)?\s*\)?$|^[-—–]+$"
)

# The classic summation rules: single line above the total figure, double
# line below it. Colours are plain black — the floor is a house style, not
# a PDF observation.
_TOTAL_RULE_STYLE: dict[str, Any] = {
    "border_top": {"width": "1px", "style": "solid", "color": "#000000"},
    "border_bottom": {"width": "3px", "style": "double", "color": "#000000"},
}


def house_style_ops(html: str) -> list[dict[str, Any]]:
    """Synthesize a house-style ops patch for every ``<table>`` in ``html``.

    Pure function over the HTML string — no LLM, no I/O. Returns ``[]`` for
    prose-only content (paragraphs are never touched). The ops use the same
    zero-based table indices / 1-based row+col addressing as
    ``format_patch._resolve_target``, so they can be fed straight into
    ``apply_cell_operations``.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    ops: list[dict[str, Any]] = []
    for t_idx, table in enumerate(soup.find_all("table")):
        if not isinstance(table, Tag):
            continue
        # Row/cell walking mirrors _resolve_target's own addressing
        # (find_all("tr") for rows, direct th/td children per row) so the
        # indices we emit resolve to the same elements the applier sees.
        rows = table.find_all("tr")
        row_cells = [
            [
                c for c in tr.find_all(["th", "td"], recursive=False)
                if isinstance(c, Tag)
            ]
            for tr in rows
        ]
        if not any(row_cells):
            continue  # degenerate table with no cells — nothing to style

        # 1. Accountant baseline: no grid, no fills. Explicit clears (not
        # attribute absence) because the review panel's theme CSS paints a
        # default grid + header fill wherever the HTML says nothing.
        ops.append({
            "target": {"table": t_idx, "range": "all"},
            "style": {
                "clear_border": ["top", "right", "bottom", "left"],
                "fill": "transparent",
            },
        })

        numeric_cols = _numeric_columns(row_cells)
        if numeric_cols:
            # 2. Right-align the amount columns — every cell in the column,
            # so the year / "RM'000" caption headers line up over their
            # figures (the formatter prompt's caption-alignment rule).
            ops.append({
                "target": {
                    "table": t_idx,
                    "rows": list(range(1, len(rows) + 1)),
                    "cols": numeric_cols,
                },
                "style": {"text_align": "right"},
            })
            # 3. Summation rules under the amount columns of total rows —
            # only when a total row actually exists ("total" substring,
            # matching _resolve_target's own total_rows test), otherwise
            # the target would resolve to nothing and the applier raises.
            if any(
                "total" in tr.get_text(" ", strip=True).lower() for tr in rows
            ):
                ops.append({
                    "target": {
                        "table": t_idx,
                        "range": "total_rows",
                        "cols": numeric_cols,
                    },
                    "style": dict(_TOTAL_RULE_STYLE),
                })
    return ops


def _numeric_columns(row_cells: list[list[Tag]]) -> list[int]:
    """1-based positional columns whose non-empty cells are mostly numeric.

    Column 1 of a multi-column table is exempt (it's the row-label column,
    left-aligned even when a cell reads like a number — same exemption as
    ``shouldRightAlignCell`` in web/src/lib/tableAlign.ts). Positional
    addressing deliberately ignores colspan drift — the same limitation the
    ops vocabulary itself has, so the emitted `cols` resolve consistently.
    """
    max_cols = max((len(cells) for cells in row_cells), default=0)
    if max_cols == 0:
        return []
    first_eligible = 2 if max_cols > 1 else 1
    numeric_cols: list[int] = []
    for col in range(first_eligible, max_cols + 1):
        numeric = 0
        text = 0
        for cells in row_cells:
            if col > len(cells):
                continue
            value = cells[col - 1].get_text(" ", strip=True)
            if not value:
                continue
            if NUMERIC_TEXT_RE.match(value):
                numeric += 1
            else:
                text += 1
        # Majority vote over non-empty cells: a genuine amount column has a
        # couple of caption cells ("2024", "RM'000" — the year IS numeric-
        # shaped, the caption isn't) above many figures; a prose column has
        # mostly text. Requires at least one numeric cell so an all-empty
        # column never qualifies.
        if numeric >= 1 and numeric >= text:
            numeric_cols.append(col)
    return numeric_cols
