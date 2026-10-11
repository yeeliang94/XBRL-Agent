"""Plain list of the horizontal rules a notes table actually draws.

Collapsed table borders store one physical line on two cells (the bottom of
one row and the top of the next). Agents reading raw inline CSS counted those
as two rules or placed a line "above" the wrong row (runs 347-349). This
resolves each shared edge once, using the CSS border-collapse winner, and names
it by the row it sits below. It describes canonical HTML; theme baselines and
transport white edges are not rules.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from mtool.notes_decorate import _parse_decls, _resolve_cell_borders, _split_css_tokens
from notes.format_patch import _build_grid
from notes.format_verify import table_rows

_STYLE_RANK = {"double": 4, "solid": 3, "dashed": 2, "dotted": 1}
_WIDTH_RE = re.compile(r"^(\d+(?:\.\d+)?)(px|pt)?$", re.I)
_INVISIBLE_COLOURS = {"#ffffff", "#fff", "white", "transparent"}


def _stroke(value: str | None) -> tuple[str, float, str] | None:
    """(style, width_px, colour) for a visible stroke; 'hidden' or None otherwise."""
    if not value:
        return None
    style, width, colour = "solid", 1.0, "#000000"
    for token in _split_css_tokens(value):
        low = token.lower()
        if low in ("hidden", "none"):
            return ("hidden", 0.0, "") if low == "hidden" else None
        if low in _STYLE_RANK:
            style = low
        elif match := _WIDTH_RE.match(low):
            width = float(match.group(1)) * (4 / 3 if match.group(2) == "pt" else 1)
        elif low in ("thin", "medium", "thick"):
            width = {"thin": 1.0, "medium": 3.0, "thick": 5.0}[low]
        else:
            colour = low
    if width <= 0 or colour in _INVISIBLE_COLOURS:
        return None
    return style, width, colour


def _winner(upper, lower):
    """CSS border-collapse: hidden wins, then width, then style; ties go up."""
    if (upper and upper[0] == "hidden") or (lower and lower[0] == "hidden"):
        return None
    candidates = [s for s in (upper, lower) if s]
    if not candidates:
        return None
    return max(candidates, key=lambda s: (s[1], _STYLE_RANK[s[0]]))


def _label(row: Tag) -> str:
    cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"], recursive=False)]
    text = " | ".join(t for t in cells if t)
    return text[:80] or "(blank row)"


def table_rules(html: str) -> list[dict]:
    """Every horizontal line per table: {table, below_row, cells, kind}.

    ``below_row`` is the 1-based direct row the line sits under (0 = above the
    first row). ``cells`` are 1-based direct cell numbers of that row;
    ``columns`` is the 1-based grid-column span the line actually covers,
    which is narrower than a merged cell when the line is partial.
    """
    out: list[dict] = []
    for index, table in enumerate(BeautifulSoup(html or "", "html.parser").find_all("table")):
        rows = table_rows(table)
        grid, _ = _build_grid(table)
        if not grid:
            continue
        n_rows = max(r for r, _ in grid) + 1
        n_cols = max(c for _, c in grid) + 1
        direct = {}
        for r, tr in enumerate(rows):
            for position, cell in enumerate(tr.find_all(["th", "td"], recursive=False), 1):
                direct[id(cell)] = (r, position)
        sides = {}

        def side(cell, name):
            key = (id(cell), name)
            if key not in sides:
                sides[key] = _stroke(_resolve_cell_borders(
                    _parse_decls(cell.get("style") or "")).get(name))
            return sides[key]

        for boundary in range(n_rows + 1):
            # One entry per contiguous run of grid columns with the same owner
            # row and stroke kind: a single and a double line side by side are
            # two lines, and a partial line under a merged cell keeps its span.
            runs: list[dict] = []
            for col in range(n_cols):
                upper = grid.get((boundary - 1, col))
                lower = grid.get((boundary, col))
                if upper is not None and upper is lower:
                    continue  # a row-spanning cell has no line inside it
                stroke = _winner(side(upper, "bottom") if upper is not None else None,
                                 side(lower, "top") if lower is not None else None)
                if stroke is None:
                    continue
                owner = upper if upper is not None else lower
                row_index, cell_number = direct.get(id(owner), (None, None))
                if row_index is None:
                    continue
                kind = "double" if stroke[0] == "double" else "single"
                last = runs[-1] if runs else None
                if (last and last["_row"] == row_index and last["kind"] == kind
                        and last["columns"][1] == col):
                    last["columns"][1] = col + 1
                    last["_cells"].add(cell_number)
                else:
                    runs.append({"_row": row_index, "kind": kind, "columns": [col + 1, col + 1],
                                 "_cells": {cell_number}})
            for run in runs:
                row_index = run["_row"]
                out.append({
                    "table": index,
                    "below_row": boundary,
                    "row_label": _label(rows[row_index]) if row_index < len(rows) else "",
                    "cells": sorted(run["_cells"]),
                    "columns": run["columns"],
                    "kind": run["kind"],
                })
    return out


def _span(columns: list[int]) -> str:
    first, last = columns
    return f"column {first}" if first == last else f"columns {first}-{last}"


def rule_map_text(html: str) -> str:
    """One readable line per physical rule, or a statement that none exist."""
    lines = []
    for rule in table_rules(html):
        where = ("above row 1" if rule["below_row"] == 0
                 else f"below row {rule['below_row']}")
        lines.append(
            f"table {rule['table']}: {rule['kind']} line {where} "
            f"(\"{rule['row_label']}\"), under cells {rule['cells']}"
            f" (grid {_span(rule['columns'])})"
        )
    return "\n".join(lines) if lines else "no explicit lines"
