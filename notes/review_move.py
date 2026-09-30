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


def reconcile_reviewed_placement_conflicts(
    conn: sqlite3.Connection, *, run_id: int,
) -> int:
    """Close same-block proposals whose complete source selection was moved.

    Reviewer writes can satisfy a proposal through a cell move or relink, not
    only through resolve_placement_conflict. The active placement ledger is
    authoritative for whether the original conflict still exists.
    """
    generation = sources.active_generation(conn, run_id)
    if generation is None:
        return 0
    locations: dict[str, set[tuple[str, int]]] = {}
    for placement in sources.active_placements(conn, generation["id"]):
        locations.setdefault(placement["block_id"], set()).add(
            (placement["sheet"], placement["row"])
        )
    block_kinds = {
        row["block_id"]: row["block_kind"]
        for row in conn.execute(
            "SELECT block_id,block_kind FROM notes_source_blocks WHERE generation_id=?",
            (generation["id"],),
        )
    }
    settled = 0
    for flag in repo.fetch_notes_review_flags(conn, run_id):
        if flag["status"] != "open":
            continue
        try:
            conflict = json.loads(flag.get("evidence") or "{}")
        except (TypeError, ValueError):
            continue
        if (not isinstance(conflict, dict)
                or conflict.get("match_kind") != "same_block"
                or conflict.get("generation_id") != generation["id"]):
            continue
        target = conflict.get("target") or {}
        coordinate = (target.get("sheet"), target.get("row"))
        selected = set(conflict.get("proposed_block_ids") or [])
        if not coordinate[0] or not isinstance(coordinate[1], int) or not selected:
            continue
        if all(
            coordinate in locations.get(block_id, set())
            and (block_kinds.get(block_id) == "heading"
                 or locations[block_id] == {coordinate})
            for block_id in selected
        ):
            repo.answer_notes_review_flag(
                conn, flag_id=flag["id"], run_id=run_id,
                answer="Reviewer placed every proposed source part in the proposed field.",
            )
            settled += 1
    return settled


def transfer_source_sections(
    conn: sqlite3.Connection, *, run_id: int, generation_id: int,
    source_sheet: str, source_row: int,
    destination_sheet: str, destination_row: int,
    section_ids: list[str], template_prefix: str,
    evidence: str | None = None,
) -> None:
    """Move complete frozen sections out of a mixed cell in one transaction.

    Both cells are rebuilt by the existing source writer. A failed destination
    write rolls back the source change with the caller's transaction.
    """
    from notes import source_write
    from notes.source_sections import expand_section_ids
    from notes.html_to_text import rendered_length

    if (source_sheet, source_row) == (destination_sheet, destination_row):
        raise MoveConflict("Choose a different destination field.")
    if not section_ids or any(not item.startswith("section:") for item in section_ids):
        raise MoveConflict("Select complete source section IDs for a transfer.")
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    generation = sources.fetch_generation(conn, generation_id)
    if (generation is None or generation["run_id"] != run_id
            or generation["status"] != "active"):
        raise MoveConflict("The source generation changed. Reload the review.")
    origin = conn.execute(
        "SELECT label, content_revision, content_origin FROM notes_cells "
        "WHERE run_id=? AND sheet=? AND row=?",
        (run_id, source_sheet, source_row),
    ).fetchone()
    if origin is None or origin["content_origin"] == "human_modified":
        raise MoveConflict("The source cell is absent or contains a human edit.")
    target = conn.execute(
        "SELECT content_revision, html, source_generation_id FROM notes_cells "
        "WHERE run_id=? AND sheet=? AND row=?",
        (run_id, destination_sheet, destination_row),
    ).fetchone()
    if target and (rendered_length(target["html"] or "")
                   or target["source_generation_id"] is not None):
        raise MoveConflict("The destination contains content or source records.")
    available = source_write.load_blocks(conn, generation_id)
    try:
        moving = set(expand_section_ids(
            available, sources.fetch_notes(conn, generation_id), section_ids,
        ))
    except ValueError as exc:
        raise MoveConflict(str(exc)) from exc
    active = {item["block_id"] for item in conn.execute(
        "SELECT block_id FROM notes_block_placements WHERE run_id=? "
        "AND generation_id=? AND sheet=? AND row=? AND active=1",
        (run_id, generation_id, source_sheet, source_row),
    )}
    if not moving or not moving <= active:
        raise MoveConflict("The selected section is not wholly in the source cell.")
    remaining = active - moving
    by_id = {block.block_id: block for block in available}
    if not any(by_id[bid].block_kind != "heading" for bid in remaining):
        raise MoveConflict("This is a whole-cell move; use move_note_cell instead.")
    source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=generation_id,
        sheet=source_sheet, row=source_row,
        block_ids=sorted(remaining, key=lambda bid: by_id[bid].reading_order),
        label=origin["label"] or "", actor="notes_reviewer",
        evidence=evidence, template_prefix=template_prefix,
        expected_revision=origin["content_revision"],
        allow_unplaced_during_conflict_resolution=True,
    )
    source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=generation_id,
        sheet=destination_sheet, row=destination_row,
        block_ids=section_ids, actor="notes_reviewer", evidence=evidence,
        template_prefix=template_prefix,
        expected_revision=target["content_revision"] if target else None,
    )
    placed = {item["block_id"] for item in conn.execute(
        "SELECT block_id FROM notes_block_placements WHERE run_id=? "
        "AND generation_id=? AND sheet=? AND row=? AND active=1",
        (run_id, generation_id, destination_sheet, destination_row),
    )}
    if not moving <= placed:
        raise MoveConflict("The destination did not retain every selected source part.")


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


