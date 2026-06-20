"""Stop-All must be able to cancel the REVIEWER pass (2026-06-20 fix).

Regression: only the extraction + notes COORDINATORS registered their asyncio
tasks in ``task_registry``. The reviewer (correction) task — launched by
``server.run_multi_agent_stream`` via ``asyncio.create_task`` once a hard
cross-check fails — was never registered. So ``POST /api/abort`` →
``task_registry.cancel_all`` found nothing live, returned 0 (HTTP 404), and the
reviewer ran to completion regardless. Users reported "Stop All doesn't stop
the reviewer."

The fix registers the reviewer task under ``CORRECTION`` (and the notes
validator under ``NOTES_VALIDATOR``) and mirrors the proven coordinator-cancel
path: on ``CancelledError`` the run finalizes as ``aborted`` and stops.

This test drives a run to the reviewer stage with a failing cross-check, then —
from inside the mocked reviewer pass — asserts the task is registered and that
``cancel_all`` reaches it, and finally that the run lands ``aborted``.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from coordinator import AgentResult, CoordinatorResult
from cross_checks.framework import CrossCheckResult
from statement_types import StatementType
from workbook_merger import MergeResult


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    session_id = "stop-all-reviewer-session"
    out = tmp_path / "output"
    (out / session_id).mkdir(parents=True)
    (out / session_id / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")

    import server
    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", out / "xbrl_agent.db")
    fake_env = tmp_path / ".env-test"
    fake_env.write_text("")
    monkeypatch.setattr(server, "ENV_FILE", fake_env)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("TEST_MODEL", "test-model-default")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    monkeypatch.setenv("XBRL_AUTO_REVIEW", "true")  # reviewer must auto-launch

    return TestClient(server.app), session_id, out


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def test_stop_all_cancels_running_reviewer(session_env):
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"
    session_dir = out / session_id
    sofp_path = session_dir / "SOFP_filled.xlsx"
    sofp_path.write_bytes(b"PK\x03\x04 fake xlsx")
    merged_path = str(session_dir / "filled.xlsx")

    coordinator_result = CoordinatorResult(agent_results=[
        AgentResult(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            status="succeeded",
            workbook_path=str(sofp_path),
        )
    ])

    async def mock_coordinator_run(
        config, infopack=None, event_queue=None, session_id=None, **_kwargs,
    ):
        if event_queue is not None:
            await event_queue.put({
                "event": "complete",
                "data": {
                    "success": True, "agent_id": "sofp",
                    "agent_role": "SOFP", "workbook_path": str(sofp_path),
                },
            })
            await event_queue.put(None)
        return coordinator_result

    # One FAILING cross-check → should_correct is True → reviewer launches.
    failing = [CrossCheckResult(
        name="sofp_balance", status="failed",
        expected=1000.0, actual=900.0, diff=100.0, tolerance=1.0,
        message="assets != equity + liabilities",
    )]

    # Captured from inside the reviewer so the test can assert on the chain.
    observed: dict = {}

    async def mock_reviewer(**kwargs):
        import task_registry
        # The FIX: the reviewer task must be registered so Stop-All sees it.
        observed["registered"] = (
            task_registry.get_task(session_id, "CORRECTION") is not None
        )
        # Simulate the user hitting Stop All: cancel_all must find + cancel
        # THIS running task (it returns the count of live tasks cancelled).
        observed["cancelled_count"] = task_registry.cancel_all(session_id)
        # Cancellation lands at the next await as CancelledError, exactly as a
        # real Stop-All would interrupt the in-flight reviewer.
        await asyncio.sleep(5)
        return {"writes_performed": 0}  # unreached

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=mock_coordinator_run), \
         patch("workbook_merger.merge", return_value=MergeResult(
             success=True, output_path=merged_path, sheets_copied=1)), \
         patch("cross_checks.framework.run_all", return_value=failing), \
         patch("cross_checks.framework.run_all_facts", return_value=failing), \
         patch("correction.reviewer_agent.load_open_conflicts", return_value=[]), \
         patch("server._run_reviewer_pass", side_effect=mock_reviewer):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200

    # The reviewer task was registered (the core fix) and cancel_all reached it.
    assert observed.get("registered") is True, (
        "reviewer task was not registered in task_registry — Stop-All "
        "(cancel_all) cannot reach it"
    )
    assert observed.get("cancelled_count", 0) >= 1, (
        "cancel_all found no live task to cancel during the reviewer stage"
    )

    # Cancelling the reviewer finalizes the run as 'aborted' and surfaces the
    # cancellation to the still-connected SSE client.
    assert "Run cancelled during review" in resp.text

    conn = _open_db(db_path)
    try:
        row = conn.execute("SELECT * FROM runs").fetchone()
        # The CORRECTION pseudo-agent audit row must be finalized too — not
        # left dangling 'running' under an 'aborted' run (peer-review Finding 1).
        corr = conn.execute(
            "SELECT status FROM run_agents WHERE statement_type='CORRECTION'"
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "aborted", \
        f"expected status='aborted' after reviewer cancel, got {row['status']!r}"
    assert row["ended_at"] is not None
    assert corr is not None, "CORRECTION run_agent row was never created"
    assert corr["status"] == "cancelled", (
        f"CORRECTION row must be 'cancelled' on reviewer abort, got "
        f"{corr['status']!r}"
    )


def test_reviewer_task_unregistered_after_normal_completion(session_env):
    """On a normal (non-cancelled) reviewer pass the task is unregistered in
    the finally, leaving no stale ref behind for a later cancel_all."""
    client, session_id, out = session_env
    session_dir = out / session_id
    sofp_path = session_dir / "SOFP_filled.xlsx"
    sofp_path.write_bytes(b"PK\x03\x04 fake xlsx")
    merged_path = str(session_dir / "filled.xlsx")

    coordinator_result = CoordinatorResult(agent_results=[
        AgentResult(statement_type=StatementType.SOFP, variant="CuNonCu",
                    status="succeeded", workbook_path=str(sofp_path))
    ])

    async def mock_coordinator_run(
        config, infopack=None, event_queue=None, session_id=None, **_kwargs,
    ):
        if event_queue is not None:
            await event_queue.put(None)
        return coordinator_result

    failing = [CrossCheckResult(name="sofp_balance", status="failed",
                                expected=1.0, actual=0.0, diff=1.0,
                                tolerance=0.5, message="x")]
    observed: dict = {}

    async def mock_reviewer(**kwargs):
        import task_registry
        observed["during"] = (
            task_registry.get_task(session_id, "CORRECTION") is not None
        )
        return {"writes_performed": 0}

    run_config = {
        "statements": ["SOFP"], "variants": {"SOFP": "CuNonCu"},
        "models": {}, "infopack": None, "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=mock_coordinator_run), \
         patch("workbook_merger.merge", return_value=MergeResult(
             success=True, output_path=merged_path, sheets_copied=1)), \
         patch("cross_checks.framework.run_all", return_value=failing), \
         patch("cross_checks.framework.run_all_facts", return_value=failing), \
         patch("correction.reviewer_agent.load_open_conflicts", return_value=[]), \
         patch("server._run_reviewer_pass", side_effect=mock_reviewer):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    assert observed.get("during") is True  # registered while running
    # Unregistered after completion (finally) — no stale ref.
    import task_registry
    assert task_registry.get_task(session_id, "CORRECTION") is None
