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

from typing import Annotated, Literal, Optional

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, ConfigDict, Field, model_validator

from notes.format_patch import FormatPatchError, _build_grid, apply_cell_operations, cell_paragraphs
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
    text_align: Optional[Literal["left", "center", "right"]] = None
    vertical_align: Optional[Literal["top", "middle", "bottom"]] = None
    bold: Optional[Literal[True]] = Field(default=None, description="Add bold to this cell only.")
    reason: str = Field(min_length=3, description="Why the column role does not apply here")

    @model_validator(mode="after")
    def require_alignment(self):
        if self.text_align is None and self.vertical_align is None and self.bold is None:
            raise ValueError("a cell override must specify alignment or bold")
        return self


class ParagraphOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int = Field(ge=1)
    cell: int = Field(ge=1, description="1-based direct cell number within the row")
    paragraph: int = Field(ge=1, description="1-based existing p element from the cell's paragraph manifest")
    indent: Optional[int] = Field(default=None, ge=0, le=8, description="Indent in 2em levels; 0 clears it")
    text_align: Optional[Literal["left", "center", "right", "justify"]] = None
    reason: str = Field(min_length=3)

    @model_validator(mode="after")
    def require_style(self):
        if self.indent is None and self.text_align is None:
            raise ValueError("a paragraph override must specify indent or alignment")
        return self


class TextEmphasis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int = Field(ge=1)
    cell: int = Field(ge=1)
    text: str = Field(min_length=1, description="Exact, unique text in this cell; may span inline marks, never paragraphs or hard breaks.")
    reason: str = Field(min_length=3)


class TableLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: int = Field(ge=0, description="Zero-based table index in the note")
    columns: list[ColumnRole] = Field(description="One role per grid column, left to right")
    frame: Literal["none", "grid"] = Field(
        default="none",
        description='"grid" only when the source boxes every cell with vertical and horizontal rules.',
    )
    rows: list[RowSpec] = Field(default_factory=list, description="Only rows that are not plain items")
    lines: list[LineSpec] = Field(default_factory=list, description="Every source-visible horizontal line")
    overrides: list[CellOverride] = Field(default_factory=list)
    paragraphs: list[ParagraphOverride] = Field(default_factory=list)
    emphasis: list[TextEmphasis] = Field(default_factory=list)
    width_percent: Optional[int] = Field(default=None, ge=10, le=100, strict=True)
    column_widths: Optional[list[Annotated[int, Field(ge=16, le=2000, strict=True)]]] = Field(
        default=None, description="Preferred width in pixels for every GRID column, including merged columns.",
    )


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
    if layout.column_widths is not None and len(layout.column_widths) != n_cols:
        errors.append(f"{name}: widths must describe all {n_cols} grid columns")
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
        if any(line.after_row > 0 and grid.get((line.after_row - 1, col)) is not None
               and grid.get((line.after_row - 1, col)) is grid.get((line.after_row, col))
               for col in span):
            errors.append(f"{label} crosses a cell merged across rows")
        elif _line_side(grid, positions, line) is None:
            errors.append(f"{label} cuts through merged cells on both sides of the edge; "
                          "widen or narrow it to whole cells on at least one side")
    targets = set()
    for item in [*layout.overrides, *layout.paragraphs, *layout.emphasis]:
        cells = (rows[item.row - 1].find_all(["th", "td"], recursive=False)
                 if item.row <= n_rows else [])
        if item.cell > len(cells):
            errors.append(f"{name}: override targets row {item.row} cell {item.cell}, which does not exist")
            continue
        key = (item.row, item.cell, getattr(item, "text", getattr(item, "paragraph", None)))
        if key in targets:
            errors.append(f"{name}: override target {key} is described twice")
        targets.add(key)
        if isinstance(item, ParagraphOverride) and item.paragraph > len(cell_paragraphs(cells[item.cell - 1])):
            errors.append(f"{name}: paragraph {item.paragraph} does not exist in row {item.row} cell {item.cell}; "
                          "a hard break is not a separate paragraph")
    return errors


