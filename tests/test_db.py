"""Tests for the SQLite audit store (Phase 2)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}
    finally:
        conn.close()


def test_schema_created(tmp_path: Path) -> None:
    """init_db creates all 5 data tables + schema_version."""
    db = tmp_path / "xbrl_agent.db"
    init_db(db)

    assert _tables(db) >= {
        "runs",
        "run_agents",
        "agent_events",
        "extracted_fields",
        "cross_checks",
        "schema_version",
    }


def test_expected_columns(tmp_path: Path) -> None:
    db = tmp_path / "xbrl_agent.db"
    init_db(db)

    assert {"id", "created_at", "pdf_filename", "status", "notes"} <= _columns(db, "runs")
    assert {
        "id", "run_id", "statement_type", "variant", "model", "status",
        "started_at", "ended_at", "workbook_path", "total_tokens", "total_cost",
    } <= _columns(db, "run_agents")
    assert {
        "id", "run_agent_id", "ts", "event_type", "phase", "payload_json",
    } <= _columns(db, "agent_events")
    assert {
        "id", "run_agent_id", "sheet", "field_label", "section", "col",
        "row_num", "value", "evidence",
    } <= _columns(db, "extracted_fields")
    assert {
        "id", "run_id", "check_name", "status", "expected", "actual",
        "diff", "tolerance", "message",
    } <= _columns(db, "cross_checks")


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    """Re-running init_db does not error and does not duplicate version rows."""
    db = tmp_path / "xbrl_agent.db"
    init_db(db)
    init_db(db)
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    finally:
        conn.close()
    assert count == 1


def test_schema_version_recorded(tmp_path: Path) -> None:
    db = tmp_path / "xbrl_agent.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
    finally:
        conn.close()
    assert version == CURRENT_SCHEMA_VERSION


def test_foreign_key_cascade(tmp_path: Path) -> None:
    """Deleting a run sweeps dependent rows when FKs are enforced."""
    db = tmp_path / "xbrl_agent.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) VALUES (?, ?, ?)",
            ("2026-04-06T00:00:00Z", "x.pdf", "running"),
        )
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            (run_id, "SOFP", "running", "2026-04-06T00:00:00Z"),
        )
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        (n,) = conn.execute("SELECT COUNT(*) FROM run_agents").fetchone()
    finally:
        conn.close()
    assert n == 0


def test_init_db_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "deeply" / "nested" / "xbrl.db"
    init_db(nested)
    assert nested.exists()
