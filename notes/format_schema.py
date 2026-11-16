"""Typed shape of the notes formatter's answer.

Declaring the shape lets the provider enforce it instead of parsing free text.
The formatter describes what each table IS (``TableLayout`` in
``notes/table_layout.py``: column roles, row roles, lines) and code compiles
that into formatting. Prose blocks keep a small style vocabulary. Every answer
is still re-validated against the live note and the content-preservation gate.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from notes.table_layout import TableLayout

TextAlign = Literal["left", "center", "right", "justify"]


class BlockTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: Literal["all"] = Field(description="Top-level paragraphs, headings and list items")


class BlockStyle(BaseModel):
    """Prose-block appearance; tables are described by TableLayout instead."""
    model_config = ConfigDict(extra="forbid")
    text_align: Optional[TextAlign] = None
    indent: Optional[str] = Field(default=None, description='e.g. "1em"')
    space_before: Optional[str] = Field(default=None, description='e.g. "6px"')
    space_after: Optional[str] = Field(default=None, description='e.g. "6px"')


class BlockOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: BlockTarget
    style: BlockStyle


class NoteLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row: int = Field(ge=1, description="The notes-sheet row number")
    tables: List[TableLayout] = Field(
        default_factory=list,
        description="Exactly one layout per table listed in this row's layout_tables.",
    )
    blocks: List[BlockOperation] = Field(default_factory=list)


class UnresolvedNote(BaseModel):
    """A note the formatter could not make match the source; a person checks it."""
    model_config = ConfigDict(extra="forbid")
    row: int = Field(ge=1, description="The notes-sheet row number")
    kind: Literal["house_style", "miscopied_table", "cannot_format"] = Field(
        description='"house_style" when the source shows formatting the layout cannot draw '
                    "and you used the nearest house-style equivalent; the note is not a "
                    'failure. "miscopied_table" when the copied table itself differs from the '
                    "PDF (rows, columns, merged or heading cells); that table is re-copied from "
                    'the source. "cannot_format" for anything else a person must check.',
    )
    table: Optional[int] = Field(
        default=None, ge=0, description="The table's number in layout_tables, when one table is meant.",
    )
    quote: str = Field(
        default="",
        description="For miscopied_table: an exact row label or cell text copied from that table.",
    )
    reason: str = Field(min_length=1, description="One short sentence a person will read.")


class SheetLayout(BaseModel):
    """The formatter's whole answer: what each table is, not how to style it."""
    sheet: str = Field(description="Must equal the sheet you were given.")
    # Before `notes` so a long answer states it early; optional because a
    # missing one-line summary must not discard every layout (live run 349).
    format_summary: str = Field(default="", description="Short user-facing description.")
    notes: List[NoteLayout] = Field(
        default_factory=list,
        description="One entry per note row to format; empty when nothing needs formatting.",
    )
    unresolved: List[UnresolvedNote] = Field(
        default_factory=list,
        description="Notes a person must check, each with a reason.",
    )


def layout_to_dict(layout: SheetLayout) -> dict:
    """Plain dict for screening and retry prompts; drops unset optional keys."""
    return layout.model_dump(exclude_none=True)
