"""Phase 1 schema v3 → v4 tests for the canonical concept model.

v4 adds seven additive tables: concept_templates, concept_nodes,
concept_edges, concept_targets, run_concept_facts, concept_fact_events,
run_concept_conflicts. Migration is idempotent (gotcha #11) and lives
behind the same `current_version < N` guard used for v2/v3.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db.schema import CURRENT_SCHEMA_VERSION, init_db


_NEW_TABLES = (
    "concept_templates",
    "concept_nodes",
    "concept_edges",
    "concept_targets",
    "run_concept_facts",
    "concept_fact_events",
    "run_concept_conflicts",
)


def _tables(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()


def _seed_v3(db: Path) -> None:
    """Create a v3-shaped DB by running init_db once then forcing the
    schema_version back to 3 (the simplest way to simulate "user has v3,
    we just shipped v4")."""
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE schema_version SET version = 3")
        # Drop the v4 tables so the migration has work to do.
        for t in _NEW_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
    finally:
        conn.close()


def test_v3_database_migrates_to_v4_on_init(tmp_path: Path) -> None:
    db = tmp_path / "xbrl.db"
    _seed_v3(db)

    init_db(db)

    assert CURRENT_SCHEMA_VERSION >= 4
    tables = _tables(db)
    for t in _NEW_TABLES:
        assert t in tables, f"{t} missing after v3→v4 migration"

    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
    finally:
        conn.close()
    assert version == CURRENT_SCHEMA_VERSION


def test_migration_runs_idempotently(tmp_path: Path) -> None:
    db = tmp_path / "xbrl.db"
    _seed_v3(db)

    # Three back-to-back inits must converge — no duplicate columns,
    # no leftover schema_version rows.
    init_db(db)
    init_db(db)
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM schema_version"
        ).fetchone()
    finally:
        conn.close()
    assert version == CURRENT_SCHEMA_VERSION
    assert count == 1


def test_fresh_init_creates_v4_tables(tmp_path: Path) -> None:
    """Empty DB → init → every v4 table present without any migration
    pass having to run."""
    db = tmp_path / "xbrl.db"
    init_db(db)

    tables = _tables(db)
    for t in _NEW_TABLES:
        assert t in tables


def test_concept_nodes_schema_fields(tmp_path: Path) -> None:
    """The shape of concept_nodes is load-bearing for the importer + the
    facts API: pin the column set so a future drift is caught loudly."""
    db = tmp_path / "xbrl.db"
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(concept_nodes)"
        ).fetchall()}
    finally:
        conn.close()

    for name in (
        "concept_uuid",
        "template_id",
        "parent_uuid",
        "kind",
        "canonical_label",
        "render_sheet",
        "render_row",
        "render_col",
    ):
        assert name in cols, f"concept_nodes.{name} missing"


def test_run_concept_facts_composite_key(tmp_path: Path) -> None:
    """run_concept_facts uses (run_id, concept_uuid, period, entity_scope)
    as the upsert composite key — pinned via a UNIQUE index."""
    db = tmp_path / "xbrl.db"
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        # Create the parent run + template + node so FKs are satisfied.
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-21T00:00:00Z", "x.pdf", "running",
             "2026-05-21T00:00:00Z"),
        )
        run_id = cur.lastrowid
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path) "
            "VALUES (?, ?)",
            ("t-1", "fake.xlsx"),
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("c-1", "t-1", "LEAF", "L", "S", 10, "B"),
        )

        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, children_status, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, "c-1", "CY", "Company", 100.0, "observed", None, "pdf"),
        )
        conn.commit()

        # Same (run_id, concept_uuid, period, entity_scope) → integrity
        # error from the unique index.
        raised = False
        try:
            conn.execute(
                "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
                "entity_scope, value, value_status, children_status, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, "c-1", "CY", "Company", 200.0, "observed",
                 None, "pdf"),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "UNIQUE(run_id, concept_uuid, period, entity_scope) " \
                       "not enforced"
    finally:
        conn.close()
