"""Structural section partitions and the destination reviews they require."""
from __future__ import annotations

import sqlite3


def classify_source_section_partitions(conn, run_id: int, splits: list[dict]):
    """Separate whole source-section partitions from genuine note fragments.

    This checks source identities and completeness only. The reviewer still
    judges whether each worksheet field matches the PDF disclosure.
    """
    from notes import source_repository as sources, source_write
    from notes.source_sections import sections_for_note
    from notes.source_models import INPUT_KIND_PREPARED

    generation = sources.active_generation(conn, run_id)
    if generation is None or generation["input_kind"] != INPUT_KIND_PREPARED:
        return splits, []
    blocks = source_write.load_blocks(conn, generation["id"])
    by_id = {block.block_id: block for block in blocks}
    notes_by_number: dict[str, list] = {}
    for note in sources.fetch_notes(conn, generation["id"]):
        notes_by_number.setdefault(str(note["top_note_num"]), []).append(note)
    placed_by_cell: dict[tuple[str, int], set[str]] = {}
    for placement in sources.active_placements(conn, generation["id"]):
        block = by_id.get(placement["block_id"])
        if block is not None and block.block_kind != "heading":
            placed_by_cell.setdefault(
                (placement["sheet"], placement["row"]), set(),
            ).add(block.block_id)
    unresolved, partitions = [], []
    for split in splits:
        matching = notes_by_number.get(str(split["note_num"])) or []
        if len(matching) != 1:
            unresolved.append(split)
            continue
        note = matching[0]
        section_parts = [
            {bid for bid in section.block_ids
             if by_id[bid].block_kind != "heading"}
            for section in sections_for_note(
                blocks, note["source_note_id"], note["top_note_num"],
                note["title"],
            )
        ]
        section_parts = [part for part in section_parts if part]
        row_parts = [
            placed_by_cell.get((split["sheet"], row["row"]), set())
            for row in split["rows"]
        ]
        if (not all(row_parts)
                or any(row_parts[i] & row_parts[j]
                       for i in range(len(row_parts))
                       for j in range(i + 1, len(row_parts)))
                or any(
                    parts != set().union(*(
                        section for section in section_parts
                        if section <= parts
                    ))
                    or any(by_id[bid].source_note_id != note["source_note_id"]
                           for bid in parts)
                    for parts in row_parts
                )):
            unresolved.append(split)
        else:
            partitions.append(split)
    return unresolved, partitions


def pending_section_placements(
    conn: sqlite3.Connection, run_id: int, partitions: list[dict],
    verdicts: dict[int, dict],
) -> list[dict]:
    """Complete source coverage does not prove the field matches the source."""
    cells = {
        (row["sheet"], row["row"]): dict(row)
        for row in conn.execute(
            "SELECT sheet,row,label,content_revision FROM notes_cells WHERE run_id=?",
            (run_id,),
        )
    }
    pending = {}
    for partition in partitions:
        for row in partition["rows"]:
            coordinate = (partition["sheet"], row["row"])
            cell = cells.get(coordinate)
            verdict = verdicts.get(row["row"])
            if cell and verdict and verdict["revision"] == cell["content_revision"]:
                continue
            pending[coordinate] = {
                "sheet": coordinate[0], "row": coordinate[1],
                "label": cell["label"] if cell else row["row_label"],
                "content_revision": cell["content_revision"] if cell else None,
            }
    return list(pending.values())
