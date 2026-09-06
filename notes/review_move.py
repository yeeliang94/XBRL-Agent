"""Move a reviewed note without rewriting its content or its source lineage."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import sqlite3

from concept_model.filing_targets import resolve_writable_html_target
from db import repository as repo
from notes.html_to_text import rendered_length
from notes import source_repository as sources
from notes.source_models import Disposition


class MoveConflict(ValueError):
    """The compared cells or the run are no longer safe to move."""


def move_reviewed_note(
    conn: sqlite3.Connection, *, run_id: int, sheet: str, row: int,
    destination_sheet: str, destination_row: int,
    expected_revision: int, destination_revision: int | None,
) -> None:
    """Caller owns BEGIN IMMEDIATE; every ledger change commits with the move."""
    run = repo.fetch_run(conn, run_id)
    if run is None:
        raise LookupError("Run not found")
    if run.status in {"running", "pending"}:
        raise MoveConflict("Wait for extraction to finish before moving a note.")
    busy = conn.execute(
        "SELECT 1 FROM notes_review_tasks WHERE run_id = ? AND status = 'running' "
        "UNION ALL SELECT 1 FROM notes_format_tasks WHERE run_id = ? AND status = 'running' "
        "UNION ALL SELECT 1 FROM notes_integrity_tasks WHERE run_id = ? AND status = 'running'",
        (run_id, run_id, run_id),
    ).fetchone()
    if busy:
        raise MoveConflict("Wait for the notes review or formatting pass to finish.")
    if (sheet, row) == (destination_sheet, destination_row):
        raise ValueError("Choose a different destination field.")
    config = run.config or {}
    family = f"{config.get('filing_standard', 'mfrs').lower()}-{config.get('filing_level', 'company').lower()}-"
    target = resolve_writable_html_target(
        conn, family_prefix=family, sheet=destination_sheet, row=destination_row,
    )
    if target is None:
        raise ValueError("Choose a writable notes field in this run's template.")
    source = conn.execute(
        "SELECT * FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, sheet, row),
    ).fetchone()
    destination = conn.execute(
        "SELECT * FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, destination_sheet, destination_row),
    ).fetchone()
    if source is None or source['content_revision'] != expected_revision:
        raise MoveConflict("The source changed. Reload the notes and compare again.")
    if rendered_length(source['html']) == 0:
        raise MoveConflict("The source field is empty.")
    actual_destination_revision = destination['content_revision'] if destination else None
    if actual_destination_revision != destination_revision:
        raise MoveConflict("The destination changed. Reload the notes and compare again.")
    if destination and rendered_length(destination['html']) > 0:
        raise MoveConflict("The destination contains content. Choose an empty field.")
    # An apparently empty destination with source placements is unresolved,
    # not an available slot; never discard its ledger or source lineage.
    if (destination and destination['source_generation_id'] is not None) or conn.execute(
        "SELECT 1 FROM notes_block_placements WHERE run_id = ? AND sheet = ? AND row = ? AND active = 1",
        (run_id, destination_sheet, destination_row),
    ).fetchone():
        raise MoveConflict("The destination has source records. Choose another field.")
    for table in ("notes_cell_provenance", "notes_block_usages"):
        if conn.execute(f"SELECT 1 FROM {table} WHERE run_id = ? AND sheet = ? AND row = ?",
                        (run_id, destination_sheet, destination_row)).fetchone():
            raise MoveConflict("The destination has source records. Choose another field.")
    coverage_rows = conn.execute("SELECT id, placements_json FROM notes_coverage_rows WHERE run_id = ?", (run_id,)).fetchall()
    for coverage in coverage_rows:
        if any(item.get('sheet') == destination_sheet and item.get('row') == destination_row
               for item in json.loads(coverage['placements_json'] or '[]')):
            raise MoveConflict("The destination has a recorded placement. Choose another field.")
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    conn.execute("DELETE FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
                 (run_id, destination_sheet, destination_row))
    # Relocate the existing record: all HTML, style, hashes, origin and
    # divergence flags survive. Advance past BOTH coordinates' revisions.
    conn.execute(
        "UPDATE notes_cells SET sheet = ?, row = ?, label = ?, concept_uuid = ?, "
        "content_revision = ?, updated_at = ?, invalid_target = 0, invalid_target_reason = NULL WHERE id = ?",
        (destination_sheet, destination_row, target['label'], target['concept_uuid'],
         max(expected_revision, destination_revision or 0) + 1, now, source['id']),
    )
    repo.move_notes_provenance(conn, run_id=run_id, from_sheet=sheet, from_row=row,
                              to_sheet=destination_sheet, to_row=destination_row,
                              to_label=target['label'])
    placements = conn.execute(
        "SELECT * FROM notes_block_placements WHERE run_id = ? AND sheet = ? AND row = ? AND active = 1",
        (run_id, sheet, row),
    ).fetchall()
    for generation in {item['generation_id'] for item in placements}:
        group = [item for item in placements if item['generation_id'] == generation]
        sources.set_cell_placements(conn, run_id, generation, sheet, row, [])
        sources.set_cell_placements(conn, run_id, generation, destination_sheet, destination_row,
                                    [item['block_id'] for item in group], render_sha256=group[0]['render_sha256'])
    active_blocks = {(item['generation_id'], item['block_id']) for item in placements}
    for usage in conn.execute(
        "SELECT * FROM notes_block_usages WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, sheet, row),
    ).fetchall():
        if (usage['generation_id'], usage['block_id']) not in active_blocks:
            continue
        sources.record_disposition_in_txn(
            conn, run_id, usage['generation_id'], usage['block_id'], Disposition(usage['disposition']),
            reason_code=usage['reason_code'], actor='human',
            note=f"Moved from {sheet} row {row} to {destination_sheet} row {destination_row}",
            sheet=destination_sheet, row=destination_row, concept_uuid=target['concept_uuid'],
            target_kind=usage['target_kind'], route_type=usage['route_type'],
        )
    # Relocate exact persisted coordinates, retaining every review status.
    for coverage in coverage_rows:
        items = json.loads(coverage['placements_json'] or '[]')
        changed = False
        for item in items:
            if item.get('sheet') == sheet and item.get('row') == row:
                item.update(sheet=destination_sheet, row=destination_row, row_label=target['label'])
                changed = True
        if changed:
            conn.execute("UPDATE notes_coverage_rows SET placements_json = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(items), now, coverage['id']))
    repo.add_notes_tombstone(conn, run_id=run_id, sheet=sheet, row=row)
    repo.remove_notes_tombstone(conn, run_id=run_id, sheet=destination_sheet, row=destination_row)
