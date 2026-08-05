"""Cross-check rows must be durable BEFORE the reviewer pass runs.

The rows used to be written once, in the persistence block at the very end of
``run_multi_agent_stream`` — after the reviewer and the notes reviewer. Stopping
a run during either pass therefore discarded the failing-check diagnosis, which
is the state an operator stops a run to look at. Reported against a Windows run
(2026-08-05): the reviewer started on six failed checks and ``cross_checks``
ended up with no rows at all.

Two contracts pinned here:

  1. ``_persist_cross_check_results`` REPLACES the run's rows. It runs twice on
     the live path (initial pass, then post-reviewer), and appending would leave
     a run carrying both passes.
  2. The initial-pass write is ordered before the reviewer stage in the
     pipeline, and does not commit — it rides ``_safe_mark_finished``'s commit,
     which fires on every terminal path including cancel.
"""
from __future__ import annotations

import inspect

import pytest

from cross_checks.framework import CrossCheckResult
from db.repository import create_run, db_session, fetch_cross_checks
from db.schema import init_db


def _results(status: str, message: str) -> list[CrossCheckResult]:
    return [
        CrossCheckResult(
            name="sofp_balance", status=status, expected=1000.0,
            actual=1000.0, diff=0.0, tolerance=1.0, message=message,
        ),
    ]


@pytest.fixture()
def run_conn(tmp_path):
    db_path = str(tmp_path / "audit.db")
    init_db(db_path)
    with db_session(db_path) as conn:
        run_id = create_run(conn, pdf_filename="x.pdf", session_id="s1")
        conn.commit()
        yield conn, run_id


def test_second_persist_replaces_the_first(run_conn):
    """The post-reviewer write supersedes the initial pass rather than
    doubling the run's rows."""
    from server import _persist_cross_check_results

    conn, run_id = run_conn

    _persist_cross_check_results(conn, run_id, _results("failed", "before"))
    conn.commit()
    assert len(fetch_cross_checks(conn, run_id)) == 1

    _persist_cross_check_results(conn, run_id, _results("passed", "after"))
    conn.commit()

    rows = fetch_cross_checks(conn, run_id)
    assert len(rows) == 1, "second persist must replace, not append"
    assert rows[0].status == "passed"
    assert rows[0].message == "after"


def test_persist_does_not_commit(run_conn):
    """The helper must leave the write pending so it flushes with the
    lifecycle's own terminal commit — adding a commit here would make
    `merged_workbook_path` durable on its own schedule (gotcha #10)."""
    from server import _persist_cross_check_results

    conn, run_id = run_conn

    _persist_cross_check_results(conn, run_id, _results("failed", "pending"))
    conn.rollback()

    assert fetch_cross_checks(conn, run_id) == [], (
        "helper committed on its own — an uncommitted write must be "
        "discardable by the caller"
    )


def test_initial_pass_is_persisted_before_the_reviewer_stage():
    """Source-order check: the initial-pass persist must appear before the
    reviewer stage, or a cancel during the reviewer loses the diagnosis
    again."""
    import server

    src = inspect.getsource(server.run_multi_agent_stream)

    persist_at = src.find("_persist_cross_check_results")
    reviewing_at = src.find('_emit_stage("reviewing")')

    assert persist_at != -1, "initial-pass persist call is missing"
    assert reviewing_at != -1, "reviewer stage marker is missing"
    assert persist_at < reviewing_at, (
        "cross-check results must be persisted BEFORE the reviewer pass "
        "starts — otherwise a Stop-All during the reviewer discards them"
    )


def test_persisted_rows_survive_a_caller_rollback(run_conn):
    """The point of writing early is durability through a Stop-All, and the
    cancel path rolls back. A write left pending is therefore not persisted at
    all — it has to be committed at the call site."""
    from server import _persist_cross_check_results

    conn, run_id = run_conn

    _persist_cross_check_results(conn, run_id, _results("failed", "diagnosis"))
    conn.commit()          # what the live call site now does
    conn.rollback()        # what a Stop-All does next

    rows = fetch_cross_checks(conn, run_id)
    assert len(rows) == 1 and rows[0].message == "diagnosis"


def test_the_reviewer_snapshot_can_run_after_the_initial_persist(tmp_path):
    """The reproduced deadlock (peer review, 2026-08-05).

    SQLite allows ONE writer. An uncommitted INSERT/DELETE on the lifecycle
    connection holds the write lock for the whole reviewer stage, and the first
    thing the reviewer does is `ensure_snapshot` on its own connection with
    BEGIN IMMEDIATE. Left pending, that blocks for busy_timeout and raises
    `database is locked`, which the pass reports as `snapshot_failed` — the
    change meant to preserve the failing-check diagnosis destroyed the pass
    that acts on it.

    Uses the REAL persistence and snapshot functions: a fake for either side
    is exactly what let the original test pass while the live path deadlocked.
    """
    from concept_model.versioning import ensure_snapshot
    from server import _persist_cross_check_results

    db_path = str(tmp_path / "audit.db")
    init_db(db_path)
    with db_session(db_path) as conn:
        run_id = create_run(conn, pdf_filename="x.pdf", session_id="s1")
        conn.commit()

        _persist_cross_check_results(conn, run_id, _results("failed", "six"))
        conn.commit()
        assert not conn.in_transaction, (
            "the lifecycle connection must not hold the write lock into the "
            "reviewer stage"
        )

        # The reviewer's own connection, exactly as `_run_reviewer_pass` opens
        # it. This raised OperationalError('database is locked') before.
        ensure_snapshot(db_path, run_id)

        # And the diagnosis is still there for the reviewer to work from.
        assert len(fetch_cross_checks(conn, run_id)) == 1


def test_the_live_call_site_commits(run_conn):
    """Source check: a future edit that drops the commit reintroduces the
    deadlock, and the symptom (`snapshot_failed`) points nowhere near here."""
    import inspect

    import server

    src = inspect.getsource(server.run_multi_agent_stream)
    start = src.find("Persist the INITIAL pass now")
    assert start != -1, "initial-pass persist block is missing"
    block = src[start:start + 2000]
    call_at = block.find("_persist_cross_check_results(")
    commit_at = block.find("db_conn.commit()", call_at)
    assert call_at != -1 and commit_at != -1, (
        "the initial-pass persist must be followed by a commit — an "
        "uncommitted write deadlocks the reviewer's snapshot"
    )
