"""Schema v42: durable run-level incident capture."""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_schema_has_run_incidents(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        assert CURRENT_SCHEMA_VERSION >= 42
        assert (
            conn.execute("SELECT version FROM schema_version").fetchone()[0]
            == CURRENT_SCHEMA_VERSION
        )
        assert {
            "id", "run_id", "created_at", "source", "stage", "severity",
            "error_code", "user_message", "technical_message",
            "exception_type", "correlation_id", "details_json",
        } <= _columns(conn, "run_incidents")
        assert {
            "id", "run_id", "ts", "event_type", "phase", "payload_json",
        } <= _columns(conn, "run_events")
    finally:
        conn.close()


def test_v41_upgrade_is_idempotent_and_preserves_runs(tmp_path):
    db = tmp_path / "v41.db"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'legacy.pdf', 'failed')"
        ).lastrowid
        conn.execute("UPDATE schema_version SET version = 41")
        conn.execute("DROP TABLE run_incidents")
        conn.execute("DROP TABLE run_events")
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        assert (
            conn.execute("SELECT version FROM schema_version").fetchone()[0]
            == CURRENT_SCHEMA_VERSION
        )
        assert conn.execute(
            "SELECT pdf_filename FROM runs WHERE id = ?", (run_id,)
        ).fetchone()[0] == "legacy.pdf"
        assert conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'run_incidents'"
        ).fetchone()
        assert conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'run_events'"
        ).fetchone()
    finally:
        conn.close()
