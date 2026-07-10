"""Schema v31 — Evals workspace Phase 2 (suites + batch runner).

Adds three tables (eval_suites, eval_suite_docs, eval_suite_runs) and one
nullable column (runs.suite_run_id). All additive; a v30 DB walks forward
gaining them, and legacy runs read suite_run_id = NULL.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_fresh_init_has_suite_tables_and_column(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        tables = _tables(conn)
        assert {"eval_suites", "eval_suite_docs", "eval_suite_runs"} <= tables
        assert "suite_run_id" in _columns(conn, "runs")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION >= 31
    finally:
        conn.close()


def test_v30_db_walks_forward(tmp_path):
    """A DB pinned at v30 gains the v31 tables + suite_run_id on next init_db."""
    db = tmp_path / "v30.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            PRAGMA foreign_keys=off;
            DROP TABLE IF EXISTS eval_suites;
            DROP TABLE IF EXISTS eval_suite_docs;
            DROP TABLE IF EXISTS eval_suite_runs;
            CREATE TABLE runs_tmp AS SELECT id, created_at, pdf_filename, status,
                notes, session_id, output_dir, merged_workbook_path,
                run_config_json, scout_enabled, started_at, ended_at,
                orchestration, benchmark_id, notes_table_style, app_version,
                repeat_group_id, repeat_index FROM runs;
            DROP TABLE runs;
            ALTER TABLE runs_tmp RENAME TO runs;
            UPDATE schema_version SET version = 30;
            """
        )
        conn.commit()
        assert "suite_run_id" not in _columns(conn, "runs")
        assert "eval_suites" not in _tables(conn)
    finally:
        conn.close()

    init_db(db)  # migrate
    conn = sqlite3.connect(str(db))
    try:
        assert "suite_run_id" in _columns(conn, "runs")
        assert {"eval_suites", "eval_suite_docs", "eval_suite_runs"} <= _tables(conn)
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_init_db_is_idempotent_at_v31(tmp_path):
    db = tmp_path / "twice.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
        assert "eval_suites" in _tables(conn)
    finally:
        conn.close()


def test_suite_run_id_defaults_null_on_legacy_run(tmp_path):
    db = tmp_path / "rt.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t','x.pdf','completed')"
        )
        row = conn.execute("SELECT suite_run_id FROM runs").fetchone()
        assert row == (None,)
    finally:
        conn.close()
