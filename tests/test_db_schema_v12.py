"""DB migration v11 → v12: reviewer-agent backing tables.

v12 adds two additive tables for the reviewer pass that replaces the
autonomous canonical correction pass (docs/Archive/PLAN-reviewer-agent.md):

* ``run_fact_snapshots`` — the ORIGINAL extraction facts, snapshotted
  once before the reviewer writes anything. "Revert to original"
  restores from here; this reversibility is what lets the reviewer
  write freely.
* ``reviewer_flags`` — the narrow user-facing list of `stuck` /
  `disputes_prior` cases the reviewer surfaces.

Same pinning shape as ``test_db_schema_v11.py``: fresh init creates both
tables, a v11 fixture upgrades cleanly, re-init is idempotent, and both
FKs to runs cascade on delete.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        r[1]: r[2]
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_current_schema_version_is_at_least_v12():
    assert CURRENT_SCHEMA_VERSION >= 12


def test_fresh_init_creates_reviewer_tables(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _table_exists(conn, "run_fact_snapshots")
        assert _table_exists(conn, "reviewer_flags")

        snap_cols = _table_columns(conn, "run_fact_snapshots")
        for required in (
            "id", "run_id", "concept_uuid", "period", "entity_scope",
            "value", "value_status", "children_status", "source",
            "evidence", "snapshot_at",
        ):
            assert required in snap_cols, f"missing snapshot column {required!r}"

        flag_cols = _table_columns(conn, "reviewer_flags")
        for required in (
            "id", "run_id", "concept_uuid", "target_sheet", "target_row",
            "category", "reasoning", "pdf_page", "applied_fix", "status",
            "human_answer", "created_at", "updated_at",
        ):
            assert required in flag_cols, f"missing flag column {required!r}"

        assert _schema_version(conn) >= 12
    finally:
        conn.close()


def _seed_run(conn: sqlite3.Connection) -> int:
    """Insert a minimal run row and return its id."""
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status) "
        "VALUES ('2026-05-29T00:00:00Z', 'x.pdf', 'completed')"
    )
    conn.commit()
    return int(cur.lastrowid)


def test_snapshot_round_trips_and_uniques(tmp_path):
    db = tmp_path / "rt.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        run_id = _seed_run(conn)
        conn.execute(
            "INSERT INTO run_fact_snapshots(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, snapshot_at) "
            "VALUES (?, 'c1', 'CY', 'Company', 100.0, 'observed', "
            "'2026-05-29T00:00:01Z')",
            (run_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT value, value_status FROM run_fact_snapshots "
            "WHERE run_id = ? AND concept_uuid = 'c1'",
            (run_id,),
        ).fetchone()
        assert row == (100.0, "observed")

        # UNIQUE(run_id, concept_uuid, period, entity_scope).
        import pytest
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO run_fact_snapshots(run_id, concept_uuid, period, "
                "entity_scope, value, value_status) "
                "VALUES (?, 'c1', 'CY', 'Company', 200.0, 'observed')",
                (run_id,),
            )
    finally:
        conn.close()


def test_reviewer_tables_cascade_on_run_delete(tmp_path):
    db = tmp_path / "cascade.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        run_id = _seed_run(conn)
        conn.execute(
            "INSERT INTO run_fact_snapshots(run_id, concept_uuid, period, "
            "entity_scope, value_status) VALUES (?, 'c1', 'CY', 'Company', "
            "'observed')",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO reviewer_flags(run_id, category, reasoning) "
            "VALUES (?, 'stuck', 'cannot reconcile')",
            (run_id,),
        )
        conn.commit()

        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()

        assert conn.execute(
            "SELECT COUNT(*) FROM run_fact_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM reviewer_flags WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_v11_fixture_upgrades_cleanly(tmp_path):
    """A v11 DB walks forward to v12: the two tables appear, the marker
    bumps to 12, and existing data is undisturbed."""
    db = tmp_path / "v11.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE runs("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TEXT NOT NULL, pdf_filename TEXT NOT NULL, "
            "status TEXT NOT NULL, orchestration TEXT DEFAULT 'split')"
        )
        conn.execute(
            "CREATE TABLE schema_version(version INTEGER PRIMARY KEY)"
        )
        conn.execute("INSERT INTO schema_version(version) VALUES (11)")
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-05-01T00:00:00Z', 'legacy.pdf', 'completed')"
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        assert _table_exists(conn, "run_fact_snapshots")
        assert _table_exists(conn, "reviewer_flags")
        assert _schema_version(conn) >= 12
        row = conn.execute(
            "SELECT pdf_filename FROM runs WHERE pdf_filename = 'legacy.pdf'"
        ).fetchone()
        assert row[0] == "legacy.pdf"
    finally:
        conn.close()


def test_re_init_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _table_exists(conn, "run_fact_snapshots")
        assert _table_exists(conn, "reviewer_flags")
        for idx_name in (
            "ix_run_fact_snapshots_run_id",
            "ix_reviewer_flags_run_id",
        ):
            idx = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name = ?",
                (idx_name,),
            ).fetchone()
            assert idx is not None, f"missing index {idx_name}"
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()
