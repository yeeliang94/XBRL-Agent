"""PLAN-stop-and-validation-visibility Phase 5: cross-check progress events.

Today the post-correction cross-check re-run at server.py around
line 2111 calls ``run_cross_checks(...)`` synchronously and emits zero
SSE events while it runs — a silent dead zone of up to 5-10 minutes on
large workbooks. This test pins the desired contract: per-check progress
events fire as each check completes so the Validator tab can fill in
rows live instead of all-at-end on ``run_complete``.

Step 1.3 (RED): assert ``cross_check_start`` and at least one
``cross_check_result`` event ride the SSE stream during a normal run.

Step 5.1-5.2 (GREEN, follow-up): server wraps ``run_cross_checks`` with
a progress emitter that pushes the new event types onto the queue.
"""
from __future__ import annotations

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
    session_id = "cross-check-progress-session"
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


def _happy_coordinator(agent_results):
    async def mock_run(config, infopack=None, event_queue=None, session_id=None, **_kwargs):
        if event_queue is not None:
            for ar in agent_results:
                await event_queue.put({
                    "event": "complete",
                    "data": {
                        "success": ar.status == "succeeded",
                        "agent_id": ar.statement_type.value.lower(),
                        "agent_role": ar.statement_type.value,
                        "workbook_path": ar.workbook_path,
                        "error": ar.error,
                    },
                })
            await event_queue.put(None)
        return CoordinatorResult(agent_results=list(agent_results))
    return mock_run


