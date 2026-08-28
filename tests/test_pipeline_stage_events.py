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

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from coordinator import AgentResult, CoordinatorResult
from cross_checks.framework import CrossCheckResult
from scout.infopack import Infopack, StatementPageRef
from scout.notes_discoverer import NoteInventoryEntry
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
         patch("cross_checks.framework.run_all", return_value=fake_results), patch("cross_checks.framework.run_all_facts", return_value=fake_results), \
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

    # The same coordinator events must survive a reload through the durable
    # run timeline.  This live-pipeline assertion catches an allowlist-order
    # bug that repository-only round-trip tests cannot see.
    conn = sqlite3.connect(out / "xbrl_agent.db")
    try:
        event_types = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM run_events ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()
    assert event_types.count("pipeline_stage") >= 4
    assert "cross_check_start" in event_types
    assert "cross_check_complete" in event_types
    assert "run_complete" in event_types


def test_normal_run_scouts_before_extraction_and_persists_fresh_infopack(session_env):
    """The primary Run action owns a fresh scout pass.

    A prior preview is optional and cannot be the only source of page hints:
    the coordinator receives the infopack produced immediately before it.
    """
    client, session_id, out = session_env
    received: dict[str, object] = {}
    fresh_infopack = Infopack(
        toc_page=2,
        page_offset=4,
        statements={
            StatementType.SOFP: StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=8,
            ),
        },
        entity_name="Fresh Scout Sdn. Bhd.",
        notes_inventory=[
            NoteInventoryEntry(1, "Existing note", (10, 11)),
            NoteInventoryEntry(2, "Remove me", (12, 12)),
        ],
    )
    scout_args: dict[str, object] = {}

    async def fake_scout(*, on_event=None, usage_out=None, **kwargs):
        scout_args.update(kwargs)
        if usage_out is not None:
            usage_out.update({"prompt_tokens": 10, "completion_tokens": 5})
        if on_event is not None:
            await on_event("status", {
                "phase": "scouting",
                "message": "Scanning the uploaded document",
            })
        return fresh_infopack

    async def fake_extract(config, infopack=None, event_queue=None, **_kwargs):
        received["infopack"] = infopack
        result = AgentResult(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        )
        if event_queue is not None:
            await event_queue.put({
                "event": "complete",
                "data": {
                    "success": True,
                    "agent_id": "sofp",
                    "agent_role": "SOFP",
                    "workbook_path": result.workbook_path,
                },
            })
            await event_queue.put(None)
        return CoordinatorResult(agent_results=[result])

    fake_results = [
        CrossCheckResult(name="check_a", status="passed", message="ok"),
    ]
    run_config = {
        "statements": ["SOFP"],
        "variants": {},
        "models": {},
        "infopack": None,
        "use_scout": True,
        "scanned_pdf": True,
        "notes_inventory_overrides": {
            "added": [{
                "note_num": 3,
                "title": "Operator-added note",
                "page_range": [0, 0],
            }],
            "removed_note_nums": [2],
        },
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("scout.runner.run_scout_streaming", side_effect=fake_scout), \
         patch("coordinator.run_extraction", side_effect=fake_extract), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(
                 success=True,
                 output_path=str(out / session_id / "filled.xlsx"),
                 sheets_copied=1,
             ),
         ), \
         patch("cross_checks.framework.run_all", return_value=fake_results), \
         patch("cross_checks.framework.run_all_facts", return_value=fake_results), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    assert received["infopack"] is fresh_infopack
    assert scout_args["force_vision_inventory"] is True
    assert [entry.note_num for entry in fresh_infopack.notes_inventory] == [1, 3]
    body = resp.text
    assert body.index('"stage": "scouting"') < body.index('"stage": "extracting"')
    assert "event: scout_complete" in body

    conn = sqlite3.connect(out / "xbrl_agent.db")
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute(
            "SELECT id, run_config_json FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run is not None
        stored = __import__("json").loads(run["run_config_json"])
        assert stored["infopack"]["entity_name"] == "Fresh Scout Sdn. Bhd."
        scout_row = conn.execute(
            "SELECT status FROM run_agents WHERE run_id = ? AND statement_type = 'SCOUT'",
            (run["id"],),
        ).fetchone()
        assert scout_row is not None
        assert scout_row["status"] == "succeeded"
    finally:
        conn.close()


def test_integrated_scout_failure_continues_and_closes_audit_row(session_env):
    client, session_id, out = session_env
    extraction_called = False

    async def failed_scout(**_kwargs):
        raise RuntimeError("provider unavailable")

    async def fake_extract(config, infopack=None, event_queue=None, **_kwargs):
        nonlocal extraction_called
        extraction_called = True
        result = AgentResult(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        )
        if event_queue is not None:
            await event_queue.put({
                "event": "complete",
                "data": {
                    "success": True,
                    "agent_id": "sofp",
                    "agent_role": "SOFP",
                    "workbook_path": result.workbook_path,
                },
            })
            await event_queue.put(None)
        return CoordinatorResult(agent_results=[result])

    passing = [CrossCheckResult(name="check_a", status="passed", message="ok")]
    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("scout.runner.run_scout_streaming", side_effect=failed_scout), \
         patch("coordinator.run_extraction", side_effect=fake_extract), \
         patch("workbook_merger.merge", return_value=MergeResult(
             success=True,
             output_path=str(out / session_id / "filled.xlsx"),
             sheets_copied=1,
         )), \
         patch("cross_checks.framework.run_all", return_value=passing), \
         patch("cross_checks.framework.run_all_facts", return_value=passing), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        response = client.post(f"/api/run/{session_id}", json={
            "statements": ["SOFP"],
            "variants": {"SOFP": "CuNonCu"},
            "use_scout": True,
        })

    assert response.status_code == 200
    assert extraction_called is True
    assert '"type": "scout_failed"' in response.text
    assert '"agent_role": "SCOUT"' in response.text
    conn = sqlite3.connect(out / "xbrl_agent.db")
    try:
        row = conn.execute(
            "SELECT status, ended_at FROM run_agents WHERE statement_type='SCOUT'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "failed"
    assert row[1] is not None


def test_integrated_scout_cancel_aborts_run_and_closes_audit_row(session_env):
    client, session_id, out = session_env

    async def cancelled_scout(**_kwargs):
        raise asyncio.CancelledError()

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("scout.runner.run_scout_streaming", side_effect=cancelled_scout):
        response = client.post(f"/api/run/{session_id}", json={
            "statements": ["SOFP"],
            "variants": {"SOFP": "CuNonCu"},
            "use_scout": True,
        })

    assert response.status_code == 200
    assert "Run cancelled" in response.text
    conn = sqlite3.connect(out / "xbrl_agent.db")
    try:
        run = conn.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        scout = conn.execute(
            "SELECT status, ended_at FROM run_agents WHERE statement_type='SCOUT'"
        ).fetchone()
    finally:
        conn.close()
    assert run == ("aborted",)
    assert scout is not None
    assert scout[0] == "cancelled"
    assert scout[1] is not None
