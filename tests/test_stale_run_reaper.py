"""Startup stale-run reaper (UX-QA fix, backlog #2).

A run row left `status='running'` by a dead process can never reach a terminal
state on its own — the streaming session that owned it is gone, so the UI shows
Download/Delete disabled forever and History carries a phantom "Running" row.
`reconcile_stale_runs` mirrors `reconcile_stale_review_tasks`: at startup, flip
every sufficiently-old `running` run to `aborted` so the lifecycle contract
(gotcha #10 — no row stuck non-terminal) holds across restarts.

A *fresh* running run (a genuinely in-flight extraction that outlived a very
recent restart, or a run started seconds ago) must NOT be reaped.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from db.schema import init_db
from db import repository as repo


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "audit.db"
    init_db(db_path)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def _make_run(conn: sqlite3.Connection, *, status: str, started_at: str) -> int:
    run_id = repo.create_run(
        conn, "uploaded.pdf", session_id="s", output_dir="/tmp/x", status=status
    )
    conn.execute(
        "UPDATE runs SET started_at = ? WHERE id = ?", (started_at, run_id)
    )
    conn.commit()
    return run_id


def test_old_running_run_is_aborted(conn):
    old = _iso(datetime.now(timezone.utc) - timedelta(hours=48))
    run_id = _make_run(conn, status="running", started_at=old)

    n = repo.reconcile_stale_runs(conn)
    conn.commit()

    assert n == 1
    row = conn.execute(
        "SELECT status, ended_at FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "aborted"
    assert row["ended_at"], "reaped run must carry a terminal ended_at timestamp"


def test_fresh_running_run_is_left_alone(conn):
    fresh = _iso(datetime.now(timezone.utc) - timedelta(minutes=2))
    run_id = _make_run(conn, status="running", started_at=fresh)

    n = repo.reconcile_stale_runs(conn)
    conn.commit()

    assert n == 0
    row = conn.execute(
        "SELECT status FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "running"


def test_running_run_with_blank_started_at_is_reaped(conn):
    # A `running` row with no started_at is definitionally broken (run-start
    # always stamps it) — treat as stale regardless of age.
    run_id = _make_run(conn, status="running", started_at="")

    n = repo.reconcile_stale_runs(conn)
    conn.commit()

    assert n == 1
    row = conn.execute(
        "SELECT status FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "aborted"


def test_terminal_and_draft_runs_are_untouched(conn):
    old = _iso(datetime.now(timezone.utc) - timedelta(hours=48))
    completed = _make_run(conn, status="completed", started_at=old)
    draft = repo.create_run(
        conn, "uploaded.pdf", session_id="s", output_dir="/tmp/x", status="draft"
    )
    conn.commit()

    n = repo.reconcile_stale_runs(conn)
    conn.commit()

    assert n == 0
    assert conn.execute(
        "SELECT status FROM runs WHERE id = ?", (completed,)
    ).fetchone()["status"] == "completed"
    assert conn.execute(
        "SELECT status FROM runs WHERE id = ?", (draft,)
    ).fetchone()["status"] == "draft"


def test_startup_mode_reaps_all_orphaned_running_rows(conn):
    # At startup EVERY running row is orphaned (no stream can have started yet),
    # so the _lifespan call passes max_age_hours=0 to reap all of them —
    # including a row that started seconds ago. This closes the
    # crash-then-immediate-restart gap where a young orphan would survive a 6h
    # threshold forever (peer review).
    fresh = _iso(datetime.now(timezone.utc) - timedelta(seconds=30))
    run_id = _make_run(conn, status="running", started_at=fresh)

    # Default threshold spares it; startup mode reaps it.
    assert repo.reconcile_stale_runs(conn, max_age_hours=6) == 0
    assert repo.reconcile_stale_runs(conn, max_age_hours=0) == 1
    conn.commit()
    assert conn.execute(
        "SELECT status FROM runs WHERE id = ?", (run_id,)
    ).fetchone()["status"] == "aborted"


def test_custom_age_threshold_is_honoured(conn):
    started = _iso(datetime.now(timezone.utc) - timedelta(hours=3))
    run_id = _make_run(conn, status="running", started_at=started)

    # 3h-old run: not stale at a 6h threshold, stale at a 1h threshold.
    assert repo.reconcile_stale_runs(conn, max_age_hours=6) == 0
    assert repo.reconcile_stale_runs(conn, max_age_hours=1) == 1
    conn.commit()
    assert conn.execute(
        "SELECT status FROM runs WHERE id = ?", (run_id,)
    ).fetchone()["status"] == "aborted"
