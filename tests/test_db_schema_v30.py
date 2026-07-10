"""Schema v30 — Evals workspace (docs/PLAN-evals-workspace.md).

Adds two tables (repeat_groups, gold_note_texts) and five nullable columns:
runs.app_version / repeat_group_id / repeat_index and
eval_scores.taxonomy_json / per_statement_json. All additive; a v29 DB walks
forward gaining them, and legacy rows read NULL.
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


def test_fresh_init_has_evals_tables_and_columns(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        tables = _tables(conn)
        assert "repeat_groups" in tables
        assert "gold_note_texts" in tables

        runs_cols = _columns(conn, "runs")
        assert {"app_version", "repeat_group_id", "repeat_index"} <= runs_cols

        score_cols = _columns(conn, "eval_scores")
        assert {"taxonomy_json", "per_statement_json"} <= score_cols

        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION >= 30
    finally:
        conn.close()


def test_v29_db_walks_forward(tmp_path):
    """A DB pinned at v29 gains the v30 columns/tables on the next init_db."""
    db = tmp_path / "v29.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        # Simulate a v29 DB: drop the new tables and the new columns off runs.
        conn.executescript(
            """
            PRAGMA foreign_keys=off;
            DROP TABLE IF EXISTS repeat_groups;
            DROP TABLE IF EXISTS gold_note_texts;
            CREATE TABLE runs_tmp AS SELECT id, created_at, pdf_filename, status,
                notes, session_id, output_dir, merged_workbook_path,
                run_config_json, scout_enabled, started_at, ended_at,
                orchestration, benchmark_id, notes_table_style FROM runs;
            DROP TABLE runs;
            ALTER TABLE runs_tmp RENAME TO runs;
            UPDATE schema_version SET version = 29;
            """
        )
        conn.commit()
        assert "app_version" not in _columns(conn, "runs")
        assert "repeat_groups" not in _tables(conn)
    finally:
        conn.close()

    init_db(db)  # migrate
    conn = sqlite3.connect(str(db))
    try:
        assert "app_version" in _columns(conn, "runs")
        assert "repeat_group_id" in _columns(conn, "runs")
        assert "repeat_groups" in _tables(conn)
        assert "gold_note_texts" in _tables(conn)
        assert "taxonomy_json" in _columns(conn, "eval_scores")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_init_db_is_idempotent_at_v30(tmp_path):
    db = tmp_path / "twice.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
        assert "repeat_groups" in _tables(conn)
    finally:
        conn.close()


def test_taxonomy_columns_default_null_on_legacy_score(tmp_path):
    """A scorecard inserted without the v30 columns reads them as NULL."""
    db = tmp_path / "rt.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA foreign_keys=off")
        conn.execute(
            "INSERT INTO eval_benchmarks(name, filing_standard, filing_level) "
            "VALUES ('b','mfrs','company')"
        )
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t','x.pdf','completed')"
        )
        conn.execute(
            "INSERT INTO eval_scores(run_id, benchmark_id, gold_cells, "
            "matched_cells, missing_cells, mismatch_cells, extra_cells, "
            "scale_mismatch) VALUES (1, 1, 10, 8, 1, 1, 0, 0)"
        )
        row = conn.execute(
            "SELECT taxonomy_json, per_statement_json FROM eval_scores"
        ).fetchone()
        assert row == (None, None)
    finally:
        conn.close()
