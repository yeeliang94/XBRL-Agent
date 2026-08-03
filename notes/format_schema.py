"""Typed shape of the notes-formatter's style patch.

The formatter used to return free-form text that `_parse_json_patch` cleaned
up — stripping ```json fences, then hunting for the first balanced object when
the model wrapped its answer in prose. That repair exists because the model
was asked for JSON in the prompt and sometimes did not comply. Declaring the
shape lets the provider enforce it instead.

This mirrors `notes/format_patch.py` EXACTLY and deliberately adds nothing:
that module raises `FormatPatchError` on any unrecognised target or style key,
so a schema wider than it would only move the failure, and a schema narrower
than it would reject patches that are currently valid. `format_patch` stays
the authority — everything here is re-validated there after the model answers.

Keep the two in step. The vocabularies:

    targets   all | table | header | total_rows | numeric_cells,
              plus {"cell": {r, c}}, {"rows": [...]}, {"blocks": "all"}
    styles    border_{top,right,bottom,left} | clear_border | fill |
              text_align | bold | italic | underline | indent | padding |
              space_before | space_after | table_width
"""
from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field

# `format_patch.BORDER_WIDTHS` / `BORDER_STYLES` / `TEXT_ALIGN` / `SIDES`.
BorderWidth = Literal["1px", "2px", "3px"]
BorderStyle = Literal["solid", "double", "dashed", "dotted", "hidden"]
Side = Literal["top", "right", "bottom", "left"]
TextAlign = Literal["left", "center", "right", "justify"]
TargetRange = Literal["all", "table", "header", "total_rows", "numeric_cells"]


class BorderSpec(BaseModel):
    """One edge. `_border_value` also accepts the bare string "hidden"."""
    width: BorderWidth = "1px"
    style: BorderStyle = "solid"
    color: str = Field(
        default="#000000",
        description='#rrggbb, or a theme name: black, grey900, grey700, '
                    'grey500, grey300, white, orange, header_fill.',
    )


# `_border_value` short-circuits on the literal "hidden" before the dict path.
Border = Union[BorderSpec, Literal["hidden"]]


class CellRef(BaseModel):
    r: int = Field(ge=1, description="1-based row within the table")
    c: int = Field(ge=1, description="1-based column within the table")


class Target(BaseModel):
    """What the operation applies to. Exactly one addressing mode is used;
    `table` is required for every mode except `blocks`."""
    table: Optional[int] = Field(
        default=None, ge=0, description="Zero-based index of the table in the cell",
    )
    range: Optional[TargetRange] = Field(
        default=None,
        description='"all" every cell; "table" the table element itself '
                    '(table_width only); "header" the first row; "total_rows" '
                    'every row whose text contains "total"; "numeric_cells" '
                    "cells that look like figures.",
    )
    cols: Optional[List[int]] = Field(
        default=None,
        description="1-based column restriction for total_rows / rows. A "
                    "summation rule usually runs under the amount columns only.",
    )
    cell: Optional[CellRef] = None
    rows: Optional[List[int]] = Field(
        default=None, description="1-based row numbers within the table",
    )
    blocks: Optional[Literal["all"]] = Field(
        default=None,
        description="Top-level paragraphs / headings / list items. Used "
                    "INSTEAD of `table`, never with it.",
    )


class Style(BaseModel):
    """Only these keys exist. `format_patch._apply_style` raises on any other."""
    border_top: Optional[Border] = None
    border_right: Optional[Border] = None
    border_bottom: Optional[Border] = None
    border_left: Optional[Border] = None
    clear_border: Optional[List[Side]] = Field(
        default=None, description="Erase these edges.",
    )
    fill: Optional[str] = Field(
        default=None,
        description='#rrggbb, a theme name, or "transparent" to clear a fill.',
    )
    text_align: Optional[TextAlign] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    indent: Optional[str] = Field(default=None, description='e.g. "1em"')
    padding: Optional[str] = Field(
        default=None, description='cell inner spacing, e.g. "4px 8px"',
    )
    space_before: Optional[str] = Field(
        default=None, description='paragraph spacing above, e.g. "6px"',
    )
    space_after: Optional[str] = Field(
        default=None, description='paragraph spacing below, e.g. "6px"',
    )
    table_width: Optional[str] = Field(
        default=None,
        description='e.g. "100%" — only with target {"table": N, "range": "table"}',
    )


class Operation(BaseModel):
    target: Target
    style: Style


class CellPatch(BaseModel):
    row: int = Field(ge=1, description="The notes-sheet row number")
    operations: List[Operation]


class SheetFormatPatch(BaseModel):
    """The formatter's whole answer."""
    sheet: str = Field(description="Must equal the sheet you were given.")
    cells: List[CellPatch] = Field(
        default_factory=list,
        description="Empty when nothing needs restyling — that is a valid answer.",
    )
    format_summary: str = Field(description="Short user-facing description.")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Your honest self-assessment. A low number is respected, "
                    "not retried — do not inflate it.",
    )


def patch_to_dict(patch: SheetFormatPatch) -> dict:
    """The plain dict `format_patch.apply_sheet_patch` expects.

    `exclude_none` is load-bearing, not tidiness: `_apply_style` iterates the
    style keys it is given, so a serialised `"border_top": null` would reach
    `_border_value(None)` and raise, and a `"table": null` would defeat the
    `blocks` branch of `_resolve_target`.
    """
    return patch.model_dump(exclude_none=True)
