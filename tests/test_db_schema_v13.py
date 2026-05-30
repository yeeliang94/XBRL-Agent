"""DB migration v12 -> v13: durable manual re-review task state.

v13 adds the additive ``run_review_tasks`` table that replaces the
in-process ``_REVIEW_TASKS`` dict in server.py (rewrite Phase 5.3). A
manual re-review runs on a background thread for minutes; the old dict
lost both in-flight and finished passes on a process restart. The table
persists the latest pass per run (``run_id`` is the PRIMARY KEY, so a new
launch overwrites the slot) so a finished outcome survives a restart and a
poll can still fetch it.

Same pinning shape as ``test_db_schema_v12.py``: fresh init creates the
table, a v12 fixture upgrades cleanly, re-init is idempotent, the FK to
runs cascades on delete, and the repository helpers round-trip + reconcile
stale rows.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db.schema import CURRENT_SCHEMA_VERSION, init_db
from db import repository as repo


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        r[1]: r[2]
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def _seed_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status) "
        "VALUES ('2026-05-30T00:00:00Z', 'x.pdf', 'completed')"
    )
    conn.commit()
    return int(cur.lastrowid)


def test_current_schema_version_is_at_least_v13():
    assert CURRENT_SCHEMA_VERSION >= 13


def test_fresh_init_creates_review_tasks_table(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _table_exists(conn, "run_review_tasks")
        cols = _table_columns(conn, "run_review_tasks")
        for required in (
            "run_id", "status", "model_name", "outcome_json",
            "started_at", "updated_at",
        ):
            assert required in cols, f"missing column {required!r}"
        # run_id is the PRIMARY KEY (one latest pass per run).
        pk = [r for r in conn.execute(
            "PRAGMA table_info(run_review_tasks)"
        ).fetchall() if r[5]]  # r[5] == pk flag
        assert [r[1] for r in pk] == ["run_id"]
        assert _schema_version(conn) >= 13
    finally:
        conn.close()


def test_review_task_round_trips_running_then_done(tmp_path):
    db = tmp_path / "rt.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        run_id = _seed_run(conn)
        # No row yet == idle.
        assert repo.fetch_review_task(conn, run_id) is None

        repo.upsert_review_task(conn, run_id, "running", model_name="m1")
        conn.commit()
        running = repo.fetch_review_task(conn, run_id)
        assert running == {
            "status": "running", "model_name": "m1", "outcome": None
        }
        started_running = conn.execute(
            "SELECT started_at FROM run_review_tasks WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        assert started_running  # stamped on launch

        outcome = {"ok": True, "invoked": True, "writes_performed": 2,
                   "flags_raised": 0, "model": "m1"}
        repo.upsert_review_task(conn, run_id, "done", model_name="m1",
                                outcome=outcome)
        conn.commit()
        done = repo.fetch_review_task(conn, run_id)
        assert done["status"] == "done"
        assert done["outcome"] == outcome

        # started_at is preserved across running -> done.
        started_done = conn.execute(
            "SELECT started_at FROM run_review_tasks WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        assert started_done == started_running
    finally:
        conn.close()


def test_relaunch_overwrites_prior_pass_and_clears_outcome(tmp_path):
    db = tmp_path / "relaunch.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        run_id = _seed_run(conn)
        repo.upsert_review_task(conn, run_id, "done", model_name="m1",
                                outcome={"ok": True})
        conn.commit()
        # A fresh launch reuses the same run_id slot (PK), wiping the old
        # outcome — mirrors the old `_REVIEW_TASKS[run_id] = state`.
        repo.upsert_review_task(conn, run_id, "running", model_name="m2")
        conn.commit()
        again = repo.fetch_review_task(conn, run_id)
        assert again == {"status": "running", "model_name": "m2",
                         "outcome": None}
        assert conn.execute(
            "SELECT COUNT(*) FROM run_review_tasks WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_reconcile_stale_running_tasks_after_restart(tmp_path):
    """A row left 'running' by a crashed process is retired to a terminal
    'done' carrying an honest error, so a poll resolves and a relaunch is
    not blocked by the re-entrancy guard."""
    db = tmp_path / "stale.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        run_id = _seed_run(conn)
        repo.upsert_review_task(conn, run_id, "running", model_name="m1")
        conn.commit()

        n = repo.reconcile_stale_review_tasks(conn)
        conn.commit()
        assert n == 1

        reconciled = repo.fetch_review_task(conn, run_id)
        assert reconciled["status"] == "done"
        assert reconciled["outcome"]["ok"] is False
        assert reconciled["outcome"]["invoked"] is False
        assert "restart" in reconciled["outcome"]["error"].lower()

        # A second reconcile is a no-op (nothing left running).
        assert repo.reconcile_stale_review_tasks(conn) == 0

        # A finished pass is NOT touched by reconcile.
        repo.upsert_review_task(conn, run_id, "done", model_name="m1",
                                outcome={"ok": True, "invoked": True})
        conn.commit()
        assert repo.reconcile_stale_review_tasks(conn) == 0
        assert repo.fetch_review_task(conn, run_id)["outcome"]["ok"] is True
    finally:
        conn.close()


def test_review_task_cascades_on_run_delete(tmp_path):
    db = tmp_path / "cascade.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        run_id = _seed_run(conn)
        repo.upsert_review_task(conn, run_id, "running", model_name="m1")
        conn.commit()

        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM run_review_tasks WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_v12_fixture_upgrades_cleanly(tmp_path):
    """A v12 DB walks forward to v13: the table appears, the marker bumps to
    13, and existing data is undisturbed."""
    db = tmp_path / "v12.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE runs("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TEXT NOT NULL, pdf_filename TEXT NOT NULL, "
            "status TEXT NOT NULL, orchestration TEXT DEFAULT 'split')"
        )
        conn.execute(
            "CREATE TABLE schema_version(version INTEGER PRIMARY KEY)"
        )
        conn.execute("INSERT INTO schema_version(version) VALUES (12)")
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-05-01T00:00:00Z', 'legacy.pdf', 'completed')"
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        assert _table_exists(conn, "run_review_tasks")
        assert _schema_version(conn) >= 13
        row = conn.execute(
            "SELECT pdf_filename FROM runs WHERE pdf_filename = 'legacy.pdf'"
        ).fetchone()
        assert row[0] == "legacy.pdf"
    finally:
        conn.close()


def test_re_init_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _table_exists(conn, "run_review_tasks")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()
