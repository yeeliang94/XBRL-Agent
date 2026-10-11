"""AI-described table structure, compiled into notes-table formatting by code.

The formatter decides what each column and row IS and where each line sits;
this module validates that description against the live table and turns it
into the same inline styles the rest of the system already renders. Code never
guesses accounting meaning here, and the AI never writes per-cell CSS.

Addressing (docs/local/notes-table-layout-plan.md, revision 2):
- rows are direct table rows, 1-based (a rowspan does not renumber rows);
- columns are GRID columns, 1-based, after expanding colspan/rowspan;
- a line sits after a row (0 = top edge of row 1) and must coincide with whole
  cell edges on both sides; it can never cut a merged cell or cross a rowspan.
"""
from __future__ import annotations

from typing import Literal, Optional

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, ConfigDict, Field

from notes.format_patch import FormatPatchError, _build_grid, apply_cell_operations
from mtool.notes_decorate import is_numeric_cell_text
from notes.format_verify import table_rows

SOURCE_STYLED_ATTR = "data-source-styled"
ColumnRole = Literal["label", "amount", "text", "date"]
RowRole = Literal["heading", "section", "item", "subtotal", "total"]
_RIGHT_ROLES = {"amount", "date"}


class LayoutError(ValueError):
    """A layout that cannot be applied to this note's live tables."""


class RowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int = Field(ge=1, description="1-based direct table row")
    role: RowRole = "item"
    bold: bool = Field(default=False, description="Add bold to the row's text; never removes source bold.")
    indent: int = Field(default=0, ge=0, le=3, description="Description-column indent level")
    shaded: bool = Field(default=False, description="Only where the source visibly shades the row.")


class LineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    after_row: int = Field(ge=0, description="Line sits below this row; 0 = above the first row")
    from_col: int = Field(ge=1, description="First grid column the line spans, 1-based")
    to_col: int = Field(ge=1, description="Last grid column the line spans, 1-based")
    kind: Literal["single", "double"] = "single"


class CellOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int = Field(ge=1)
    cell: int = Field(ge=1, description="1-based direct cell number within the row")
    text_align: Literal["left", "center", "right"]
    reason: str = Field(min_length=3, description="Why the column role does not apply here")


class TableLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: int = Field(ge=0, description="Zero-based table index in the note")
    columns: list[ColumnRole] = Field(description="One role per grid column, left to right")
    rows: list[RowSpec] = Field(default_factory=list, description="Only rows that are not plain items")
    lines: list[LineSpec] = Field(default_factory=list, description="Every source-visible horizontal line")
    overrides: list[CellOverride] = Field(default_factory=list)


def layout_tables(html: str) -> list[int]:
    """Indexes of the tables a layout must describe (Word source tables excluded)."""
    tables = BeautifulSoup(html or "", "html.parser").find_all("table")
    return [i for i, t in enumerate(tables) if t.get(SOURCE_STYLED_ATTR) != "true"]


