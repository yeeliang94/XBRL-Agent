"""PLAN-stop-and-validation-visibility Phase 2: Stop-All preserves partial.

When the user clicks "Stop All" mid-run, any per-statement
``{stmt}_filled.xlsx`` already written by completed agents must survive
into a downloadable merged workbook. Today the cancel handler at
``server._run_multi_agent_stream`` (server.py around line 1830) returns
immediately on ``asyncio.CancelledError`` without invoking
``workbook_merger.merge`` or ``mark_run_merged`` — so even though the
per-statement files exist on disk, the History row's
``merged_workbook_path`` stays NULL and the download endpoint can't serve
anything.

Step 1.1 (RED): pin the broken behaviour with an end-to-end test that
runs the multi-agent endpoint, has the coordinator raise
``CancelledError`` after writing two of three per-statement files, and
asserts that the runs row ends up with ``status='aborted'`` AND
``merged_workbook_path`` pointing at the merged xlsx file.

Step 2.1 (GREEN, in a follow-up edit) refactors the cancel handler to
call a ``_attempt_partial_merge`` helper before marking aborted.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from coordinator import AgentResult, CoordinatorResult
from statement_types import StatementType
from workbook_merger import MergeResult


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """Wire server paths at a temp directory.

    Mirrors test_server_run_lifecycle.py's fixture so the contract under
    test is exercised against the same harness as the existing terminal-
    status tests (gotcha #10).
    """
    session_id = "stop-all-partial-session"
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

    return TestClient(server.app), session_id, out


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def test_stop_all_after_partial_completion_preserves_merged_workbook(session_env):
    """Two of three per-statement workbooks land on disk before the
    coordinator is cancelled. The cancel handler must still attempt a
    merge over the survivors so the runs row carries a non-NULL
    ``merged_workbook_path`` and the download endpoint can serve a
    real (if partial) ``filled.xlsx``.

    RED today: cancel handler returns before merge → merged_workbook_path
    stays NULL.
    """
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"
    session_dir = out / session_id

    # Pre-create the per-statement workbooks that "completed" before the
    # user hit Stop All. We don't care what's in them — the contract is
    # only that the merge helper is given a chance to act on them.
    sofp_path = session_dir / "SOFP_filled.xlsx"
    sopl_path = session_dir / "SOPL_filled.xlsx"
    sofp_path.write_bytes(b"PK\x03\x04 fake xlsx")
    sopl_path.write_bytes(b"PK\x03\x04 fake xlsx")

    merged_path = str(session_dir / "filled.xlsx")

    async def cancelled_coordinator(
        config, infopack=None, event_queue=None, session_id=None, **_kwargs,
    ):
        # Simulate two agents completing successfully, then a Stop-All.
        # The fan-in sentinel is what the drain loop looks for.
        if event_queue is not None:
            await event_queue.put({
                "event": "complete",
                "data": {
                    "success": True,
                    "agent_id": "sofp",
                    "agent_role": "SOFP",
                    "workbook_path": str(sofp_path),
                },
            })
            await event_queue.put({
                "event": "complete",
                "data": {
                    "success": True,
                    "agent_id": "sopl",
                    "agent_role": "SOPL",
                    "workbook_path": str(sopl_path),
                },
            })
            await event_queue.put(None)
        # The third agent never finishes — Stop All cancels the
        # coordinator before SOCI/SOCF/SOCIE can complete.
        raise asyncio.CancelledError()

    run_config = {
        "statements": ["SOFP", "SOPL", "SOCI"],
        "variants": {"SOFP": "CuNonCu", "SOPL": "Function", "SOCI": "NetOfTax"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    # Patch the merger to "succeed" — we just need to verify the cancel
    # handler invoked it and threaded the merged path through to the DB.
    # The real merger is unit-tested elsewhere; here we only care about
    # the cancel-handler's plumbing.
    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch(
             "coordinator.run_extraction",
             side_effect=cancelled_coordinator,
         ), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(
                 success=True,
                 output_path=merged_path,
                 sheets_copied=2,
             ),
         ), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200

    conn = _open_db(db_path)
    try:
        rows = conn.execute("SELECT * FROM runs").fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    row = rows[0]

    # Stop-All still terminates the run as 'aborted' — that contract from
    # gotcha #10 is unchanged.
    assert row["status"] == "aborted", \
        f"expected status='aborted', got {row['status']!r}"
    assert row["ended_at"] is not None, \
        "ended_at must be set for any terminal status"

    # The new contract: merged_workbook_path must be the partial filled.xlsx,
    # not NULL. Today this assertion fails because the cancel handler
    # returns before the merge step runs.
    assert row["merged_workbook_path"] == merged_path, (
        "Stop-All must preserve partial work: expected "
        f"merged_workbook_path={merged_path!r}, got "
        f"{row['merged_workbook_path']!r}"
    )


def test_stop_all_emits_partial_merge_sse_event(session_env):
    """The cancel handler must surface a ``partial_merge`` SSE event so
    the frontend can render a "Saved partial workbook with SOFP, SOPL"
    banner. Without this event the user sees only the generic "Run
    cancelled" error and has no idea anything was saved.
    """
    client, session_id, out = session_env
    session_dir = out / session_id

    sofp_path = session_dir / "SOFP_filled.xlsx"
    sopl_path = session_dir / "SOPL_filled.xlsx"
    sofp_path.write_bytes(b"PK\x03\x04 fake xlsx")
    sopl_path.write_bytes(b"PK\x03\x04 fake xlsx")
    merged_path = str(session_dir / "filled.xlsx")

    async def cancelled_coordinator(
        config, infopack=None, event_queue=None, session_id=None, **_kwargs,
    ):
        if event_queue is not None:
            await event_queue.put(None)
        raise asyncio.CancelledError()

    run_config = {
        "statements": ["SOFP", "SOPL", "SOCI"],
        "variants": {"SOFP": "CuNonCu", "SOPL": "Function", "SOCI": "NetOfTax"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch(
             "coordinator.run_extraction",
             side_effect=cancelled_coordinator,
         ), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(
                 success=True,
                 output_path=merged_path,
                 sheets_copied=2,
             ),
         ), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200

    # The SSE body is a stream of `event: <type>\ndata: <json>\n\n` frames.
    # We just need to confirm the partial_merge type appears at least once.
    body = resp.text
    assert "event: partial_merge" in body, (
        "Expected a partial_merge SSE event after Stop-All over completed "
        f"per-statement files. Body did not contain the event type. "
        f"Body[:600]: {body[:600]!r}"
    )


def test_stop_all_with_no_partial_files_does_not_emit_partial_merge(session_env):
    """If no per-statement files exist on disk when the user hits Stop
    All, the cancel handler must NOT emit a misleading partial_merge
    event. The run is still marked aborted, but there's nothing to
    merge so nothing to advertise.
    """
    client, session_id, out = session_env
    # Deliberately do NOT pre-create any *_filled.xlsx files.

    async def cancelled_coordinator(
        config, infopack=None, event_queue=None, session_id=None, **_kwargs,
    ):
        if event_queue is not None:
            await event_queue.put(None)
        raise asyncio.CancelledError()

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    # Merge would fail anyway with no inputs, but stub it to be safe.
    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch(
             "coordinator.run_extraction",
             side_effect=cancelled_coordinator,
         ), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(success=False, errors=["no inputs"]),
         ), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    body = resp.text
    assert "event: partial_merge" not in body, (
        "partial_merge must not fire when there is nothing to merge. "
        f"Body[:600]: {body[:600]!r}"
    )
