"""Index the tables inside a notes cell — plan Phase 2, Steps 2.1 / 2.4.

A prose notes cell can hold several tables, and Malaysian financial-statement
tables nest (mammoth emits a nested `<table>` for merged-cell layouts). The
review surface needs to name ONE of them, describe its shape, and say how it
is styled.

Two things this module deliberately does NOT do:

* **It does not attribute source pages per table.** `notes_cells` carries one
  `source_pages` list for the whole cell, so page evidence is cell-level and
  the API says so. True per-table provenance arrives with the source-block
  work (plan Phase 4).
* **It does not enforce anything.** The flags are advisory. A ragged table is
  often correct — a note's table legitimately has a narrow total row — so this
  surfaces the observation and leaves the judgement to a person.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup, Tag

# A cell that reads as a figure: digits, optionally bracketed/negative, with
# thousands separators or decimals. Mirrors the intent of
# `format_patch._looks_numeric` — used only to spot a wholly non-numeric table.
_NUMERIC_RE = re.compile(r"^\(?\s*-?\s*[\d,]+(?:\.\d+)?\s*\)?$")

# A table this long would consume the whole 30,000-char cell budget by itself
# (notes.writer.CELL_CHAR_LIMIT).
_OVERSIZED_CHARS = 30_000


@dataclass
class TableEntry:
    """One table inside one notes cell."""

    # Zero-based, flat document order INCLUDING nested tables — the same
    # enumeration `notes/format_patch.py::_resolve_target` uses for
    # `target.table`. They must not drift: an operator restyling "table 2"
    # from the review surface has to hit the table the surface showed them.
    table_index: int
    depth: int          # 0 = top level, 1 = nested inside a cell, ...
    rows: int
    cols: int           # widest row, counting colspan
    cells: int          # this table's own cells, excluding nested tables'
    chars: int          # rendered text length
    source_styled: bool
    has_inline_borders: bool
    has_fills: bool
    style_state: str    # "source" | "styled" | "plain"
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "table_index": self.table_index,
            "depth": self.depth,
            "rows": self.rows,
            "cols": self.cols,
            "cells": self.cells,
            "chars": self.chars,
            "source_styled": self.source_styled,
            "style_state": self.style_state,
            "flags": list(self.flags),
        }


def _own_rows(table: Tag) -> list[Tag]:
    """Rows belonging to THIS table, not to a table nested inside it."""
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _own_cells(row: Tag, table: Tag) -> list[Tag]:
    return [
        c for c in row.find_all(["td", "th"])
        if c.find_parent("table") is table
    ]


def _span(cell: Tag) -> int:
    try:
        return max(1, int(cell.get("colspan", 1)))
    except (TypeError, ValueError):
        return 1


def _declarations(tag: Tag) -> list[tuple[str, str]]:
    """Split a `style=` attribute into (property, value) pairs, lowercased."""
    out: list[tuple[str, str]] = []
    for decl in (tag.get("style") or "").lower().split(";"):
        prop, sep, value = decl.partition(":")
        if sep:
            out.append((prop.strip(), value.strip()))
    return out


_SIDES = ("top", "right", "bottom", "left")
_BORDER_STYLE_KEYWORDS = frozenset({
    "none", "hidden", "dotted", "dashed", "solid", "double",
    "groove", "ridge", "inset", "outset",
})
_INVISIBLE_STYLES = frozenset({"none", "hidden"})
_ZERO_WIDTH_RE = re.compile(r"^0(?:\.0+)?(?:px|pt|em|rem|%)?$")
_WIDTH_TOKEN_RE = re.compile(r"^(?:thin|medium|thick|[\d.]+(?:px|pt|em|rem|%)?)$")


def _expand_sides(values: list[str]) -> dict[str, str]:
    """CSS 1–4 value expansion: all / (v,h) / (t,h,b) / (t,r,b,l)."""
    if not values:
        return {}
    if len(values) == 1:
        return {s: values[0] for s in _SIDES}
    if len(values) == 2:
        return {"top": values[0], "right": values[1],
                "bottom": values[0], "left": values[1]}
    if len(values) == 3:
        return {"top": values[0], "right": values[1],
                "bottom": values[2], "left": values[1]}
    return dict(zip(_SIDES, values[:4]))


def _shorthand_style_and_width(value: str) -> tuple[Optional[str], Optional[str]]:
    """Pull the style and width out of a `border: …` shorthand.

    Colour is ignored: a colour never makes a border visible on its own, and
    skipping it also sidesteps `rgb(0, 0, 0)` tokenising badly.
    """
    style = width = None
    for token in value.split():
        if style is None and token in _BORDER_STYLE_KEYWORDS:
            style = token
        elif width is None and _WIDTH_TOKEN_RE.match(token):
            width = token
    return style, width


def _has_visible_border(tag: Tag) -> bool:
    """True only if some SIDE resolves to a line that actually paints.

    A border declaration is not a visible border, and the two failure
    directions are both real in this codebase:

    * gotcha #16 records that erasing an edge persists as `1px hidden`, not
      `none` — so the markup meaning "deliberately no line here" contains both
      the word "border" and a non-zero width. Reading that as styling hides an
      explicitly-blank table from the needs-a-look filter.
    * gotcha #16 also records that the browser COLLAPSES per-side borders into
      grouped longhands (`border-width|style|color`). A grouped
      `border-style: hidden solid hidden hidden` has one real edge, so a
      whole-string test for the word "hidden" wrongly calls it blank.

    So every declaration is resolved per side, in document order, and CSS's own
    rule decides: a side paints only when its style is a visible keyword AND
    its width is non-zero. Note that a declared width with no declared style
    paints nothing — CSS's initial border-style is `none`.
    """
    style: dict[str, Optional[str]] = {s: None for s in _SIDES}
    width: dict[str, Optional[str]] = {s: None for s in _SIDES}

    for prop, value in _declarations(tag):
        if not prop.startswith("border") or prop in {"border-collapse",
                                                     "border-spacing",
                                                     "border-radius"}:
            continue
        parts = prop.split("-")

        if prop == "border":
            st, w = _shorthand_style_and_width(value)
            for s in _SIDES:
                if st is not None:
                    style[s] = st
                if w is not None:
                    width[s] = w
        elif prop == "border-style":
            style.update(_expand_sides(value.split()))
        elif prop == "border-width":
            width.update(_expand_sides(value.split()))
        elif prop == "border-color":
            continue  # colour alone never paints a line
        elif len(parts) >= 2 and parts[1] in _SIDES:
            side = parts[1]
            if len(parts) == 2:                      # border-top: 1px solid #000
                st, w = _shorthand_style_and_width(value)
                if st is not None:
                    style[side] = st
                if w is not None:
                    width[side] = w
            elif parts[2] == "style":
                style[side] = value
            elif parts[2] == "width":
                width[side] = value
            # border-<side>-color: ignored, as above.

    for s in _SIDES:
        st, w = style[s], width[s]
        if st is None or st in _INVISIBLE_STYLES:
            continue                                  # nothing declared, or erased
        if w is not None and _ZERO_WIDTH_RE.match(w):
            continue                                  # zero-width paints nothing
        return True
    return False


def _has_visible_fill(tag: Tag) -> bool:
    """`background-color: transparent` is how "no fill" persists (gotcha #16),
    so its presence is the opposite of a fill."""
    for prop, value in _declarations(tag):
        if prop not in {"background", "background-color"}:
            continue
        if value in {"transparent", "none", "inherit", "initial", "unset"}:
            continue
        if value.startswith("rgba(") and re.search(r",\s*0(?:\.0+)?\s*\)$", value):
            continue  # fully transparent rgba
        return True
    return False


def index_tables(html: Optional[str]) -> list[TableEntry]:
    """Return one `TableEntry` per table in ``html``, in format_ops order."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")

    entries: list[TableEntry] = []
    for idx, table in enumerate(soup.find_all("table")):
        rows = _own_rows(table)
        widths = [sum(_span(c) for c in _own_cells(tr, table)) for tr in rows]
        cell_tags = [c for tr in rows for c in _own_cells(tr, table)]

        # `data-source-styled` is value-checked to "true" by the sanitiser —
        # a stray value is not a verbatim-copy claim (gotcha #16).
        source_styled = (table.get("data-source-styled") or "").lower() == "true"

        borders = _has_visible_border(table) or any(
            _has_visible_border(c) for c in cell_tags
        )
        fills = _has_visible_fill(table) or any(
            _has_visible_fill(c) for c in cell_tags
        )

        if source_styled:
            state = "source"
        elif borders or fills:
            state = "styled"
        else:
            state = "plain"

        text_len = len(table.get_text(" ", strip=True))

        flags: list[str] = []
        if len(rows) <= 1:
            flags.append("single_row")
        if len(set(widths)) > 1:
            flags.append("ragged_rows")
        if cell_tags and not any(
            _NUMERIC_RE.match(c.get_text(" ", strip=True)) for c in cell_tags
        ):
            flags.append("no_numeric_cells")
        if text_len > _OVERSIZED_CHARS:
            flags.append("oversized")

        entries.append(
            TableEntry(
                table_index=idx,
                depth=len(table.find_parents("table")),
                rows=len(rows),
                cols=max(widths) if widths else 0,
                cells=len(cell_tags),
                chars=text_len,
                source_styled=source_styled,
                has_inline_borders=borders,
                has_fills=fills,
                style_state=state,
                flags=flags,
            )
        )
    return entries