def _table_errors(table: Tag, layout: TableLayout) -> list[str]:
    errors: list[str] = []
    rows = table_rows(table)
    grid, positions = _build_grid(table)
    if not grid:
        return [f"table {layout.table} has no cells"]
    n_rows, n_cols = len(rows), max(c for _, c in grid) + 1
    name = f"table {layout.table}"
    if len(layout.columns) != n_cols:
        errors.append(f"{name}: columns lists {len(layout.columns)} roles but the table has {n_cols} grid columns")
    seen_rows = set()
    for spec in layout.rows:
        if spec.row > n_rows:
            errors.append(f"{name}: row {spec.row} does not exist (table has {n_rows} rows)")
        if spec.row in seen_rows:
            errors.append(f"{name}: row {spec.row} is described twice")
        seen_rows.add(spec.row)
    by_boundary: dict[int, list[LineSpec]] = {}
    for line in layout.lines:
        label = f"{name}: line after row {line.after_row}, columns {line.from_col}-{line.to_col}"
        if line.after_row > n_rows or line.from_col > line.to_col or line.to_col > n_cols:
            errors.append(f"{label} is outside the table ({n_rows} rows, {n_cols} columns)")
            continue
        for other in by_boundary.get(line.after_row, []):
            if line.from_col <= other.to_col and other.from_col <= line.to_col:
                errors.append(f"{label} overlaps another line on the same edge")
        by_boundary.setdefault(line.after_row, []).append(line)
        span = range(line.from_col - 1, line.to_col)
        for col in span:
            upper = grid.get((line.after_row - 1, col)) if line.after_row > 0 else None
            lower = grid.get((line.after_row, col))
            if upper is not None and upper is lower:
                errors.append(f"{label} crosses a cell merged across rows at column {col + 1}")
                break
        else:
            for col in span:
                for cell in (grid.get((line.after_row - 1, col)) if line.after_row > 0 else None,
                             grid.get((line.after_row, col))):
                    if cell is None:
                        continue
                    cols = [c for _, c in positions[id(cell)]]
                    if min(cols) < span.start or max(cols) >= span.stop:
                        errors.append(f"{label} cuts through a merged cell spanning columns "
                                      f"{min(cols) + 1}-{max(cols) + 1}; widen or narrow the line to whole cells")
                        break
                else:
                    continue
                break
    for item in layout.overrides:
        cells = (rows[item.row - 1].find_all(["th", "td"], recursive=False)
                 if item.row <= n_rows else [])
        if item.cell > len(cells):
            errors.append(f"{name}: override targets row {item.row} cell {item.cell}, which does not exist")
    return errors


def validate_layouts(html: str, layouts: list[TableLayout]) -> list[str]:
    """Mechanical checks against the live note; empty list means applicable."""
    tables = BeautifulSoup(html or "", "html.parser").find_all("table")
    expected = layout_tables(html)
    given = [layout.table for layout in layouts]
    errors = []
    if sorted(given) != expected:
        errors.append(f"provide exactly one layout for each table {expected}; got {sorted(given)}")
    for layout in layouts:
        if layout.table < len(tables) and layout.table in expected and given.count(layout.table) == 1:
            errors.extend(_table_errors(tables[layout.table], layout))
    return errors


def _operations(table: Tag, layout: TableLayout, cell_padding: tuple[int, int]) -> list[dict]:
    rows = table_rows(table)
    grid, positions = _build_grid(table)
    specs = {spec.row: spec for spec in layout.rows}
    ops: list[dict] = []

    def cell_op(r, c, style):
        ops.append({"target": {"table": layout.table, "cell": {"r": r, "c": c}}, "style": style})

    for r, tr in enumerate(rows, 1):
        spec = specs.get(r)
        for c, cell in enumerate(tr.find_all(["th", "td"], recursive=False), 1):
            cols = sorted({col for _, col in positions.get(id(cell), [])})
            roles = {layout.columns[col] for col in cols if col < len(layout.columns)}
            # Right only when every column the cell covers is an amount/date
            # column; a cell spanning a label column (a "2024" section row) is left.
            right = bool(roles) and roles <= _RIGHT_ROLES
            text = cell.get_text(strip=True)
            style: dict = {}
            if right:
                style["text_align"] = "right"
            elif cell.name == "th" or is_numeric_cell_text(text):
                # Only where render-time fallbacks would otherwise guess right;
                # plain left text needs no declaration (Excel cell-size budget).
                style["text_align"] = "left"
            if spec and spec.bold and text:
                style["bold"] = True
            if spec and spec.shaded:
                style["fill"] = "header_fill"
            if spec and spec.indent and 0 in cols:
                # Cells accept padding, not margins: widen the left padding
                # by 16px per level on top of the theme's normal padding.
                v, h = cell_padding
                style["padding"] = f"{v}px {h}px {v}px {h + 16 * spec.indent}px"
            if style:
                cell_op(r, c, style)
    for line in layout.lines:
        border = {"width": "0.75px", "style": "double" if line.kind == "double" else "solid",
                  "color": "#000000"}
        done = set()
        for col in range(line.from_col - 1, line.to_col):
            # Write on the upper cell's bottom; the patch engine mirrors it to
            # the cell below. Above row 1, or beside a short upper row, use the
            # lower cell's top instead.
            side, cell = "bottom", grid.get((line.after_row - 1, col)) if line.after_row else None
            if cell is None:
                side, cell = "top", grid.get((line.after_row, col))
            if cell is None or id(cell) in done:
                continue
            done.add(id(cell))
            first_row = min(r for r, _ in positions[id(cell)])
            direct = rows[first_row].find_all(["th", "td"], recursive=False).index(cell) + 1
            cell_op(first_row + 1, direct, {f"border_{side}": border})
    for item in layout.overrides:
        cell_op(item.row, item.cell, {"text_align": item.text_align})
    return ops


