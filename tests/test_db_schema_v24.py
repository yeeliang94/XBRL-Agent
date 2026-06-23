"""DB migration v23 -> v24: notes_review_flags + notes_review_tasks.

Two new tables (pure CREATE TABLE IF NOT EXISTS walk-forward). Pinning mirrors
v23: fresh init carries the tables + version 24, a v23 fixture walks forward,
re-init is idempotent.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db

_NEW_TABLES = ("notes_review_flags", "notes_review_tasks")


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_current_schema_version_is_at_least_v24():
    assert CURRENT_SCHEMA_VERSION >= 24


def test_fresh_init_has_notes_reviewer_v24_tables(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        tables = _tables(conn)
        for t in _NEW_TABLES:
            assert t in tables, f"missing {t}"
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_v23_db_walks_forward(tmp_path):
    db = tmp_path / "v23.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        for t in _NEW_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.execute("UPDATE schema_version SET version = 23")
        conn.commit()
        assert not (_tables(conn) & set(_NEW_TABLES))
    finally:
        conn.close()

    init_db(db)  # walk-forward
    conn = sqlite3.connect(str(db))
    try:
        tables = _tables(conn)
        for t in _NEW_TABLES:
            assert t in tables
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_notes_review_task_round_trips(tmp_path):
    from db import repository as repo

    db = tmp_path / "task.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")
        repo.upsert_notes_review_task(conn, run_id, "running", model="m")
    with repo.db_session(db) as conn:
        t = repo.fetch_notes_review_task(conn, run_id)
        assert t["status"] == "running" and t["model"] == "m"
        repo.upsert_notes_review_task(conn, run_id, "done",
                                      model="m", outcome={"writes_performed": 2})
    with repo.db_session(db) as conn:
        t = repo.fetch_notes_review_task(conn, run_id)
        assert t["status"] == "done" and t["outcome"]["writes_performed"] == 2


def test_reconcile_stale_notes_review_tasks(tmp_path):
    from db import repository as repo

    db = tmp_path / "stale.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")
        repo.upsert_notes_review_task(conn, run_id, "running", model="m")
    with repo.db_session(db) as conn:
        n = repo.reconcile_stale_notes_review_tasks(conn)
        assert n == 1
        t = repo.fetch_notes_review_task(conn, run_id)
        assert t["status"] == "done" and t["outcome"]["ok"] is False


def test_reinit_is_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()
