"""Run-83 hardening Phase 1: aborted runs leave no agent row 'running'.

The incident (Windows run 83, 2026-08-05): the user hit Stop All during the
notes reviewer. That cancel handler finalized only its own pseudo-agent row
and returned early, so all five extraction rows and the CORRECTION row sat
at 'running' forever under an 'aborted' run.

Two coupled defences, both pinned here:

1. Face + notes agent rows are finalized from in-memory results right after
   the merge, BEFORE the reviewer / notes-reviewer stages — so a later
   cancel can no longer orphan them (source-order pin below).
2. `_safe_mark_finished` reconciles: once a run is terminal, any child row
   still 'running' is closed as 'cancelled'. Backstop only — it never
   invents 'succeeded' from the presence of artifacts.
"""
from __future__ import annotations

import inspect
import sqlite3

import pytest

from db import repository as repo
from db.schema import init_db


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "xbrl_agent.db"
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _agent_statuses(conn, run_id):
    rows = conn.execute(
        "SELECT statement_type, status, error_type FROM run_agents "
        "WHERE run_id = ? ORDER BY id", (run_id,),
    ).fetchall()
    return {r["statement_type"]: (r["status"], r["error_type"]) for r in rows}


def test_reconcile_closes_only_running_rows(db):
    run_id = repo.create_run(db, pdf_filename="doc.pdf")
    running_id = repo.create_run_agent(db, run_id, statement_type="SOFP")
    done_id = repo.create_run_agent(db, run_id, statement_type="SOPL")
    repo.finish_run_agent(db, done_id, status="succeeded")
    failed_id = repo.create_run_agent(db, run_id, statement_type="SOCF")
    repo.finish_run_agent(db, failed_id, status="failed", error_type="timeout")

    closed = repo.reconcile_unfinished_run_agents(db, run_id)

    assert closed == 1
    statuses = _agent_statuses(db, run_id)
    assert statuses["SOFP"] == ("cancelled", "cancelled")
    # Terminal rows are untouched — the backstop never rewrites real
    # outcomes, and never promotes anything to 'succeeded'.
    assert statuses["SOPL"] == ("succeeded", None)
    assert statuses["SOCF"] == ("failed", "timeout")
    row = db.execute(
        "SELECT ended_at FROM run_agents WHERE id = ?", (running_id,)
    ).fetchone()
    assert row["ended_at"], "closed row must carry an ended_at stamp"


def test_reconcile_marks_system_incident_as_failed_not_cancelled(db):
    run_id = repo.create_run(db, pdf_filename="doc.pdf")
    repo.create_run_agent(db, run_id, statement_type="SYSTEM")

    closed = repo.reconcile_unfinished_run_agents(db, run_id)

    assert closed == 1
    assert _agent_statuses(db, run_id)["SYSTEM"] == (
        "failed", "coordinator_incident",
    )


def test_reconcile_scoped_to_the_given_run(db):
    run_a = repo.create_run(db, pdf_filename="a.pdf")
    run_b = repo.create_run(db, pdf_filename="b.pdf")
    repo.create_run_agent(db, run_a, statement_type="SOFP")
    repo.create_run_agent(db, run_b, statement_type="SOFP")

    closed = repo.reconcile_unfinished_run_agents(db, run_a)

    assert closed == 1
    assert _agent_statuses(db, run_a)["SOFP"][0] == "cancelled"
    assert _agent_statuses(db, run_b)["SOFP"][0] == "running"


def test_safe_mark_finished_reconciles_stragglers(db):
    """Reproduces the run-83 shape end-state: cancel during a late stage.

    The cancel handlers call `_safe_mark_finished(db, run_id, "aborted")`
    and return early. After that single call, NO row may remain 'running'.
    """
    import server

    run_id = repo.create_run(db, pdf_filename="ime-2024.docx")
    # The run-83 incident shape: extraction finished (finalized by the
    # early persist), the CORRECTION pseudo-agent exited un-finalized
    # after its wall-clock breach, and the NOTES_VALIDATOR (notes
    # reviewer) was mid-flight when the user hit Stop All.
    done_id = repo.create_run_agent(db, run_id, statement_type="SOFP")
    repo.finish_run_agent(db, done_id, status="succeeded")
    repo.create_run_agent(db, run_id, statement_type="CORRECTION")
    repo.create_run_agent(db, run_id, statement_type="NOTES_VALIDATOR")
    repo.create_run_agent(db, run_id, statement_type="NOTES_LIST_OF_NOTES")

    assert server._safe_mark_finished(db, run_id, "aborted") is True

    run_row = db.execute(
        "SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run_row["status"] == "aborted"
    statuses = _agent_statuses(db, run_id)
    assert statuses["SOFP"] == ("succeeded", None)
    assert statuses["CORRECTION"][0] == "cancelled"
    assert statuses["NOTES_VALIDATOR"][0] == "cancelled"
    assert statuses["NOTES_LIST_OF_NOTES"][0] == "cancelled"
    assert not [
        s for s, (st, _e) in statuses.items() if st == "running"
    ], "a terminal run must leave zero 'running' child rows"


def test_safe_mark_finished_survives_reconcile_failure(db, monkeypatch):
    """Gotcha #10: the reconcile is best-effort inside the swallow-all
    helper — a reconcile crash must not block the terminal-status write."""
    import server

    run_id = repo.create_run(db, pdf_filename="doc.pdf")
    repo.create_run_agent(db, run_id, statement_type="SOFP")

    def _boom(conn, rid):
        raise RuntimeError("reconcile exploded")

    monkeypatch.setattr(repo, "reconcile_unfinished_run_agents", _boom)
    assert server._safe_mark_finished(db, run_id, "aborted") is True
    run_row = db.execute(
        "SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run_row["status"] == "aborted"


def test_face_and_notes_rows_finalized_before_reviewer_launch():
    """Source-order pin (Step 1): the early persist call sits BEFORE the
    reviewer launch inside run_multi_agent_stream, so extraction rows are
    terminal in the DB before any post-extraction stage can be cancelled.
    """
    import server

    src = inspect.getsource(server.run_multi_agent_stream)
    persist_call = src.index("_persist_face_and_notes_agent_rows()\n",
                             src.index("def _persist_face_and_notes_agent_rows"))
    reviewer_launch = src.index("_run_reviewer_pass(")
    notes_reviewer_launch = src.index("_run_notes_reviewer_pass(")
    assert persist_call < reviewer_launch, (
        "face/notes agent rows must be finalized before the reviewer starts"
    )
    assert persist_call < notes_reviewer_launch
