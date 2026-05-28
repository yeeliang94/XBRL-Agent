"""Cross-sheet rollup regression — face computed totals must include
sub-sheet rolled-up children after the v11 alias fix.

Pre-fix bug (CLAUDE.md gotcha #21): the importer dedupes face + sub
concepts to one concept_uuid anchored at the sub-sheet's render coord,
then resolves edge children via ``(render_sheet, render_row)``
lookups. Face-row coords are gone from concept_nodes after dedup, so
every edge from a face-sheet COMPUTED total to a face-sheet child
that cross-rolls-up from a sub-sheet (like "Property, plant and
equipment" → SOFP-Sub-CuNonCu *Total) was silently dropped. Cascade
then summed only the surviving children, understating face totals.

The v11 fix preserves the demoted face render coord in
``concept_render_aliases`` and builds the importer's edge-resolution
coord map from the FULL concept list (not the dedup'd ``seen``), so
face refs resolve to the shared canonical UUID and the edges land.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import openpyxl
import pytest

from concept_model.cascade import recompute_after_turn
from concept_model.importer import import_template
from concept_model.parser import parse_template
from db.schema import init_db


REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def seeded(tmp_path: Path):
    db = tmp_path / "xbrl.db"
    init_db(db)

    tree = parse_template(str(FIXTURE))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    template_id = import_template(db, jp)

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-28T00:00:00Z", "x.pdf", "running",
             "2026-05-28T00:00:00Z"),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return db, run_id, template_id


def _set_fact(db: Path, run_id: int, uid: str, value: float) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO run_concept_facts("
            "run_id, concept_uuid, period, entity_scope, value, "
            "value_status, source, updated_at) "
            "VALUES (?, ?, 'CY', 'Company', ?, 'observed', "
            "'pdf p.1', '2026-05-28Z')",
            (run_id, uid, value),
        )
        conn.commit()
    finally:
        conn.close()


def _uuid_at(db: Path, sheet: str, row: int) -> str | None:
    conn = sqlite3.connect(str(db))
    try:
        r = conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE render_sheet = ? AND render_row = ?",
            (sheet, row),
        ).fetchone()
    finally:
        conn.close()
    return r[0] if r else None


def test_face_row_alias_preserves_sub_concept_uuid(seeded):
    """The face PPE row at SOFP-CuNonCu and the sub *Total PPE row at
    SOFP-Sub-CuNonCu share one concept_uuid; the face coord lives in
    concept_render_aliases pointing at that shared UUID."""
    db, _run_id, _template_id = seeded
    sub_uid = _uuid_at(db, "SOFP-Sub-CuNonCu", 39)
    assert sub_uid is not None, "*Total PPE missing from concept_nodes"

    # The face row's coord must appear as an alias linked to the same
    # canonical concept_uuid as the sub-sheet's *Total PPE.
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT alias_sheet, alias_row FROM concept_render_aliases "
            "WHERE concept_uuid = ?",
            (sub_uid,),
        ).fetchall()
    finally:
        conn.close()
    face_coords = {(r[0], int(r[1])) for r in rows}
    # The exact face row number is template-defined (and may shift if
    # the template is regenerated). What matters: there's at least
    # one alias on the FACE sheet (SOFP-CuNonCu, not the sub-sheet
    # itself) linked to the *Total PPE concept_uuid.
    face_aliases = {c for c in face_coords if c[0] == "SOFP-CuNonCu"}
    assert face_aliases, (
        f"no face-sheet alias for *Total PPE ({sub_uid}); the "
        f"cross-sheet linkage was lost during import. Saw "
        f"{face_coords!r}"
    )


def test_face_computed_total_includes_cross_sheet_rolled_up_child(seeded):
    """The smoking-gun regression. A face computed total on
    SOFP-CuNonCu (e.g. *Total non-current assets at row 25) must
    include the cross-sheet-rolled-up PPE child in its cascade-summed
    value. Pre-fix the edge to PPE was silently dropped and the total
    excluded PPE; with v11 the edge lands and PPE is included."""
    db, run_id, template_id = seeded

    # Find a face-sheet COMPUTED row whose formula references at least
    # one cross-sheet-rolled-up child. *Total non-current assets is the
    # canonical example on SOFP-CuNonCu; assert by canonical_label
    # rather than hardcoding a row number so the test survives template
    # tweaks. Then assert it has an edge to the sub-sheet *Total PPE.
    sub_ppe_uid = _uuid_at(db, "SOFP-Sub-CuNonCu", 39)
    assert sub_ppe_uid is not None

    conn = sqlite3.connect(str(db))
    try:
        # Find every face-sheet COMPUTED concept on SOFP-CuNonCu that
        # has an edge to *Total PPE — pre-fix this set is empty
        # because the edge was dropped.
        rows = conn.execute(
            """
            SELECT n.concept_uuid, n.canonical_label
            FROM concept_nodes n
            JOIN concept_edges e ON e.parent_uuid = n.concept_uuid
            WHERE n.template_id = ?
              AND n.render_sheet = 'SOFP-CuNonCu'
              AND n.kind = 'COMPUTED'
              AND e.child_uuid = ?
            """,
            (template_id, sub_ppe_uid),
        ).fetchall()
    finally:
        conn.close()
    assert rows, (
        "No face-sheet COMPUTED row on SOFP-CuNonCu has an edge to "
        "*Total PPE. The importer dropped the cross-sheet edge, so "
        "cascade will understate every face total that should include "
        "PPE."
    )


def test_cascade_face_total_picks_up_sub_sheet_rollup(seeded):
    """End-to-end: post a fact on the sub-sheet *Total PPE row,
    cascade, then read the face-sheet computed parent's value. The
    parent value must include PPE's contribution (i.e. the rolled-up
    child)."""
    db, run_id, template_id = seeded

    sub_ppe_uid = _uuid_at(db, "SOFP-Sub-CuNonCu", 39)
    assert sub_ppe_uid is not None

    # Seed PPE total directly (skip the per-leaf cascade; we want a
    # focused test that the FACE total picks up the SUB total, not a
    # multi-hop test).
    _set_fact(db, run_id, sub_ppe_uid, 5_000_000.0)

    # Also seed zeros on every OTHER itemised sibling on the face sheet
    # so the cascade can mark face totals "observed". Without facts on
    # the siblings the cascade may leave the parent as not_computed.
    # We don't need exact values — only that PPE's contribution is
    # carried up. To keep the assertion focused, we read the cascade
    # output for any face-sheet COMPUTED row that has PPE as a child
    # and confirm it includes 5,000,000 in its sum.
    conn = sqlite3.connect(str(db))
    try:
        face_parent_rows = conn.execute(
            """
            SELECT DISTINCT n.concept_uuid
            FROM concept_nodes n
            JOIN concept_edges e ON e.parent_uuid = n.concept_uuid
            WHERE n.template_id = ?
              AND n.render_sheet = 'SOFP-CuNonCu'
              AND n.kind = 'COMPUTED'
              AND e.child_uuid = ?
            """,
            (template_id, sub_ppe_uid),
        ).fetchall()
        face_parent_uids = [r[0] for r in face_parent_rows]
        # Seed zeros on every other child of every such parent so the
        # cascade has a value for every input.
        for parent_uid in face_parent_uids:
            for (child_uid,) in conn.execute(
                "SELECT child_uuid FROM concept_edges WHERE parent_uuid = ?",
                (parent_uid,),
            ).fetchall():
                if child_uid == sub_ppe_uid:
                    continue
                existing = conn.execute(
                    "SELECT 1 FROM run_concept_facts "
                    "WHERE run_id = ? AND concept_uuid = ? "
                    "AND period = 'CY' AND entity_scope = 'Company'",
                    (run_id, child_uid),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO run_concept_facts("
                        "run_id, concept_uuid, period, entity_scope, "
                        "value, value_status, source, updated_at) "
                        "VALUES (?, ?, 'CY', 'Company', 0.0, "
                        "'observed', 'pin', '2026-05-28Z')",
                        (run_id, child_uid),
                    )
        conn.commit()
    finally:
        conn.close()

    recompute_after_turn(db, run_id)

    # Read back the face parent values. At least one must be ≥ PPE.
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            """
            SELECT n.canonical_label, f.value
            FROM concept_nodes n
            JOIN run_concept_facts f ON f.concept_uuid = n.concept_uuid
            WHERE n.concept_uuid IN (
              SELECT DISTINCT n2.concept_uuid
              FROM concept_nodes n2
              JOIN concept_edges e ON e.parent_uuid = n2.concept_uuid
              WHERE n2.render_sheet = 'SOFP-CuNonCu'
                AND n2.kind = 'COMPUTED'
                AND e.child_uuid = ?
            )
            AND f.run_id = ? AND f.period = 'CY'
            AND f.entity_scope = 'Company'
            """,
            (sub_ppe_uid, run_id),
        ).fetchall()
    finally:
        conn.close()

    assert rows, "no face computed parent has a fact after cascade"
    assert any(
        (r[1] or 0.0) >= 5_000_000.0 for r in rows
    ), (
        "Face computed parent did NOT pick up the PPE sub-sheet "
        f"rollup. Parents seen: {rows!r}"
    )


def test_resolve_cell_falls_back_to_alias_for_face_coord(seeded):
    """An agent write to a face-sheet cell whose row only exists as
    an alias (because the same concept's primary lives on the sub
    sheet) must still resolve to the canonical concept_uuid. Pre-fix
    `resolve_cell` returned None and the write was silently skipped.
    """
    from concept_model.cell_resolver import resolve_cell

    db, _run_id, template_id = seeded
    sub_uid = _uuid_at(db, "SOFP-Sub-CuNonCu", 39)
    assert sub_uid is not None

    # Discover one face-sheet alias coord linked to that concept.
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT alias_row FROM concept_render_aliases "
            "WHERE concept_uuid = ? AND alias_sheet = 'SOFP-CuNonCu'",
            (sub_uid,),
        ).fetchone()
        assert row is not None, "no face alias to test against"
        face_row = int(row[0])

        resolved = resolve_cell(conn, template_id, "SOFP-CuNonCu", face_row, 2)
    finally:
        conn.close()

    assert resolved is not None, (
        "resolve_cell returned None for a face coord that has an "
        "alias — agent writes to that cell would be silently dropped."
    )
    concept_uuid, period, entity_scope = resolved
    assert concept_uuid == sub_uid
    assert period == "CY"
    assert entity_scope == "Company"


def test_exporter_preserves_cross_sheet_formula_on_alias_coord(seeded, tmp_path):
    """The exporter writes facts via concept_nodes' primary coord. It
    must NOT touch the alias coord (the face cell holding the live
    cross-sheet formula like ='SOFP-Sub-CuNonCu'!B39). If it did, the
    next time Excel opened the workbook the rolled-up value would be a
    stale literal snapshot and the formula chain would be broken.
    """
    from concept_model.exporter import export_run_to_xlsx

    db, run_id, _template_id = seeded
    sub_uid = _uuid_at(db, "SOFP-Sub-CuNonCu", 39)
    assert sub_uid is not None

    # Seed a literal value on the sub-sheet *Total PPE concept; this
    # WILL get written to SOFP-Sub-CuNonCu B39. The face cell
    # SOFP-CuNonCu Bn (whichever row carries the cross-sheet formula)
    # must stay as the formula string.
    _set_fact(db, run_id, sub_uid, 7_777_777.0)

    # Discover the face alias coord(s).
    conn = sqlite3.connect(str(db))
    try:
        alias_rows = conn.execute(
            "SELECT alias_sheet, alias_row, alias_col FROM "
            "concept_render_aliases WHERE concept_uuid = ?",
            (sub_uid,),
        ).fetchall()
    finally:
        conn.close()
    face_aliases = [r for r in alias_rows if r[0] == "SOFP-CuNonCu"]
    assert face_aliases, "test setup: no face alias to assert against"

    work = tmp_path / "filled.xlsx"
    shutil.copyfile(FIXTURE, work)
    export_run_to_xlsx(db, run_id, str(work))

    wb = openpyxl.load_workbook(str(work), data_only=False)
    # Primary sub-sheet coord got the literal write.
    assert wb["SOFP-Sub-CuNonCu"]["B39"].value == 7_777_777.0
    # Every face alias coord still carries a cross-sheet formula —
    # NEVER the literal value. (If a future change writes literals
    # here, this test fires.)
    for a_sheet, a_row, a_col in face_aliases:
        cell = wb[a_sheet][f"{a_col}{int(a_row)}"]
        assert isinstance(cell.value, str) and cell.value.startswith("="), (
            f"Alias coord {a_sheet}!{a_col}{a_row} no longer holds a "
            f"formula: {cell.value!r}. The exporter clobbered the "
            f"cross-sheet linkage."
        )
        assert "SOFP-Sub-CuNonCu" in cell.value, (
            f"Alias formula at {a_sheet}!{a_col}{a_row} no longer "
            f"references the sub sheet: {cell.value!r}"
        )
