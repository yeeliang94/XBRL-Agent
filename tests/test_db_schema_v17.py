"""DB migration v16 -> v17: run_agents.error_type (item 9 failure taxonomy).

One nullable additive column. Same pinning shape as the v14-v16 column
steps: fresh init carries the column + version 17, a v16 fixture walks
forward cleanly, re-init is idempotent, and the write/read helpers
round-trip the value.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_current_schema_version_is_17():
    assert CURRENT_SCHEMA_VERSION == 17


def test_fresh_init_has_error_type_column(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "error_type" in _table_columns(conn, "run_agents")
        assert _schema_version(conn) == 17
    finally:
        conn.close()


def test_v16_db_walks_forward_to_v17(tmp_path):
    db = tmp_path / "v16.db"
    # Build a fresh DB, then simulate a committed v16 install by dropping
    # the new column and resetting the version marker (SQLite ≥3.35
    # supports DROP COLUMN; the venv interpreter ships 3.45+).
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("ALTER TABLE run_agents DROP COLUMN error_type")
        conn.execute("UPDATE schema_version SET version = 16")
        conn.commit()
        assert "error_type" not in _table_columns(conn, "run_agents")
    finally:
        conn.close()

    init_db(db)  # the walk-forward
    conn = sqlite3.connect(str(db))
    try:
        assert "error_type" in _table_columns(conn, "run_agents")
        assert _schema_version(conn) == 17
        # Legacy rows read NULL — nullable column, no backfill.
    finally:
        conn.close()


def test_reinit_is_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _schema_version(conn) == 17
    finally:
        conn.close()


def test_finish_run_agent_roundtrips_error_type(tmp_path):
    from db import repository as repo

    db = tmp_path / "rt.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        run_id = int(conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES ('2026Z', 'x.pdf', 'running', '2026Z')",
        ).lastrowid)
        rid = repo.create_run_agent(conn, run_id, "SOFP", variant="CuNonCu")
        repo.finish_run_agent(
            conn, rid, status="failed", error_type="wallclock",
        )
        conn.commit()

        agents = repo.fetch_run_agents(conn, run_id)
        assert agents[0].error_type == "wallclock"

        # Success rows stay NULL (legacy call sites pass nothing).
        rid2 = repo.create_run_agent(conn, run_id, "SOPL")
        repo.finish_run_agent(conn, rid2, status="succeeded")
        conn.commit()
        agents = repo.fetch_run_agents(conn, run_id)
        assert agents[1].error_type is None
    finally:
        conn.close()
