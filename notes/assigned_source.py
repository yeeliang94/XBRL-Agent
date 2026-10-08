"""Resolve assigned whole notes and exact destination choices for source tools.

Accounting placement remains the agent's choice. Run, generation, source block
identity and worksheet coordinates are supplied by the application.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from db import repository as repo
from notes import source_repository as srepo, source_write

if TYPE_CHECKING:
    from notes.agent import NotesDeps
    from notes.integrity import IntegrityInput
    from notes.payload import NotesPayload


def _assigned_blocks(conn, deps, note_num: int):
    if deps.batch_note_nums is None or note_num not in deps.batch_note_nums:
        raise source_write.SourceWriteError("Choose a note in your assigned batch.")
    generation = srepo.fetch_generation(conn, deps.source_generation_id)
    if (generation is None or generation["run_id"] != deps.run_id
            or generation["status"] != "active"):
        raise source_write.SourceWriteError(
            "The assigned source is stale or belongs to another run. Stop this "
            "pass and report the source change; do not reuse its selections."
        )
    notes = [note for note in srepo.fetch_notes(conn, deps.source_generation_id)
             if str(note["top_note_num"]).strip() == str(note_num)]
    if len(notes) != 1:
        raise source_write.SourceWriteError(
            f"Note {note_num} does not identify one captured source note. "
            "Inspect the source sections or report the capture gap."
        )
    blocks = [block for block in source_write.load_blocks(conn, deps.source_generation_id)
              if block.source_note_id == notes[0]["source_note_id"]]
    if not blocks:
        raise source_write.SourceWriteError(
            f"Note {note_num} has no captured source content. Report the capture gap."
        )
    return notes[0], blocks


def assigned_note_content(deps, note_num: int) -> tuple[str, str]:
    """Return complete captured content without asking the agent to copy IDs."""
    with repo.db_session(deps.db_path) as conn:
        note, blocks = _assigned_blocks(conn, deps, note_num)
    parts = []
    for block in blocks:
        locator = block.locator or {}
        uncertain = (locator.get("capture_uncertain")
                     or locator.get("capture_method") == "reconstructed")
        provenance = ("; best-effort reconstruction; original wording is uncertain"
                      if uncertain else "")
        page = f"; PDF page {block.page}" if block.page is not None else ""
        parts.append(f"--- {block.block_kind}{page}{provenance} ---\n"
                     f"{block.canonical_html or ''}")
    label = f"Complete captured Note {note_num}: {note['title'] or ''}."
    return label, "\n".join(parts)


def assigned_note_target(deps, note_num: int, destination_label: str) -> tuple[int, str, list[str]]:
    """Resolve a chosen exact live label and all blocks of the assigned note."""
    prefix = f"{deps.filing_standard}-{deps.filing_level}-"
    with repo.db_session(deps.db_path) as conn:
        _, blocks = _assigned_blocks(conn, deps, note_num)
        candidates = [node for node in repo.list_notes_node_rows(
            conn, sheet=deps.sheet_name, template_prefix=prefix,
        ) if (node["kind"] or "").upper() == "LEAF"
            and (node["label"] or "").strip().casefold() == destination_label.strip().casefold()]
        if len(candidates) != 1:
            raise source_write.SourceWriteError(
                "The destination label must identify exactly one writable field "
                "in this filing's live template. Copy its label verbatim; use "
                "the section writer with an explicit row if the label is ambiguous."
            )
        target = candidates[0]
        source_write.resolve_target(
            conn, deps.sheet_name, target["row"], template_prefix=prefix,
            allowed_sheets=[deps.sheet_name],
        )
    return target["row"], target["label"], [block.block_id for block in blocks]


def assess_batch_source(deps: "NotesDeps", rows_by_label: dict[str, int]) -> tuple[set[int], list[str]]:
    """Validate split destinations and prepared coverage from one live snapshot."""
    if not deps.db_path or deps.source_generation_id is None:
        return set(), []
    from notes import integrity_runner
    from notes.source_models import INPUT_KIND_PREPARED

    with repo.db_session(deps.db_path) as conn:
        generation = srepo.fetch_generation(conn, deps.source_generation_id)
        if generation is None:
            return set(), []
        active = generation["run_id"] == deps.run_id and generation["status"] == "active"
        prepared = generation["input_kind"] == INPUT_KIND_PREPARED
        if not prepared and not (active and deps.payload_sink):
            return set(), []
        snapshot = integrity_runner.build_input(conn, deps.run_id, deps.source_generation_id)
        allowed = (_source_backed_split_notes(conn, deps, snapshot, rows_by_label)
                   if active else set())
        errors = _prepared_batch_source_errors(conn, deps, snapshot) if prepared else []
    return allowed, errors


def _prepared_batch_source_errors(conn, deps: "NotesDeps", snapshot: "IntegrityInput") -> list[str]:
    """A cross-sheet skip is valid only when its frozen source is accounted for."""
    from notes import integrity
    from notes.source_sections import sections_for_note
    from notes.source_write import PLACEMENT_CONFLICT_FINDING_PREFIX

    conflicted_parts: set[str] = set()
    for flag in conn.execute(
        "SELECT evidence FROM notes_review_flags WHERE run_id=? AND status='open' "
        "AND substr(finding_id, 1, ?) = ?",
        (deps.run_id, len(PLACEMENT_CONFLICT_FINDING_PREFIX),
         PLACEMENT_CONFLICT_FINDING_PREFIX),
    ):
        try:
            conflict = json.loads(flag["evidence"] or "{}")
        except (TypeError, ValueError):
            continue
        if (isinstance(conflict, dict)
                and conflict.get("generation_id") == deps.source_generation_id):
            conflicted_parts.update(conflict.get("block_ids") or [])
            conflicted_parts.update(conflict.get("proposed_block_ids") or [])
    by_number = {str(note.top_note_num): note for note in snapshot.notes}
    errors = []
    for finding in integrity.check_prose_note_coverage(snapshot):
        try:
            number = int(finding.note_num or "")
        except ValueError:
            continue
        if (number not in deps.batch_note_nums
                or number in deps.source_gap_notes):
            continue
        note = by_number.get(str(number))
        if note is None:
            continue
        missing = set(finding.block_ids) - conflicted_parts
        if not missing:
            continue
        sections = sections_for_note(
            snapshot.blocks, note.source_note_id, str(number), note.title,
        )
        names = [section.section_id for section in sections
                 if missing.intersection(section.block_ids)]
        preview = ", ".join(names[:5])
        if len(names) > 5:
            preview += f", and {len(names) - 5} more"
        part_preview = ", ".join(sorted(missing)[:5])
        if len(missing) > 5:
            part_preview += f", and {len(missing) - 5} more"
        errors.append(
            f"Note {number} has {len(missing)} source part(s) still unplaced "
            f"in {preview} (block IDs: {part_preview}). "
            "Place the complete disclosure sections on this "
            "sheet, or ensure the other sheet has placed them before claiming "
            "a cross-sheet skip."
        )
    return errors


def _source_backed_split_notes(conn, deps: "NotesDeps", snapshot: "IntegrityInput", rows_by_label: dict[str, int]) -> set[int]:
    """Permit multi-field receipts only for actual complete source sections.

    The canonical writer guards overlapping substantive placements. This check
    additionally refuses a fragment split inside the smallest source section.
    Accounting destination accuracy remains the grounded reviewer's judgment.
    """
    if not deps.db_path or deps.source_generation_id is None or not deps.payload_sink:
        return set()
    from notes.source_sections import sections_for_note

    by_note: dict[int, list[NotesPayload]] = {}
    for payload in deps.payload_sink:
        if payload.note_num is not None:
            by_note.setdefault(int(payload.note_num), []).append(payload)
    allowed = set()
    blocks = snapshot.blocks
    by_id = {block.block_id: block for block in blocks}
    matching_cells = {(cell.sheet, cell.row) for cell in snapshot.cells
                      if cell.selection_matches_content is True}
    for note in snapshot.notes:
        number = str(note.top_note_num or "")
        if not number.isdigit():
            continue
        payloads = by_note.get(int(number), [])
        if not payloads or not all(p.source_built and p.source_note_id == note.source_note_id for p in payloads):
            continue
        targets = set()
        valid = True
        for payload in payloads:
            row = rows_by_label.get(payload.chosen_row_label)
            if row is None:
                valid = False
                break
            target = (deps.sheet_name, row)
            cell = conn.execute(
                "SELECT source_generation_id FROM notes_cells WHERE run_id=? AND sheet=? AND row=?",
                (deps.run_id, *target),
            ).fetchone()
            if (cell is None or cell["source_generation_id"] != deps.source_generation_id
                    or target not in matching_cells):
                valid = False
                break
            targets.add(target)
        if not valid or len(targets) < 2:
            continue
        locations: dict[str, set[tuple[str, int]]] = {}
        for block_id, coordinates in snapshot.placements.items():
            block = by_id.get(block_id)
            if block and block.source_note_id == note.source_note_id:
                locations[block_id] = set(coordinates)
        section_parts = [
            {bid for bid in section.block_ids if by_id[bid].block_kind != "heading"}
            for section in sections_for_note(blocks, note.source_note_id, number, note.title or "")
        ]
        # A nested parent may contain multiple complete children. The
        # smallest section owns each substantive part's routing boundary.
        atoms_by_owner: dict[int, set[str]] = {}
        for bid in set().union(*section_parts):
            owner = min((index for index, part in enumerate(section_parts) if bid in part),
                        key=lambda index: len(section_parts[index]))
            atoms_by_owner.setdefault(owner, set()).add(bid)
        atoms = list(atoms_by_owner.values())
        if not atoms:
            continue
        for atom in atoms:
            # Approved capital prose may also live in its numeric template.
            # Its List-of-Notes placement still must keep this section intact.
            atom_targets = [
                {coord for coord in locations.get(bid, set()) if coord[0] == deps.sheet_name}
                if bid in snapshot.approved_duplicate_block_ids
                else locations.get(bid, set())
                for bid in atom
            ]
            if (not all(len(coords) == 1 for coords in atom_targets)
                    or len(set.union(*atom_targets)) != 1):
                valid = False
                break
        # Every claimed destination must hold substantive parts of this note.
        actual_targets = set().union(*(coords for bid, coords in locations.items()
                                     if by_id[bid].block_kind != "heading"))
        if valid and targets == {target for target in actual_targets if target[0] == deps.sheet_name}:
            allowed.add(int(number))
    return allowed
