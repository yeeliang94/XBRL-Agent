from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_fresh_init_has_notes_formatter_v26_table(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_format_tasks" in _tables(conn)
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_v25_db_walks_forward(tmp_path):
    db = tmp_path / "v25.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE IF EXISTS notes_format_tasks")
        conn.execute("UPDATE schema_version SET version = 25")
        conn.commit()
        assert "notes_format_tasks" not in _tables(conn)
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_format_tasks" in _tables(conn)
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_notes_format_task_round_trips_and_reconciles(tmp_path):
    from db import repository as repo

    db = tmp_path / "task.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")
        assert repo.claim_notes_format_task(conn, run_id, "Notes-Listofnotes", model="m")
        assert not repo.claim_notes_format_task(conn, run_id, "Notes-Listofnotes", model="m")

    with repo.db_session(db) as conn:
        t = repo.fetch_notes_format_task(conn, run_id, "Notes-Listofnotes")
        assert t["status"] == "running"
        repo.upsert_notes_format_task(
            conn, run_id, "Notes-Listofnotes", "done", model="m",
            summary="ok", confidence=0.8, changed_rows=2,
            result={"ok": True}, before_text_hash="a", after_text_hash="a",
        )

    with repo.db_session(db) as conn:
        t = repo.fetch_notes_format_task(conn, run_id, "Notes-Listofnotes")
        assert t["status"] == "done"
        assert t["summary"] == "ok"
        assert t["confidence"] == 0.8
        assert t["changed_rows"] == 2

    with repo.db_session(db) as conn:
        assert repo.claim_notes_format_task(conn, run_id, "Notes-Listofnotes", model="m2")
        assert repo.reconcile_stale_notes_format_tasks(conn) == 1
        t = repo.fetch_notes_format_task(conn, run_id, "Notes-Listofnotes")
        assert t["status"] == "done"
        assert t["error"] == "restarted"