FIELD_COLLISION_DECISIONS = frozenset({"keep_existing", "use_proposed", "combine"})


def resolve_field_collision(
    conn: sqlite3.Connection, *, run_id: int, flag_id: int, finding_id: str,
    decision: str, answer: str, template_prefix: str,
    other_sheet: str | None = None, other_row: int | None = None,
) -> list[tuple[str, int]]:
    """Settle two notes proposed for one field. Caller owns ``BEGIN IMMEDIATE``.

    ``keep_existing`` leaves the field as it is; ``use_proposed`` puts the
    proposed note there instead; ``combine`` keeps both in the one field.
    For the first two, ``other_row`` (and optionally ``other_sheet``) names an
    empty field, or the catch-all, for the note that did not win the field.
    A decision cannot strand the other note without a destination.
    Every write goes through the source writer, so lineage, dispositions and
    the duplicate guard behave exactly as for any other placement. Returns the
    cells written.
    """
    from notes import source_write

    flag = conn.execute(
        "SELECT evidence FROM notes_review_flags "
        "WHERE id=? AND run_id=? AND finding_id=? AND status='open'",
        (flag_id, run_id, finding_id),
    ).fetchone()
    if flag is None:
        raise MoveConflict("The field conflict changed. Reload the review packet.")
    conflict = json.loads(flag["evidence"])
    if conflict.get("match_kind") != "same_field":
        raise MoveConflict("This is not a field conflict.")
    if decision not in FIELD_COLLISION_DECISIONS:
        raise MoveConflict("decision must be keep_existing, use_proposed or combine.")
    if decision in {"keep_existing", "use_proposed"} and other_row is None:
        raise MoveConflict(
            "Choose other_row for the note that does not win this field, "
            "or leave the conflict open for human review."
        )
    generation = sources.active_generation(conn, run_id)
    if generation is None or generation["id"] != conflict["generation_id"]:
        raise MoveConflict("The source generation changed. Reload the review packet.")
    target = conflict["target"]
    sheet, row = target["sheet"], int(target["row"])
    existing_ids = sorted(set(conflict.get("block_ids") or []))
    proposed_ids = sorted(set(conflict.get("proposed_block_ids") or []))
    if not existing_ids or not proposed_ids:
        raise MoveConflict("The field conflict needs human review; its selection is unavailable.")
    live_ids = {
        item["block_id"] for item in conn.execute(
            "SELECT block_id FROM notes_block_placements "
            "WHERE run_id=? AND generation_id=? AND sheet=? AND row=? AND active=1",
            (run_id, generation["id"], sheet, row),
        ).fetchall()
    }
    if live_ids != set(existing_ids):
        raise MoveConflict(
            "The field changed since the conflict was recorded. Reload the review packet."
        )

    if decision == "combine":
        current = conn.execute(
            "SELECT label FROM notes_cells WHERE run_id=? AND sheet=? AND row=?",
            (run_id, sheet, row),
        ).fetchone()
        if not current or not source_write._is_list_catch_all(sheet, current["label"] or ""):
            raise MoveConflict(
                "Distinct source notes may be combined automatically only in "
                "the List-of-Notes catch-all field. Choose a separate field "
                "for the other note or request human review."
            )

    if decision == "combine" and other_row is not None:
        raise MoveConflict("combine keeps both notes in the one field; do not name another field.")
    destination = (other_sheet or sheet, int(other_row)) if other_row is not None else None
    if destination == (sheet, row):
        raise MoveConflict("name a different field for the other note.")
    if destination is not None:
        occupied = conn.execute(
            "SELECT label, html FROM notes_cells WHERE run_id=? AND sheet=? AND row=?",
            (run_id, *destination),
        ).fetchone()
        if (occupied is not None and (occupied["html"] or "").strip()
                and not source_write._is_list_catch_all(destination[0], occupied["label"] or "")):
            raise MoveConflict(
                f"{destination[0]} row {destination[1]} already holds content. "
                "Choose an empty field or the catch-all field."
            )

    def write(cell: tuple[str, int], block_ids: list[str], *, combine: bool = False,
              replacing_conflict: bool = False) -> None:
        label_row = conn.execute(
            "SELECT label FROM notes_cells WHERE run_id=? AND sheet=? AND row=?",
            (run_id, *cell),
        ).fetchone()
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=generation["id"],
            sheet=cell[0], row=cell[1], block_ids=block_ids,
            label=(label_row["label"] if label_row else "") or "",
            evidence=answer, actor="notes_reviewer",
            template_prefix=template_prefix, combine_notes=combine,
            allow_unplaced_during_conflict_resolution=replacing_conflict,
        )

    written: list[tuple[str, int]] = []
    if decision == "combine":
        write((sheet, row), sorted(set(existing_ids) | set(proposed_ids)), combine=True)
        written.append((sheet, row))
    elif decision == "use_proposed":
        # Replace first so the displaced note is unplaced before it moves;
        # the duplicate guard would otherwise see it in two fields.
        write((sheet, row), proposed_ids, replacing_conflict=True)
        written.append((sheet, row))
        if destination is not None:
            write(destination, existing_ids)
            written.append(destination)
    elif destination is not None:
        write(destination, proposed_ids)
        written.append(destination)
    if not repo.answer_notes_review_flag(conn, flag_id=flag_id, run_id=run_id, answer=answer):
        raise MoveConflict("The field conflict changed. Reload the review packet.")
    return written
