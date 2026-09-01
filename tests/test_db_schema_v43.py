"""Schema v43: durable per-agent terminal error detail."""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_schema_has_agent_error_message(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        assert CURRENT_SCHEMA_VERSION >= 43
        assert (
            conn.execute("SELECT version FROM schema_version").fetchone()[0]
            == CURRENT_SCHEMA_VERSION
        )
        assert "error_message" in _columns(conn, "run_agents")
    finally:
        conn.close()


def test_v42_upgrade_is_idempotent_and_preserves_agent_rows(tmp_path):
    db = tmp_path / "v42.db"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'legacy.pdf', 'failed')"
        ).lastrowid
        agent_id = conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, status, started_at, "
            "error_type) VALUES (?, 'SOPL', 'failed', 't', 'save_gate_refused')",
            (run_id,),
        ).lastrowid
        conn.execute("UPDATE schema_version SET version = 42")
        try:
            conn.execute("ALTER TABLE run_agents DROP COLUMN error_message")
        except sqlite3.OperationalError as exc:  # pragma: no cover
            pytest.skip(f"SQLite too old for DROP COLUMN: {exc}")
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
        row = conn.execute(
            "SELECT statement_type, error_type, error_message "
            "FROM run_agents WHERE id = ?",
            (agent_id,),
        ).fetchone()
        assert row == ("SOPL", "save_gate_refused", None)
    finally:
        conn.close()


def test_finish_run_agent_roundtrips_error_message(tmp_path):
    from db import repository as repo

    db = tmp_path / "roundtrip.db"
    init_db(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        run_id = int(conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES ('t', 'x.pdf', 'running', 't')"
        ).lastrowid)
        agent_id = repo.create_run_agent(conn, run_id, "SOPL")
        refusal = (
            "SOPL: workbook written but save_result never succeeded. "
            "Last refusal: unresolved write error."
        )
        repo.finish_run_agent(
            conn,
            agent_id,
            status="failed",
            error_type="save_gate_refused",
            error_message=refusal,
        )
        conn.commit()

        agent = repo.fetch_run_agents(conn, run_id)[0]
        assert agent.error_type == "save_gate_refused"
        assert agent.error_message == refusal
    finally:
        conn.close()
