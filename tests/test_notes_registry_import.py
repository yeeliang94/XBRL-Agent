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
