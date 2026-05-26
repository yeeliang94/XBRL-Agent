"""Phase A2 — concept-tree bootstrap import.

`concept_nodes` is global/per-template (no run_id), so before any live run can
light up the Concepts UI the face templates must be imported once into the live
DB. `concept_model.bootstrap.import_all_face_templates` walks every face
template (SOFP/SOPL/SOCI/SOCF/SOCIE/SoRE × MFRS+MPERS × Company/Group), is
idempotent, and populates Group `concept_targets` for linear templates.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    from db.schema import init_db

    db_path = tmp_path / "xbrl.db"
    init_db(db_path)
    return db_path


def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_bootstrap_imports_face_templates(fresh_db: Path):
    from concept_model.bootstrap import import_all_face_templates

    template_ids = import_all_face_templates(fresh_db)

    # Both standards × both levels × multiple variants → many templates.
    assert len(template_ids) >= 20
    assert _count(fresh_db, "concept_templates") == len(template_ids)
    assert _count(fresh_db, "concept_nodes") > 0

    # A known MFRS Company SOFP face concept exists.
    conn = sqlite3.connect(str(fresh_db))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM concept_nodes "
            "WHERE render_sheet = 'SOFP-CuNonCu'"
        ).fetchone()[0]
        assert n > 0
    finally:
        conn.close()


def test_bootstrap_is_idempotent(fresh_db: Path):
    from concept_model.bootstrap import import_all_face_templates

    import_all_face_templates(fresh_db)
    nodes_first = _count(fresh_db, "concept_nodes")
    templates_first = _count(fresh_db, "concept_templates")

    import_all_face_templates(fresh_db)
    assert _count(fresh_db, "concept_nodes") == nodes_first
    assert _count(fresh_db, "concept_templates") == templates_first


def test_bootstrap_populates_group_targets(fresh_db: Path):
    from concept_model.bootstrap import import_all_face_templates

    import_all_face_templates(fresh_db)
    # Group linear templates get B/C/D/E per-scope concept_targets.
    assert _count(fresh_db, "concept_targets") > 0
