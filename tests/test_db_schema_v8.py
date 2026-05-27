"""Tests for schema v8 — per-turn telemetry metrics.

Step 4 of docs/PLAN-run-page-and-telemetry.md. Adds the `run_agent_turns`
metrics table (one row per agent iteration) plus four nullable rollup columns
on `run_agents` (prompt_tokens, completion_tokens, turn_count,
tool_call_count). Metrics only — full per-iteration request/response content
stays in the on-disk conversation trace (hybrid storage).

The v8 migration is additive only — no existing column or table is dropped —
so rollback is a code revert and the orphaned table/columns are harmless.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(db_path: Path, table: str) -> dict[str, dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {
            r[1]: {"type": r[2], "notnull": bool(r[3]), "default": r[4]}
            for r in rows
        }
    finally:
        conn.close()


def _make_v7_db(db: Path) -> None:
    """Hand-create a v7-shaped DB: a runs table, a run_agents table WITHOUT
    the v8 rollup columns, and schema_version pinned at 7 so init_db has to
    walk v7→v8 forward. We only need the tables the v8 block touches; init_db's
    CREATE TABLE IF NOT EXISTS fills in everything else."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                pdf_filename TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                session_id TEXT NOT NULL DEFAULT '',
                output_dir TEXT NOT NULL DEFAULT '',
                merged_workbook_path TEXT,
                run_config_json TEXT,
                scout_enabled INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL DEFAULT '',
                ended_at TEXT
            )"""
        )
        # v7 run_agents shape — total_tokens/total_cost exist, but NOT the
        # four v8 rollup columns.
        conn.execute(
            """CREATE TABLE run_agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                statement_type TEXT NOT NULL,
                variant TEXT,
                model TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                workbook_path TEXT,
                total_tokens INTEGER DEFAULT 0,
                total_cost REAL DEFAULT 0
            )"""
        )
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_version(version) VALUES (7)")
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-01T00:00:00Z", "legacy.pdf", "completed",
             "2026-05-01T00:00:00Z"),
        )
        run_id = cur.lastrowid
        conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, status, "
            "started_at, total_tokens) VALUES (?, ?, ?, ?, ?)",
            (run_id, "SOFP", "succeeded", "2026-05-01T00:00:00Z", 12345),
        )
        conn.commit()
    finally:
        conn.close()


def test_v7_to_v8_adds_turns_table_and_rollup_columns(tmp_path: Path) -> None:
    """A v7 DB walks forward: run_agent_turns appears, run_agents gains the
    four rollup columns, and existing data survives untouched."""
    db = tmp_path / "xbrl.db"
    _make_v7_db(db)

    init_db(db)

    # New metrics table exists with the expected columns.
    turn_cols = _columns(db, "run_agent_turns")
    for name in ("id", "run_agent_id", "turn_index", "node_kind", "tool_names",
                 "prompt_tokens", "completion_tokens", "total_tokens",
                 "cumulative_tokens", "cost_estimate", "duration_ms", "ts"):
        assert name in turn_cols, f"run_agent_turns.{name} missing after migration"

    # run_agents gained the v8 rollup columns.
    agent_cols = _columns(db, "run_agents")
    for name in ("prompt_tokens", "completion_tokens", "turn_count",
                 "tool_call_count"):
        assert name in agent_cols, f"run_agents.{name} missing after migration"

    # Legacy run_agents row survives untouched; new columns default to 0.
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT total_tokens, prompt_tokens, completion_tokens, "
            "turn_count, tool_call_count FROM run_agents "
            "WHERE statement_type = 'SOFP'"
        ).fetchone()
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
    finally:
        conn.close()
    assert row == (12345, 0, 0, 0, 0)
    assert version == CURRENT_SCHEMA_VERSION
    assert version >= 8


def test_v8_fresh_init_has_turns_table(tmp_path: Path) -> None:
    """A fresh DB lands directly on v8 with the metrics table present."""
    db = tmp_path / "xbrl.db"
    init_db(db)

    assert "turn_index" in _columns(db, "run_agent_turns")
    assert "turn_count" in _columns(db, "run_agents")

    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
    finally:
        conn.close()
    assert version == CURRENT_SCHEMA_VERSION


def test_v8_init_is_idempotent(tmp_path: Path) -> None:
    """Repeat init_db leaves version + row count unchanged."""
    db = tmp_path / "xbrl.db"
    init_db(db)
    init_db(db)
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        (count,) = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    finally:
        conn.close()
    assert version == CURRENT_SCHEMA_VERSION
    assert count == 1


def test_run_agent_turns_cascades_on_agent_delete(tmp_path: Path) -> None:
    """FK ON DELETE CASCADE — deleting a run sweeps run_agents and their
    turn rows."""
    db = tmp_path / "xbrl.db"
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-01T00:00:00Z", "x.pdf", "running",
             "2026-05-01T00:00:00Z"),
        )
        run_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            (run_id, "SOFP", "running", "2026-05-01T00:00:00Z"),
        )
        agent_id = cur.lastrowid
        conn.execute(
            "INSERT INTO run_agent_turns(run_agent_id, turn_index, node_kind, ts) "
            "VALUES (?, ?, ?, ?)",
            (agent_id, 1, "model_request", "2026-05-01T00:00:01Z"),
        )
        conn.commit()

        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        (turns_left,) = conn.execute(
            "SELECT COUNT(*) FROM run_agent_turns"
        ).fetchone()
        assert turns_left == 0
    finally:
        conn.close()
