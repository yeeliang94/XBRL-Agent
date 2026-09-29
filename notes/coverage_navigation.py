"""Read-time destinations for sub-notes on older coverage rows."""
from __future__ import annotations

import json
import sqlite3

from notes.coverage_checklist import (
    POLICIES_SHEET_DEFAULT, _classify_placements, subnote_key_for_note,
    subnote_keys_for_refs,
)
from notes.detectors import load_provenance_entries
from notes.source_models import SourceBlock
from notes.source_sections import sections_for_note
from notes import source_repository as source_repo


def subnote_navigation_placements(
    conn: sqlite3.Connection, run_id: int, wanted: set[tuple[int, str]],
    db_path: str,
) -> dict[tuple[int, str], list[dict]]:
    """Locate existing child notes without changing their reviewed status."""
    live_labels = {
        (r["sheet"], r["row"]): r["label"] or ""
        for r in conn.execute(
            "SELECT sheet, row, label FROM notes_cells WHERE run_id = ?", (run_id,),
        )
    }
    located: dict[tuple[int, str], dict[tuple[str, int], str]] = {}
    for entry in load_provenance_entries(run_id, db_path):
        sheet, row = entry.get("sheet"), entry.get("row")
        if not sheet or row is None or (str(sheet), int(row)) not in live_labels:
            continue
        for key in subnote_keys_for_refs(entry.get("source_note_refs") or []):
            if key in wanted:
                located.setdefault(key, {})[(str(sheet), int(row))] = (
                    entry.get("row_label") or ""
                )

    active_generations = conn.execute(
        "SELECT id FROM notes_source_generations WHERE run_id = ? AND status = 'active'",
        (run_id,),
    ).fetchall()
    for generation in active_generations:
        generation_id = generation["id"]
        blocks = [
            SourceBlock(
                block_id=b["block_id"], block_kind=b["block_kind"],
                reading_order=b["reading_order"],
                canonical_html=b["canonical_html"] or "", page=b["page"],
                locator=json.loads(b["locator_json"] or "{}"),
                source_note_id=b["source_note_id"],
            )
            for b in source_repo.fetch_blocks(conn, generation_id)
        ]
        placed: dict[str, set[tuple[str, int]]] = {}
        for placement in source_repo.active_placements(conn, generation_id):
            coord = (placement["sheet"], placement["row"])
            if coord in live_labels:
                placed.setdefault(placement["block_id"], set()).add(coord)
        for note in source_repo.fetch_notes(conn, generation_id):
            try:
                note_num = int(note["top_note_num"])
            except (TypeError, ValueError):
                continue
            prefix = f"section:{note['source_note_id']}:"
            for section in sections_for_note(
                blocks, note["source_note_id"], str(note_num), note["title"] or "",
            ):
                ref = section.section_id[len(prefix):].split(":")[0]
                key = (note_num, subnote_key_for_note(note_num, ref))
                if ref == "root" or key not in wanted:
                    continue
                for block_id in section.block_ids:
                    for coord in placed.get(block_id, ()):
                        located.setdefault(key, {})[coord] = live_labels[coord]

    return {
        key: [placement.to_dict() for placement in _classify_placements(
            coords, POLICIES_SHEET_DEFAULT,
        )]
        for key, coords in located.items()
    }
