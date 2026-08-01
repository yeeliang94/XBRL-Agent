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


def _declares(cell: Tag, *needles: str) -> bool:
    style = (cell.get("style") or "").lower()
    return any(n in style for n in needles)


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

        borders = _declares(table, "border") or any(
            _declares(c, "border") for c in cell_tags
        )
        fills = _declares(table, "background") or any(
            _declares(c, "background") for c in cell_tags
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