def test_initial_cross_check_run_emits_progress_events(session_env):
    """A normal multi-statement run with several cross-checks must
    surface ``cross_check_start`` then per-check ``cross_check_result``
    events before ``run_complete``. RED today — server emits nothing
    during cross-check execution."""
    client, session_id, out = session_env

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
        AgentResult(
            statement_type=StatementType.SOPL, variant="Function",
            status="succeeded",
            workbook_path=str(out / session_id / "SOPL_filled.xlsx"),
        ),
    ]

    # Synthesize three cross-check results so we can confirm at least
    # one "result" event makes it through. The shape mirrors what the
    # real framework returns. Peer-review fix (2026-04-27): cross-check
    # progress is now emitted LIVE via the ``on_check`` callback that
    # ``run_all`` invokes per-check, so a fake that just returns a list
    # without calling on_check would not produce the per-check events
    # — use ``side_effect`` to mirror the real framework's contract.
    fake_results = [
        CrossCheckResult(name="check_a", status="passed", message="ok"),
        CrossCheckResult(name="check_b", status="failed",
                         expected=100.0, actual=90.0, diff=-10.0,
                         tolerance=1.0, message="off by 10"),
        CrossCheckResult(name="check_c", status="warning", message="advisory"),
    ]

    def _fake_run_all(checks, workbook_paths, run_config_arg, tolerance=1.0,
                     on_check=None):
        for i, r in enumerate(fake_results):
            if on_check is not None:
                on_check(i, len(fake_results), r)
        return list(fake_results)

    run_config = {
        "statements": ["SOFP", "SOPL"],
        "variants": {"SOFP": "CuNonCu", "SOPL": "Function"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    # The 'failed' check_b will trigger the correction agent path. Stub
    # that out so we don't try to build a real PydanticAI agent against
    # the fake-model string (which pydantic-ai 1.77 rejects).
    async def _fake_correction(*args, **kwargs):
        return {
            "invoked": True, "writes_performed": 0,
            "error": None, "exhausted": False,
            "total_tokens": 0, "total_cost": 0.0,
            "turns_used": 0, "max_turns": 8,
        }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch(
             "coordinator.run_extraction",
             side_effect=_happy_coordinator(agent_results),
         ), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(
                 success=True,
                 output_path=str(out / session_id / "filled.xlsx"),
                 sheets_copied=2,
             ),
         ), \
         patch("cross_checks.framework.run_all", side_effect=_fake_run_all), \
         patch("server._run_correction_pass", side_effect=_fake_correction), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    body = resp.text

    # Start event must precede any per-check result.
    assert "event: cross_check_start" in body, (
        "Expected a cross_check_start SSE event before per-check progress. "
        f"Body[:500]: {body[:500]!r}"
    )
    # At least one result event — the 3-fake list above guarantees we
    # have something to emit.
    assert "event: cross_check_result" in body, (
        "Expected at least one cross_check_result SSE event during the "
        f"initial cross-check pass. Body[:500]: {body[:500]!r}"
    )
    # Complete event should fire too — gives the frontend an explicit
    # "all done" signal to flip the spinner off.
    assert "event: cross_check_complete" in body, (
        "Expected cross_check_complete to fire after the per-check "
        f"results. Body[:500]: {body[:500]!r}"
    )

    # The events must arrive BEFORE run_complete so the Validator tab
    # can render them progressively rather than all at the terminal
    # event.
    cc_start_idx = body.index("event: cross_check_start")
    rc_idx = body.index("event: run_complete")
    assert cc_start_idx < rc_idx, (
        "cross_check_start must arrive before run_complete; the whole "
        "point is progressive UI updates."
    )


def test_cross_check_progress_is_actually_live(session_env):
    """Peer-review fix (2026-04-27): the original implementation
    batched all cross_check_result events AFTER ``run_cross_checks``
    returned, so a slow validation pass left the UI in a silent gap.
    Assert that progress callbacks fire INTERLEAVED with the check
    run — i.e., the on_check callback we install actually receives
    each result as it lands, not all in one batch at the end.

    We exercise this by patching ``cross_checks.framework.run_all`` to
    invoke the threaded ``on_check`` callback explicitly inside the
    function body — which is the contract Phase 5.2 promises.
    """
    client, session_id, out = session_env

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
    ]

    fake_results = [
        CrossCheckResult(name="check_a", status="passed", message="ok"),
        CrossCheckResult(name="check_b", status="passed", message="ok"),
        CrossCheckResult(name="check_c", status="passed", message="ok"),
    ]

    callback_calls = []

    def _fake_run_all(checks, workbook_paths, run_config, tolerance=1.0,
                     on_check=None):
        # Simulate a check-by-check pass that calls on_check after each.
        for i, r in enumerate(fake_results):
            if on_check is not None:
                callback_calls.append((i, len(fake_results), r.name))
                on_check(i, len(fake_results), r)
        return list(fake_results)

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch(
             "coordinator.run_extraction",
             side_effect=_happy_coordinator(agent_results),
         ), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(
                 success=True,
                 output_path=str(out / session_id / "filled.xlsx"),
                 sheets_copied=1,
             ),
         ), \
         patch("cross_checks.framework.run_all", side_effect=_fake_run_all), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    # The server must have threaded on_check into run_all — the fake
    # captured 3 calls, one per fake result.
    assert len(callback_calls) == 3, (
        f"Expected 3 on_check invocations (one per result); got "
        f"{len(callback_calls)}: {callback_calls!r}"
    )

    body = resp.text
    # All three result events must reach the wire.
    assert body.count("event: cross_check_result") >= 3, (
        f"Expected 3 cross_check_result frames on the wire. "
        f"Body[:1000]: {body[:1000]!r}"
    )


def test_cross_check_progress_carries_phase_label(session_env):
    """The progress events carry a ``phase`` field so the frontend can
    distinguish the initial cross-check pass from the post-correction
    re-run. The initial pass is labelled ``"initial"``."""
    client, session_id, out = session_env

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
    ]
    fake_results = [
        CrossCheckResult(name="check_a", status="passed", message="ok"),
    ]
    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch(
             "coordinator.run_extraction",
             side_effect=_happy_coordinator(agent_results),
         ), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(
                 success=True,
                 output_path=str(out / session_id / "filled.xlsx"),
                 sheets_copied=1,
             ),
         ), \
         patch("cross_checks.framework.run_all", return_value=fake_results), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    body = resp.text

    # Find the cross_check_start event's data line and confirm phase=initial.
    # Frame format: 'event: cross_check_start\ndata: {...}\n\n'
    start_token = "event: cross_check_start\ndata: "
    start_idx = body.find(start_token)
    assert start_idx >= 0, "cross_check_start event not present"
    # Walk forward to the end of that data line (next \n).
    payload_start = start_idx + len(start_token)
    payload_end = body.index("\n", payload_start)
    payload = body[payload_start:payload_end]
    assert '"phase": "initial"' in payload, (
        f"cross_check_start payload missing phase=initial. Got: {payload!r}"
    )
