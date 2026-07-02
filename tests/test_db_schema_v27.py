"""Schema v27 — notes_format_snapshots + notes_format_tasks taxonomy/token
columns (docs/PLAN-notes-formatter-hardening.md Phase 2)."""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db

_V27_TASK_COLUMNS = {
    "error_type", "prompt_tokens", "completion_tokens",
    "cache_read_tokens", "cache_write_tokens",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_fresh_init_has_v27_table_and_columns(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_format_snapshots" in _tables(conn)
        assert _V27_TASK_COLUMNS <= _columns(conn, "notes_format_tasks")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION >= 27
    finally:
        conn.close()


def test_v26_db_walks_forward_with_alters(tmp_path):
    """A DB that walked to v26 has notes_format_tasks WITHOUT the v27 columns
    — the v27 block must ALTER them in and create the snapshots table."""
    db = tmp_path / "v26.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE IF EXISTS notes_format_snapshots")
        # Recreate the task table in its exact v26 shape (no v27 columns).
        conn.execute("DROP TABLE IF EXISTS notes_format_tasks")
        conn.execute(
            """
            CREATE TABLE notes_format_tasks (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id            INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                sheet             TEXT NOT NULL,
                status            TEXT NOT NULL,
                model             TEXT,
                summary           TEXT,
                confidence        REAL,
                changed_rows      INTEGER NOT NULL DEFAULT 0,
                result_json       TEXT,
                error             TEXT,
                before_text_hash  TEXT,
                after_text_hash   TEXT,
                created_at        TEXT NOT NULL DEFAULT '',
                updated_at        TEXT NOT NULL DEFAULT '',
                UNIQUE(run_id, sheet)
            )
            """
        )
        conn.execute("UPDATE schema_version SET version = 26")
        conn.commit()
        assert not (_V27_TASK_COLUMNS & _columns(conn, "notes_format_tasks"))
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_format_snapshots" in _tables(conn)
        assert _V27_TASK_COLUMNS <= _columns(conn, "notes_format_tasks")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_v25_db_walks_forward_through_both_steps(tmp_path):
    """A v25 DB gets the full v26 CREATE (columns inline) and both markers."""
    db = tmp_path / "v25.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE IF EXISTS notes_format_tasks")
        conn.execute("DROP TABLE IF EXISTS notes_format_snapshots")
        conn.execute("UPDATE schema_version SET version = 25")
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_format_tasks" in _tables(conn)
        assert "notes_format_snapshots" in _tables(conn)
        assert _V27_TASK_COLUMNS <= _columns(conn, "notes_format_tasks")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_init_db_is_idempotent_at_v27(tmp_path):
    db = tmp_path / "twice.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
        assert "notes_format_snapshots" in _tables(conn)
    finally:
        conn.close()


def test_snapshot_rows_cascade_on_run_delete(tmp_path):
    from db import repository as repo

    db = tmp_path / "cascade.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")
        conn.execute(
            "INSERT INTO notes_format_snapshots(run_id, sheet, row, html, created_at) "
            "VALUES (?, 'Notes-Listofnotes', 112, '<p>a</p>', '')",
            (run_id,),
        )
    with repo.db_session(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        left = conn.execute(
            "SELECT COUNT(*) FROM notes_format_snapshots"
        ).fetchone()[0]
    assert left == 0
