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


def test_bootstrap_imports_targets_and_is_idempotent(fresh_db: Path):
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
    # Group linear templates get B/C/D/E per-scope concept_targets.
    assert _count(fresh_db, "concept_targets") > 0

    # Re-import on the same database to pin the startup idempotence contract
    # without paying for two additional all-template bootstraps in separate
    # pytest-xdist workers.
    nodes_first = _count(fresh_db, "concept_nodes")
    templates_first = _count(fresh_db, "concept_templates")

    # Forget the recorded fingerprints so the second pass really re-imports.
    conn = sqlite3.connect(str(fresh_db))
    conn.execute("DELETE FROM template_imports")
    conn.commit()
    conn.close()
    assert import_all_face_templates(fresh_db) == template_ids
    assert _count(fresh_db, "concept_nodes") == nodes_first
    assert _count(fresh_db, "concept_templates") == templates_first


def test_startup_rewrites_only_templates_whose_rows_would_change(fresh_db: Path, monkeypatch):
    """On Azure SQL a full import is tens of thousands of round trips per start."""
    from concept_model import bootstrap

    written: list[str] = []
    real_import = bootstrap.import_template
    monkeypatch.setattr(bootstrap, "import_template",
                        lambda db, json_path: written.append(json_path) or real_import(db, json_path))

    first = bootstrap.import_all_face_templates(fresh_db)
    assert len(written) == len(first)

    written.clear()
    assert bootstrap.import_all_face_templates(fresh_db) == first
    assert written == []

    # The parser now reads one template differently while its file is
    # unchanged (a header-row rule, the taxonomy): that template is rewritten.
    real_parse = bootstrap.parse_template
    target = sorted(first)[0]

    def parse(path):
        tree = real_parse(path)
        if tree.template_id == target:
            tree.concepts[0].canonical_label += " (changed)"
        return tree

    monkeypatch.setattr(bootstrap, "parse_template", parse)
    assert bootstrap.import_all_face_templates(fresh_db) == first
    assert len(written) == 1
    monkeypatch.setattr(bootstrap, "parse_template", real_parse)

    # A template whose rows are gone is rewritten even though its record remains.
    written.clear()
    conn = sqlite3.connect(str(fresh_db))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM concept_templates WHERE template_id = ?", (sorted(first)[1],))
    conn.commit()
    conn.close()
    bootstrap.import_all_face_templates(fresh_db)
    assert len(written) == 2  # the deleted one, and the target back to its real labels


def test_changed_filing_semantics_refresh_the_stored_manifest(fresh_db: Path, monkeypatch):
    """A taxonomy or slot rule change with the same workbook bytes must reach template_slots."""
    import dataclasses

    from concept_model import bootstrap, filing_targets

    first = bootstrap.import_all_face_templates(fresh_db)
    target = sorted(first)[0]
    real = filing_targets.targets_for_template

    def changed(path):
        template_id, targets = real(path)
        if template_id == target:
            targets = [dataclasses.replace(t, label=t.label + " (new rule)") for t in targets]
        return template_id, targets

    monkeypatch.setattr(filing_targets, "targets_for_template", changed)
    monkeypatch.setattr(bootstrap, "targets_for_template", changed)
    bootstrap.import_all_face_templates(fresh_db)

    conn = sqlite3.connect(str(fresh_db))
    try:
        labels = [r[0] for r in conn.execute(
            "SELECT label FROM template_slots WHERE template_id = ?", (target,))]
    finally:
        conn.close()
    assert labels and all(label.endswith(" (new rule)") for label in labels)

    written: list[str] = []
    monkeypatch.setattr(bootstrap, "import_template", lambda db, json_path: written.append(json_path))
    bootstrap.import_all_face_templates(fresh_db)
    assert written == []
