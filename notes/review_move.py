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
    """Move after active work is idle. Caller owns ``BEGIN IMMEDIATE``."""
    _move_note(
        conn,
        run_id=run_id,
        sheet=sheet,
        row=row,
        destination_sheet=destination_sheet,
        destination_row=destination_row,
        expected_revision=expected_revision,
        destination_revision=destination_revision,
        require_idle=True,
    )


def move_note_during_review(
    conn: sqlite3.Connection, *, run_id: int, sheet: str, row: int,
    destination_sheet: str, destination_row: int,
    expected_revision: int, destination_revision: int | None,
) -> None:
    """Move inside the automatic reviewer's serialised write seam."""
    _move_note(
        conn,
        run_id=run_id,
        sheet=sheet,
        row=row,
        destination_sheet=destination_sheet,
        destination_row=destination_row,
        expected_revision=expected_revision,
        destination_revision=destination_revision,
        require_idle=False,
    )


def _move_note(
    conn: sqlite3.Connection, *, run_id: int, sheet: str, row: int,
    destination_sheet: str, destination_row: int,
    expected_revision: int, destination_revision: int | None,
    require_idle: bool,
) -> None:
    """Relocate one cell and every canonical ledger in the caller's txn."""
    run = repo.fetch_run(conn, run_id)
    if run is None:
        raise LookupError("Run not found")
    if require_idle and run.status in {"running", "pending"}:
        raise MoveConflict("Wait for extraction to finish before moving a note.")
    if require_idle:
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
    if target is None and not require_idle:
        node = repo.fetch_notes_node(
            conn,
            sheet=destination_sheet,
            row=destination_row,
            template_prefix=family,
        )
        if (
            node
            and str(node.get("kind") or "").upper() == "LEAF"
            and str(node.get("slot_role") or "").upper() == "INPUT"
        ):
            target = {
                "label": node.get("label") or "",
                "concept_uuid": node.get("node_uuid"),
            }
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


def resolve_source_placement_conflict(
    conn: sqlite3.Connection, *, run_id: int, flag_id: int,
    finding_id: str, decision: str, answer: str,
) -> None:
    """Validate the recorded proposal and settle it in the caller's transaction.

    A whole-cell move is safe only for a whole-cell proposal. Older findings
    without a recorded revision/selection remain open for human review.
    """
    flag = conn.execute(
        "SELECT evidence FROM notes_review_flags "
        "WHERE id=? AND run_id=? AND finding_id=? AND status='open'",
        (flag_id, run_id, finding_id),
    ).fetchone()
    if flag is None:
        raise MoveConflict("The placement conflict changed. Reload the review packet.")
    conflict = json.loads(flag["evidence"])
    existing = conflict.get("existing") or []
    if len(existing) != 1 or decision not in {"keep_existing", "move_to_proposed"}:
        raise MoveConflict("This placement conflict requires human review.")
    source = existing[0]
    generation = sources.active_generation(conn, run_id)
    if generation is None or generation["id"] != conflict["generation_id"]:
        raise MoveConflict("The source generation changed. Reload the review packet.")
    cell = conn.execute(
        "SELECT * FROM notes_cells WHERE run_id=? AND sheet=? AND row=?",
        (run_id, source["sheet"], source["row"]),
    ).fetchone()
    if (cell is None or cell["content_revision"] != source.get("content_revision")
            or cell["source_generation_id"] != generation["id"]):
        raise MoveConflict("The source placement changed. Reload the review packet.")
    placements = conn.execute(
        "SELECT block_id FROM notes_block_placements "
        "WHERE run_id=? AND generation_id=? AND sheet=? AND row=? AND active=1",
        (run_id, generation["id"], source["sheet"], source["row"]),
    ).fetchall()
    live_ids = {item["block_id"] for item in placements}
    proposed_ids = set(conflict.get("proposed_block_ids") or [])
    if not live_ids or not proposed_ids:
        raise MoveConflict("The source placement needs human review; its selection is unavailable.")
    if conflict["match_kind"] == "same_block":
        if not set(conflict["block_ids"]) <= live_ids:
            raise MoveConflict("The source placement changed. Reload the review packet.")
        if decision == "move_to_proposed" and live_ids != proposed_ids:
            raise MoveConflict(
                "This proposal covers only part of a cell or adds other source parts. "
                "Leave it open for human review; a whole-cell move would change the proposal."
            )
    elif conflict["match_kind"] != "same_render":
        raise MoveConflict("This placement conflict requires human review.")
    if decision == "move_to_proposed":
        target = conflict["target"]
        destination = conn.execute(
            "SELECT content_revision FROM notes_cells WHERE run_id=? AND sheet=? AND row=?",
            (run_id, target["sheet"], target["row"]),
        ).fetchone()
        move_note_during_review(
            conn, run_id=run_id, sheet=source["sheet"], row=source["row"],
            destination_sheet=target["sheet"], destination_row=target["row"],
            expected_revision=cell["content_revision"],
            destination_revision=destination["content_revision"] if destination else None,
        )
    if not repo.answer_notes_review_flag(conn, flag_id=flag_id, run_id=run_id, answer=answer):
        raise MoveConflict("The placement conflict changed. Reload the review packet.")