def _line_side(grid, positions, line: "LineSpec") -> Optional[str]:
    """Which side of the edge carries the line: the row above ("bottom") or
    below ("top"). That side's cells must lie wholly inside the line's columns,
    so the line is drawn exactly there; the other side may be a wider merged
    cell (an amount underline above a full-width "2024" row)."""
    span = range(line.from_col - 1, line.to_col)

    def whole(row):
        cells = [grid.get((row, col)) for col in span]
        if any(cell is None for cell in cells):
            return False
        return all(min(c for _, c in positions[id(cell)]) >= span.start
                   and max(c for _, c in positions[id(cell)]) < span.stop for cell in cells)

    if line.after_row > 0 and whole(line.after_row - 1):
        return "bottom"
    if whole(line.after_row):
        return "top"
    return None


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
    if layout.frame == "grid":
        edge = {"width": "0.75px", "style": "solid", "color": "#000000"}
        ops.append({"target": {"table": layout.table, "range": "all"},
                    "style": {f"border_{side}": edge for side in ("top", "right", "bottom", "left")}})
    for line in layout.lines:
        border = {"width": "0.75px", "style": "double" if line.kind == "double" else "solid",
                  "color": "#000000"}
        side = _line_side(grid, positions, line)
        row_index = line.after_row - 1 if side == "bottom" else line.after_row
        done = set()
        for col in range(line.from_col - 1, line.to_col):
            cell = grid[(row_index, col)]
            if id(cell) in done:
                continue
            done.add(id(cell))
            first_row = min(r for r, _ in positions[id(cell)])
            # By identity: Tag equality compares content, and equal amounts
            # ("2,077,740" in four columns) would all resolve to the first cell.
            direct = next(i for i, other in enumerate(
                rows[first_row].find_all(["th", "td"], recursive=False), 1) if other is cell)
            # The patch engine mirrors the edge onto neighbours it fully covers.
            cell_op(first_row + 1, direct, {f"border_{side}": border})
    for item in layout.overrides:
        cell_op(item.row, item.cell, {key: value for key, value in {
            "text_align": item.text_align, "vertical_align": item.vertical_align,
            "bold": item.bold,
        }.items() if value is not None})
    for item in layout.paragraphs:
        style = {}
        if item.indent:
            style["indent"] = f"{item.indent * 2}em"
        if item.text_align is not None:
            style["text_align"] = item.text_align
        ops.append({"target": {"table": layout.table, "cell": {"r": item.row, "c": item.cell},
                               "paragraph": item.paragraph}, "style": style})
    for item in layout.emphasis:
        cell_op(item.row, item.cell, {"bold_text": item.text})
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
        # Sizing is touched only when the layout states it, so an editor
        # resize or source widths survive a layout that leaves sizing alone.
        table_style: dict = {}
        if layout.width_percent is not None:
            table_style["table_width"] = f"{layout.width_percent}%"
        if layout.column_widths is not None:
            table_style["column_widths"] = layout.column_widths
        if table_style:
            ops.append({"target": {"table": layout.table, "range": "table"}, "style": table_style})
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
        out.append(f"table {layout.table}: columns {cols}"
                   + ("; boxed grid" if layout.frame == "grid" else ""))
        if layout.width_percent is not None:
            out.append(f"  table width={layout.width_percent}%")
        if layout.column_widths is not None:
            out.append(f"  column widths (px)={layout.column_widths}")
        for spec in layout.rows:
            flags = [f for f, on in (("bold", spec.bold), ("shaded", spec.shaded)) if on]
            if spec.indent:
                flags.append(f"indent {spec.indent}")
            out.append(f"  row {spec.row}: {spec.role}" + (f" ({', '.join(flags)})" if flags else ""))
        for line in layout.lines:
            where = "above row 1" if line.after_row == 0 else f"below row {line.after_row}"
            out.append(f"  {line.kind} line {where}, columns {line.from_col}-{line.to_col}")
        for item in layout.overrides:
            values = ", ".join(f"{key}={value}" for key, value in
                               (("text_align", item.text_align), ("vertical_align", item.vertical_align), ("bold", item.bold))
                               if value is not None)
            out.append(f"  override row {item.row} cell {item.cell}: {values} ({item.reason})")
        for item in layout.paragraphs:
            out.append(f"  row {item.row} cell {item.cell} paragraph {item.paragraph}: "
                       f"indent={item.indent}, text_align={item.text_align} ({item.reason})")
        for item in layout.emphasis:
            out.append(f"  bold row {item.row} cell {item.cell}: {item.text!r} ({item.reason})")
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
                cells.append({"grid_columns": span, "text": cell.get_text(" ", strip=True)[:40],
                              "paragraphs": [{"paragraph": i, "text": p.get_text(" ", strip=True)[:120],
                                              "hard_breaks": len(p.find_all("br"))}
                                             for i, p in enumerate(cell_paragraphs(cell), 1)]})
            rows.append({"row": r, "cells": cells})
        out.append({"table": index, "grid_columns": (max(c for _, c in grid) + 1) if grid else 0,
                    "rows": rows})
    return out