def layout_operations(html: str, layouts: list[TableLayout],
                      cell_padding: tuple[int, int] = (3, 3)) -> list[dict]:
    """Patch operations for validated layouts: reset owned styling, then apply.

    Raises LayoutError listing every structural problem. The operations run
    through the normal patch engine, sanitiser and content-preservation gate.
    """
    errors = validate_layouts(html, layouts)
    if errors:
        raise LayoutError("; ".join(errors))
    tables = BeautifulSoup(html or "", "html.parser").find_all("table")
    ops: list[dict] = []
    for layout in sorted(layouts, key=lambda item: item.table):
        ops.append({"target": {"table": layout.table, "range": "all"},
                    "style": {"reset_formatting": True}})
        ops.extend(_operations(tables[layout.table], layout, cell_padding))
    return ops


def compile_layouts(html: str, layouts: list[TableLayout],
                    cell_padding: tuple[int, int] = (3, 3)) -> str:
    """Apply validated layouts to one note's HTML."""
    ops = layout_operations(html, layouts, cell_padding)
    if not ops:
        return html
    try:
        return apply_cell_operations(html, ops)
    except FormatPatchError as exc:
        raise LayoutError(str(exc)) from exc


def describe_layouts(layouts: list[TableLayout]) -> str:
    """Plain-language summary for the reviewer and traces."""
    out = []
    for layout in sorted(layouts, key=lambda item: item.table):
        cols = ", ".join(f"{i}={role}" for i, role in enumerate(layout.columns, 1))
        out.append(f"table {layout.table}: columns {cols}")
        for spec in layout.rows:
            flags = [f for f, on in (("bold", spec.bold), ("shaded", spec.shaded)) if on]
            if spec.indent:
                flags.append(f"indent {spec.indent}")
            out.append(f"  row {spec.row}: {spec.role}" + (f" ({', '.join(flags)})" if flags else ""))
        for line in layout.lines:
            where = "above row 1" if line.after_row == 0 else f"below row {line.after_row}"
            out.append(f"  {line.kind} line {where}, columns {line.from_col}-{line.to_col}")
        for item in layout.overrides:
            out.append(f"  override row {item.row} cell {item.cell}: {item.text_align} ({item.reason})")
    return "\n".join(out) if out else "no tables"


def layout_manifest(html: str) -> list[dict]:
    """The coordinates a layout uses: grid columns per cell, by direct row."""
    tables = BeautifulSoup(html or "", "html.parser").find_all("table")
    out = []
    for index in layout_tables(html):
        table = tables[index]
        grid, positions = _build_grid(table)
        rows = []
        for r, tr in enumerate(table_rows(table), 1):
            cells = []
            for cell in tr.find_all(["th", "td"], recursive=False):
                cols = sorted({c for _, c in positions.get(id(cell), [])})
                span = [cols[0] + 1, cols[-1] + 1] if cols else []
                cells.append({"grid_columns": span, "text": cell.get_text(" ", strip=True)[:40]})
            rows.append({"row": r, "cells": cells})
        out.append({"table": index, "grid_columns": (max(c for _, c in grid) + 1) if grid else 0,
                    "rows": rows})
    return out
