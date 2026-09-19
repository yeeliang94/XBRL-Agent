"""Bounded source repairs using established destinations, never label guesses."""
from __future__ import annotations

from notes import source_repository as srepo, source_write


def repair_recorded_placements(conn, *, run_id: int, generation_id: int,
                               missing_block_ids: list[str]) -> int:
    """Restore missing content only where the placement history proves routing.

    A policy subsection and its containing note may have different destinations.
    Filling the first sibling's cell with the whole note would undo that routing.
    A never-routed block therefore stays unresolved for semantic review.
    """
    blocks = {b["block_id"]: b for b in srepo.fetch_blocks(conn, generation_id)}
    usages = {u["block_id"]: u for u in srepo.fetch_usages(conn, generation_id)}
    active = srepo.active_placements(conn, generation_id)
    by_cell = {}
    for bid in missing_block_ids:
        usage = usages.get(bid)
        if bid not in blocks or usage is None:
            continue
        if usage["disposition"] not in {"included", "routed", "structured_consumed"}:
            continue
        coord = (usage["sheet"], usage["row"])
        if not all(value is not None for value in coord):
            continue
        historical = conn.execute(
            "SELECT 1 FROM notes_block_placements WHERE run_id=? AND generation_id=? "
            "AND block_id=? AND sheet=? AND row=?", (run_id, generation_id, bid, *coord),
        ).fetchone()
        if historical is None:
            continue
        by_cell.setdefault(coord, set()).add(bid)
    repaired = 0
    for (sheet, row), missing in by_cell.items():
        existing = conn.execute(
            "SELECT label, content_revision, content_origin FROM notes_cells "
            "WHERE run_id=? AND sheet=? AND row=?", (run_id, sheet, row),
        ).fetchone()
        if existing is None or existing["content_origin"] == "human_modified":
            continue
        selected = missing | {p["block_id"] for p in active if (p["sheet"], p["row"]) == (sheet, row)}
        try:
            source_write.write_cell_from_blocks(conn, run_id=run_id, generation_id=generation_id,
                sheet=sheet, row=row, label=existing["label"], block_ids=sorted(selected),
                actor="integrity_retry", expected_revision=existing["content_revision"])
        except source_write.SourceWriteError:
            # The caller recomputes integrity; an unsuccessful repair is never
            # converted to a resolved disposition or a successful outcome.
            continue
        repaired += 1
    return repaired
