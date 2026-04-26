"""PLAN-stop-and-validation-visibility Phase 4: surface silent failures.

The post-extraction half of ``run_multi_agent_stream`` had two paths
that swallowed errors instead of surfacing them as SSE events:

1. **Merge failure** — when ``merge_workbooks`` returns
   ``success=False`` the failure is logged via ``logger.warning`` but
   nothing reaches the SSE stream. The user sees "100% extraction"
   then ``run_complete`` arrives with ``success=false`` and no clue
   why.

2. **Cross-check raise** — ``run_cross_checks`` is called directly
   without try/except; if a malformed workbook makes it raise (corrupt
   sheet, missing required cell), the exception propagates to the
   outer except and the run is marked failed without a meaningful
   user-facing message.

Both fail-modes are pinned here so the GREEN implementation has to
keep emitting them — never silent again.
"""
from __future__ import annotations

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
    session_id = "silent-exception-session"
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


def test_merge_failure_emits_sse_error(session_env):
    """When ``merge_workbooks`` returns success=False, the SSE stream
    must carry an explicit error event with the merge errors before
    ``run_complete`` so the user can see why their workbook isn't
    downloadable."""
    client, session_id, out = session_env

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
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
                 success=False,
                 errors=["SOFP: failed to open: corrupted xlsx"],
             ),
         ), \
         patch("cross_checks.framework.run_all", return_value=[]), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    body = resp.text

    # Find the merge-error message in some error frame.
    assert "corrupted xlsx" in body, (
        "Merge failure must reach the SSE stream as an error event. "
        f"Body[:600]: {body[:600]!r}"
    )
    # Type discriminator so the frontend can route this distinctly
    # from generic agent errors.
    assert "merge_failed" in body, (
        "Merge-failure SSE error should carry a recognizable type "
        f"discriminator. Body[:600]: {body[:600]!r}"
    )


def test_cross_check_exception_emits_sse_error_and_finalizes(session_env):
    """When ``run_cross_checks`` raises (e.g. corrupt workbook, missing
    sheet), the run must NOT crash silently — emit an error event with
    the exception class + message, treat checks as empty, and finalize
    the run."""
    client, session_id, out = session_env

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
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
         patch(
             "cross_checks.framework.run_all",
             side_effect=ValueError("Sheet 'SOFP-CuNonCu' not found"),
         ), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    body = resp.text

    # The error class + message must reach the SSE stream.
    assert "ValueError" in body and "Sheet 'SOFP-CuNonCu' not found" in body, (
        "Cross-check exceptions must surface the class name + message so "
        f"the user can copy them for a bug report. Body[:800]: {body[:800]!r}"
    )
    # Type discriminator distinguishes this from merge / agent errors.
    assert "cross_check_exception" in body, (
        "Cross-check exception SSE error should carry a recognizable "
        f"type discriminator. Body[:800]: {body[:800]!r}"
    )
    # Run still finalizes — run_complete event must arrive.
    assert "event: run_complete" in body, (
        "A cross-check exception must NOT prevent run_complete from "
        f"firing. Body[:800]: {body[:800]!r}"
    )

    # Peer-review fix (2026-04-27): a crashed cross-check pass MUST flip
    # success=false. Previously the empty results list made
    # any_check_failed=False and the run silently reported success=true.
    rc_token = "event: run_complete\ndata: "
    rc_idx = body.index(rc_token)
    rc_payload_start = rc_idx + len(rc_token)
    rc_payload_end = body.index("\n", rc_payload_start)
    rc_payload = body[rc_payload_start:rc_payload_end]
    assert '"success": false' in rc_payload, (
        f"run_complete after cross_check_exception must report "
        f"success=false. Got: {rc_payload!r}"
    )


def test_post_correction_cross_check_exception_finalizes_with_errors(session_env):
    """Peer-review fix (2026-04-27): the post-correction cross-check
    re-run was previously a bare ``run_cross_checks(...)`` call. If
    the corrected workbook is malformed enough to make a check raise,
    the exception used to propagate to the outer ``except
    BaseException`` and mark the run failed with a generic "Stream
    error" — exactly the failure mode Phase 4 set out to fix.

    This test fires the initial pass with a hard-failed check (so the
    correction agent runs) and a SECOND raise on the post-correction
    re-run. The structured ``cross_check_exception`` event must
    surface with ``phase: "post_correction"`` and the run still
    finalizes via run_complete with success=false.
    """
    client, session_id, out = session_env

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
    ]
    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    # Sequence: first call returns one hard-failed check (triggers
    # correction); second call (post-correction re-run) raises.
    initial_results = [
        CrossCheckResult(
            name="sofp_balance", status="failed",
            expected=100.0, actual=90.0, diff=-10.0, tolerance=1.0,
            message="off by 10",
        ),
    ]
    call_count = {"n": 0}
    def _run_all_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return list(initial_results)
        # Post-correction re-run: simulate a corrupt corrected workbook.
        raise RuntimeError("Workbook corrupted after correction")

    # Stub the correction pass so it returns "did some writes" without
    # actually invoking an LLM — just enough to trigger the re-run.
    async def _fake_correction(*args, **kwargs):
        return {
            "invoked": True, "writes_performed": 1,
            "error": None, "exhausted": False,
            "total_tokens": 0, "total_cost": 0.0,
            "turns_used": 1, "max_turns": 8,
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
         patch(
             "cross_checks.framework.run_all",
             side_effect=_run_all_side_effect,
         ), \
         patch("server._run_correction_pass", side_effect=_fake_correction), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    body = resp.text

    # Post-correction crash must surface with phase discriminator so
    # the Validator tab can label it correctly.
    assert "cross_check_exception" in body, (
        f"post-correction cross-check crash must surface as a typed "
        f"cross_check_exception event. Body[:1000]: {body[:1000]!r}"
    )
    assert "post_correction" in body, (
        f"post-correction crash event must carry phase=post_correction. "
        f"Body[:1000]: {body[:1000]!r}"
    )
    assert "Workbook corrupted after correction" in body, (
        f"post-correction crash message must be preserved verbatim. "
        f"Body[:1000]: {body[:1000]!r}"
    )

    # Run finalizes — does not crash to the outer BaseException path.
    assert "event: run_complete" in body
    rc_token = "event: run_complete\ndata: "
    rc_idx = body.index(rc_token)
    rc_payload_start = rc_idx + len(rc_token)
    rc_payload_end = body.index("\n", rc_payload_start)
    rc_payload = body[rc_payload_start:rc_payload_end]
    assert '"success": false' in rc_payload, (
        f"run_complete after post-correction crash must report "
        f"success=false. Got: {rc_payload!r}"
    )
