"""PLAN-stop-and-validation-visibility Phase 6: pipeline_stage SSE events.

Today the live UI has no signal for which coordinator-level stage is
running. Once extraction agents finish streaming their per-agent events,
there's a 5-30 second silent period during merge + cross-checks +
correction + re-checking + notes validation, and the user has no idea
what's happening.

This test pins the contract: the run emits ``pipeline_stage`` events at
each phase boundary so the frontend can show "Validating notes…" instead
of letting the spinner sit on the last per-agent event.
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
    session_id = "pipeline-stage-session"
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


def test_run_emits_pipeline_stage_at_each_boundary(session_env):
    """A normal multi-agent run emits ``pipeline_stage`` events for the
    extracting → merging → cross_checking → done sequence.
    Correction-stage and notes-validation events fire only when those
    stages run; this test exercises the always-firing core."""
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

    # Required stages for a normal run.
    for stage in ("extracting", "merging", "cross_checking", "done"):
        token = f'"stage": "{stage}"'
        assert token in body, (
            f"Expected pipeline_stage with stage={stage!r} on a normal "
            f"multi-agent run. Body[:600]: {body[:600]!r}"
        )

    # Stages must arrive in order — "extracting" before "merging" before
    # "cross_checking" before "done".
    extracting_idx = body.index('"stage": "extracting"')
    merging_idx = body.index('"stage": "merging"')
    cross_checking_idx = body.index('"stage": "cross_checking"')
    done_idx = body.index('"stage": "done"')
    assert extracting_idx < merging_idx < cross_checking_idx < done_idx, (
        "pipeline_stage events must arrive in chronological order; "
        f"got extracting={extracting_idx}, merging={merging_idx}, "
        f"cross_checking={cross_checking_idx}, done={done_idx}"
    )
