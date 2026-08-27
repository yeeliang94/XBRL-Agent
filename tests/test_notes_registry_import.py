"""Notes registry bootstrap — Track A (prose) + Track B (numeric).

PLAN-notes-template-registry Phase 2. import_all_notes_templates imports the 5
notes templates across {mfrs,mpers} × {company,group}:
  * prose (CORP_INFO, ACC_POLICIES, LIST_OF_NOTES) → notes_nodes (12 templates);
  * numeric (ISSUED_CAPITAL, RELATED_PARTY) → concept_nodes + concept_targets
    (8 templates), reusing the face pipeline.

Pins: the prose/numeric split lands in the right tables, prose ids are
template-scoped (no MFRS/MPERS × Company/Group collision), and re-import is
idempotent.
"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_db
from concept_model.bootstrap import import_all_notes_templates


# 3 prose + 2 numeric note types, each across 2 standards × 2 levels.
_PROSE_TEMPLATES = 3 * 2 * 2   # → notes_nodes
_NUMERIC_TEMPLATES = 2 * 2 * 2  # → concept_nodes


@pytest.fixture()
def imported_db(tmp_path):
    db = tmp_path / "notes.db"
    init_db(db)
    ids = import_all_notes_templates(db)
    return db, ids


def _conn(db):
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    return c


def test_imports_all_twenty_templates(imported_db):
    _db, ids = imported_db
    assert len(ids) == _PROSE_TEMPLATES + _NUMERIC_TEMPLATES  # 20


def test_prose_lands_in_notes_nodes_only(imported_db):
    db, _ids = imported_db
    conn = _conn(db)
    try:
        prose_templates = conn.execute(
            "SELECT COUNT(DISTINCT template_id) FROM notes_nodes"
        ).fetchone()[0]
        assert prose_templates == _PROSE_TEMPLATES

        # Prose template_ids must NOT appear in concept_nodes (kept separate).
        leaked = conn.execute(
            "SELECT COUNT(*) FROM concept_nodes "
            "WHERE template_id IN (SELECT DISTINCT template_id FROM notes_nodes)"
        ).fetchone()[0]
        assert leaked == 0

        # Prose registry carries both fillable LEAF rows and ABSTRACT headers.
        kinds = {
            r[0] for r in conn.execute("SELECT DISTINCT kind FROM notes_nodes")
        }
        assert "LEAF" in kinds
        assert "ABSTRACT" in kinds
        assert kinds <= {"LEAF", "ABSTRACT"}
    finally:
        conn.close()


def test_numeric_lands_in_concept_model(imported_db):
    db, _ids = imported_db
    conn = _conn(db)
    try:
        numeric_templates = conn.execute(
            "SELECT COUNT(DISTINCT template_id) FROM concept_nodes "
            "WHERE template_id LIKE '%-notes-%'"
        ).fetchone()[0]
        assert numeric_templates == _NUMERIC_TEMPLATES

        # Numeric notes get per-scope render targets like any face statement.
        targets = conn.execute(
            "SELECT COUNT(*) FROM concept_targets t "
            "JOIN concept_nodes n ON n.concept_uuid = t.concept_uuid "
            "WHERE n.template_id LIKE '%-notes-%'"
        ).fetchone()[0]
        assert targets > 0

        # And they are NOT in the prose registry.
        leaked = conn.execute(
            "SELECT COUNT(*) FROM notes_nodes WHERE template_id LIKE '%-notes-%' "
            "AND template_id IN (SELECT DISTINCT template_id FROM concept_nodes)"
        ).fetchone()[0]
        assert leaked == 0

        # v41: numeric notes share the same slot manifest as face statements;
        # the two reviewed MFRS wrappers no longer leave these variants at 0%.
        coverage = conn.execute(
            "SELECT template_id, COUNT(*) AS total, "
            "SUM(CASE WHEN slot_role = 'INPUT' AND taxonomy_element_id IS NOT NULL "
            "THEN 1 ELSE 0 END) AS mapped_inputs "
            "FROM template_slots WHERE template_id LIKE '%-notes-%' "
            "AND value_kind = 'numeric' "
            "GROUP BY template_id"
        ).fetchall()
        assert len(coverage) == _NUMERIC_TEMPLATES
        assert all(row["mapped_inputs"] > 0 for row in coverage)
    finally:
        conn.close()


def test_prose_ids_are_template_scoped(imported_db):
    """The same (sheet, row, label) under different families gets distinct ids.

    This is the collision the template-scoped node_uuid prevents — without it,
    MFRS-Company and MFRS-Group Corporate-Info rows would share one PK.
    """
    db, _ids = imported_db
    conn = _conn(db)
    try:
        rows = conn.execute(
            "SELECT template_id, node_uuid FROM notes_nodes "
            "WHERE sheet = 'Notes-CI' AND label = 'Corporate information'"
        ).fetchall()
        by_template = {r["template_id"]: r["node_uuid"] for r in rows}
        # Present in all four families (both standards × both levels).
        assert len(by_template) == 4
        # All four uuids are distinct.
        assert len(set(by_template.values())) == 4
    finally:
        conn.close()


def test_reimport_is_idempotent(imported_db):
    db, _ids = imported_db
    conn = _conn(db)
    try:
        before_prose = conn.execute("SELECT COUNT(*) FROM notes_nodes").fetchone()[0]
        before_concept = conn.execute(
            "SELECT COUNT(*) FROM concept_nodes WHERE template_id LIKE '%-notes-%'"
        ).fetchone()[0]
    finally:
        conn.close()

    import_all_notes_templates(db)  # second pass

    conn = _conn(db)
    try:
        after_prose = conn.execute("SELECT COUNT(*) FROM notes_nodes").fetchone()[0]
        after_concept = conn.execute(
            "SELECT COUNT(*) FROM concept_nodes WHERE template_id LIKE '%-notes-%'"
        ).fetchone()[0]
        assert after_prose == before_prose
        assert after_concept == before_concept
    finally:
        conn.close()


def test_prose_registry_persists_taxonomy_and_slot_semantics(imported_db):
    db, _ids = imported_db
    conn = _conn(db)
    try:
        status = conn.execute(
            "SELECT kind, slot_role, taxonomy_element_id, manifest_version "
            "FROM notes_nodes WHERE template_id = ? AND label = ?",
            (
                "mfrs-company-notes-corporateinfo-v1",
                "Financial reporting status",
            ),
        ).fetchone()
        assert dict(status) == {
            "kind": "ABSTRACT",
            "slot_role": "PRESENTATION_ONLY",
            "taxonomy_element_id": "ssmt-mfrs_FinancialReportingStatusAbstract",
            "manifest_version": "2022-v1-slot-semantics-1",
        }
        assert conn.execute("SELECT COUNT(*) FROM taxonomy_concepts").fetchone()[0] > 0
        assert conn.execute(
            "SELECT COUNT(DISTINCT template_id) FROM template_slots"
        ).fetchone()[0] == _PROSE_TEMPLATES + _NUMERIC_TEMPLATES
    finally:
        conn.close()


def test_notes_persistence_uses_the_registry_node_identity(imported_db):
    import json
    from notes.persistence import persist_notes_cells

    db, _ids = imported_db
    conn = _conn(db)
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, run_config_json) "
            "VALUES ('t', 'x.pdf', 'running', ?)",
            (json.dumps({
                "filing_standard": "mfrs",
                "filing_level": "company",
            }),),
        ).lastrowid
        expected = conn.execute(
            "SELECT node_uuid FROM notes_nodes WHERE template_id = ? AND row = 5",
            ("mfrs-company-notes-corporateinfo-v1",),
        ).fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    persist_notes_cells(
        db_path=str(db),
        run_id=run_id,
        sheet_name="Notes-CI",
        cells_written=[{
            "sheet": "Notes-CI",
            "row": 5,
            "label": "*Disclosure of corporate information",
            "html": "<p>Registered in Malaysia.</p>",
        }],
    )

    conn = _conn(db)
    try:
        stored = conn.execute(
            "SELECT concept_uuid, invalid_target FROM notes_cells WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert stored["concept_uuid"] == expected
        assert stored["invalid_target"] == 0
    finally:
        conn.close()


def test_manifest_upgrade_is_scoped_to_the_runs_exact_family(imported_db):
    import json
    from pathlib import Path

    from concept_model.filing_targets import persist_template_manifest

    db, _ids = imported_db
    conn = _conn(db)
    try:
        run_ids = {}
        for standard in ("mfrs", "mpers"):
            run_ids[standard] = conn.execute(
                "INSERT INTO runs(created_at, pdf_filename, status, run_config_json) "
                "VALUES ('t', ?, 'completed', ?)",
                (
                    f"{standard}.pdf",
                    json.dumps({
                        "filing_standard": standard,
                        "filing_level": "company",
                    }),
                ),
            ).lastrowid
            conn.execute(
                "INSERT INTO notes_cells(run_id, sheet, row, label, html, updated_at) "
                "VALUES (?, 'Notes-CI', 6, 'Financial reporting status', "
                "'<p>legacy</p>', 't')",
                (run_ids[standard],),
            )
        conn.commit()
    finally:
        conn.close()

    root = Path(__file__).resolve().parent.parent
    persist_template_manifest(
        db,
        root / "XBRL-template-MFRS/Company/10-Notes-CorporateInfo.xlsx",
    )

    conn = _conn(db)
    try:
        states = {
            row["run_id"]: row["invalid_target"]
            for row in conn.execute(
                "SELECT run_id, invalid_target FROM notes_cells ORDER BY run_id"
            )
        }
        assert states[run_ids["mfrs"]] == 1
        assert states[run_ids["mpers"]] == 0
    finally:
        conn.close()
