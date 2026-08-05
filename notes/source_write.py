"""Write a notes cell FROM source blocks — plan Phase 6, Step 6.2.

The write contract for link-only mapping: an agent returns a destination row,
a list of block ids, and optionally `format_ops`. It does not return prose.
Everything that decides what the cell says happens here, in ordinary code.

One function owns it (`write_cell_from_blocks`) because three callers need the
identical guarantees — the notes agent, the reviewer's relink tool, and the
restore path. A second implementation is how two of them end up disagreeing
about whether a block was used.

Every write does four things together, in one transaction:

1. render the blocks (deterministic, `notes/source_render.py`),
2. refuse an oversized render rather than truncating it,
3. store the cell,
4. record the lineage AND a disposition per block.

Step 4 is not bookkeeping. A cell written without dispositions leaves its
blocks counted as unresolved, so the run reports a gap it does not have; and
lineage written after the fact leaves a window where the cell looks
source-exact and is not.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from db import repository as repo
from notes import lineage as _lineage
from notes import source_render
from notes import source_repository as srepo
from notes.source_models import Disposition, SourceBlock


class SourceWriteError(ValueError):
    """The write cannot be performed as asked. Carries a message meant for
    the agent, so it says what to do instead rather than what went wrong."""


@dataclass
class WriteOutcome:
    sheet: str
    row: int
    block_ids: list[str]
    rendered_chars: int
    style_source: str
    warnings: list[str] = field(default_factory=list)

    def as_message(self) -> str:
        base = (
            f"ok: {self.sheet} row {self.row} built from "
            f"{len(self.block_ids)} source part(s), {self.rendered_chars:,} "
            f"characters"
        )
        return base + ("\n" + "\n".join(self.warnings) if self.warnings else "")


def load_blocks(conn: sqlite3.Connection, generation_id: int) -> list[SourceBlock]:
    """The blocks an agent may choose from, as typed rows."""
    return [
        SourceBlock(
            block_id=r["block_id"],
            block_kind=r["block_kind"],
            reading_order=r["reading_order"],
            canonical_html=r["canonical_html"] or "",
            content_sha256=r["content_sha256"],
            page=r["page"],
            source_note_id=r["source_note_id"],
            table_group_id=r["table_group_id"],
            continues_block_id=r["continues_block_id"],
        )
        for r in srepo.fetch_blocks(conn, generation_id)
    ]


def expand_table_groups(
    available: Sequence[SourceBlock], block_ids: Iterable[str]
) -> list[str]:
    """Pull in the rest of any table the selection only partly names.

    A table split across a page break is one disclosure. Silently rendering
    half of it is the failure `table_group_id` exists to catch, and asking the
    agent to notice the split itself just moves the failure upstream.
    """
    by_id = {b.block_id: b for b in available}
    wanted = list(dict.fromkeys(block_ids))
    groups = {
        by_id[bid].table_group_id for bid in wanted
        if bid in by_id and by_id[bid].table_group_id
    }
    if not groups:
        return wanted
    for b in available:
        if b.table_group_id in groups and b.block_id not in wanted:
            wanted.append(b.block_id)
    order = {b.block_id: b.reading_order for b in available}
    return sorted(wanted, key=lambda bid: order.get(bid, 0))


def resolve_target(
    conn: sqlite3.Connection,
    sheet: str,
    row: int,
    *,
    template_prefix: str,
    allowed_sheets: Optional[Sequence[str]] = None,
) -> str:
    """Validate a write target and return its template label.

    The writer used to accept any `(sheet, row)` — a write to `Ghost` row 999
    succeeded and got a clean verdict, because the agent fell back to an empty
    label on lookup failure and the reviewer's relink bypassed its own target
    guard. Validation belongs HERE, in the one function all three callers go
    through, rather than in each of them (peer review, 2026-08-01).
    """
    from db import repository as repo

    if allowed_sheets is not None and sheet not in allowed_sheets:
        raise SourceWriteError(
            f"{sheet} is not a sheet you may write. Yours: "
            f"{', '.join(allowed_sheets)}."
        )
    node = repo.fetch_notes_node(
        conn, sheet=sheet, row=row, template_prefix=template_prefix,
    )
    if node is None:
        raise SourceWriteError(
            f"{sheet} row {row} is not a row of this filing's notes "
            "templates. Call read_template to see the rows you can write."
        )
    if (node.get("kind") or "").upper() != "LEAF":
        raise SourceWriteError(
            f"{sheet} row {row} is a section heading, not a writable row. "
            "Write to the rows beneath it."
        )
    return node.get("label") or ""


def write_cell_from_blocks(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    generation_id: int,
    sheet: str,
    row: int,
    block_ids: Sequence[str],
    label: str = "",
    evidence: Optional[str] = None,
    source_pages: Optional[list[int]] = None,
    format_ops: Optional[list] = None,
    actor: str = "notes_agent",
    disposition: Disposition = Disposition.INCLUDED,
    template_prefix: Optional[str] = None,
    allowed_sheets: Optional[Sequence[str]] = None,
) -> WriteOutcome:
    """Build and store one cell from the named source blocks.

    Raises :class:`SourceWriteError` for anything the caller can act on: an
    unknown block id, an empty selection, a target that is not a writable row
    of this filing's templates, or a note too long for one cell.

    ``template_prefix`` enables target validation. It is optional only so the
    pure-unit tests can exercise rendering without a template registry; every
    live caller passes it.
    """
    if template_prefix:
        label = resolve_target(
            conn, sheet, row,
            template_prefix=template_prefix, allowed_sheets=allowed_sheets,
        ) or label

    if not block_ids:
        raise SourceWriteError(
            "no source parts were named. A cell is built from the document, "
            "so name the parts it should contain."
        )

    available = load_blocks(conn, generation_id)
    try:
        wanted = expand_table_groups(available, block_ids)
        rendered = source_render.render_blocks(
            available, wanted, format_ops=format_ops,
            row_label=f"{sheet} row {row}",
        )
    except source_render.BlockSelectionError as exc:
        raise SourceWriteError(str(exc)) from exc

    if rendered.oversized:
        raise SourceWriteError(
            f"the parts named for {sheet} row {row} run to "
            f"{rendered.rendered_chars:,} characters, over the "
            f"{source_render.CELL_CHAR_LIMIT:,} a cell holds. Split the note "
            "across the rows the template provides, or name fewer parts — the "
            "cell is never cut short to fit."
        )
    if not rendered.usable:
        raise SourceWriteError(
            f"the parts named for {sheet} row {row} render to nothing."
        )

    asked_for = set(block_ids)
    added = [bid for bid in rendered.block_ids if bid not in asked_for]

    # One transaction for the cell, its lineage and its dispositions. Split
    # across three, a crash between them leaves a cell that looks source-exact
    # with no record of which parts it used — which reads as a gap in a
    # document that has none.
    #
    # If the caller already has a transaction open we JOIN it rather than
    # nesting (SQLite has no nested transactions) and leave the commit to
    # them. Either way the three writes land together, which is the property
    # that matters; committing someone else's pending work to get our own
    # BEGIN would be worse than the problem.
    owns_txn = not conn.in_transaction
    if owns_txn:
        conn.execute("BEGIN IMMEDIATE")
    try:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=sheet, row=row, label=label,
            html=rendered.html, evidence=evidence,
            source_pages=source_pages or [], style_source=rendered.style_source,
        )
        _lineage.mark_source_render(
            conn, run_id, sheet, row,
            generation_id=generation_id,
            rendered_sha256=rendered.source_rendered_sha256,
            content_origin=rendered.content_origin.value,
            render_version=source_render.RENDER_VERSION,
        )
        for bid in rendered.block_ids:
            srepo.record_disposition_in_txn(
                conn, run_id, generation_id, bid, disposition,
                actor=actor, sheet=sheet, row=row, target_kind="prose_cell",
            )
        # The PLACEMENT ledger (v37) is what makes a relink honest: blocks
        # dropped from this cell are deactivated here, so they stop counting
        # as placed even though their disposition row still says `included`.
        srepo.set_cell_placements(
            conn, run_id, generation_id, sheet, row, rendered.block_ids,
            render_sha256=rendered.source_rendered_sha256,
        )
        if owns_txn:
            conn.commit()
    except Exception:
        if owns_txn:
            conn.rollback()
        raise

    warnings = list(rendered.warnings)
    if added:
        warnings.append(
            f"note: {', '.join(added)} were added because they are the rest "
            "of a table your selection started."
        )
    return WriteOutcome(
        sheet=sheet, row=row, block_ids=list(rendered.block_ids),
        rendered_chars=rendered.rendered_chars,
        style_source=rendered.style_source, warnings=warnings,
    )
