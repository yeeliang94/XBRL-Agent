"""Tests for POST /api/scout/{session_id} endpoint (Phase 7, Step 7.1)."""
from __future__ import annotations

import json
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from statement_types import StatementType
from scout.infopack import Infopack, StatementPageRef


@pytest.fixture
def session_dir(tmp_path):
    """Create a fake session directory with an uploaded PDF stub."""
    session_id = "test-scout-session"
    d = tmp_path / "output" / session_id
    d.mkdir(parents=True)
    # Minimal PDF-like stub (just needs to exist for the endpoint guard)
    (d / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    return session_id, d, tmp_path


@pytest.fixture
def app_client(session_dir, monkeypatch):
    """Create a TestClient wired to the real FastAPI app with patched paths."""
    session_id, d, tmp_path = session_dir
    import server
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(server, "AUDIT_DB_PATH", tmp_path / "output" / "xbrl_agent.db")
    # Provide a dummy API key so the endpoint doesn't reject us
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    return TestClient(server.app), session_id


def _fake_infopack() -> Infopack:
    """A plausible infopack for testing."""
    return Infopack(
        toc_page=3,
        page_offset=6,
        statements={
            StatementType.SOFP: StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=42,
                note_pages=[45, 46],
                confidence="HIGH",
            ),
            StatementType.SOPL: StatementPageRef(
                variant_suggestion="Function",
                face_page=44,
                note_pages=[50],
                confidence="MEDIUM",
            ),
        },
    )


class TestScoutEndpoint:
    """POST /api/scout/{session_id} runs scout and returns infopack."""

    def test_scout_endpoint_returns_infopack(self, app_client):
        """Successful scout run returns SSE stream ending with infopack JSON."""
        client, session_id = app_client
        infopack = _fake_infopack()

        with patch("scout.runner.run_scout_streaming", new_callable=AsyncMock, return_value=infopack):
            resp = client.post(f"/api/scout/{session_id}")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        # Parse SSE events — find the 'scout_complete' event with infopack
        events = _parse_sse(resp.text)
        complete_events = [e for e in events if e["event"] == "scout_complete"]
        assert len(complete_events) == 1

        data = complete_events[0]["data"]
        assert data["success"] is True
        assert "infopack" in data
        # Verify infopack shape
        ip = data["infopack"]
        assert ip["toc_page"] == 3
        assert "SOFP" in ip["statements"]
        assert ip["statements"]["SOFP"]["face_page"] == 42

    def test_scout_degraded_timeout_reports_failure_not_success(self, app_client):
        """Codex review: a scout that timed out returns a degraded pack. The
        route must report `scout_complete success:false degraded:true` — never
        a `success:true` completion that contradicts the `scout_timeout` error
        the agent already emitted. The run can still proceed (gotcha #13)."""
        client, session_id = app_client
        degraded = _fake_infopack()
        degraded.degraded = True
        degraded.degraded_reason = "Scout stalled past the 90s per-turn timeout."

        with patch(
            "scout.runner.run_scout_streaming",
            new_callable=AsyncMock,
            return_value=degraded,
        ):
            resp = client.post(f"/api/scout/{session_id}")

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        complete = [e for e in events if e["event"] == "scout_complete"]
        assert len(complete) == 1
        data = complete[0]["data"]
        assert data["success"] is False
        assert data["degraded"] is True
        assert "per-turn timeout" in data["message"]
        # The pack is still forwarded so the run can proceed without hints.
        assert "infopack" in data

    def test_scout_endpoint_404_no_pdf(self, app_client):
        """Scout endpoint returns 404 if no PDF uploaded."""
        client, session_id = app_client
        resp = client.post(f"/api/scout/nonexistent-session")
        assert resp.status_code == 404

    def test_scout_uses_per_agent_model_setting(self, app_client, tmp_path, monkeypatch):
        """Scout resolves its model from default_models.scout, not just TEST_MODEL."""
        client, session_id = app_client
        infopack = _fake_infopack()

        # The scout endpoint calls `load_dotenv(ENV_FILE, override=True)` on
        # every invocation, which would clobber `monkeypatch.setenv` with the
        # real .env. Point ENV_FILE at a tmp file so the override is honoured.
        env_file = tmp_path / ".env"
        env_file.write_text(
            'TEST_MODEL=test-model\n'
            'GOOGLE_API_KEY=test-key-12345\n'
            'XBRL_DEFAULT_MODELS={"scout":"custom-scout-model"}\n'
        )
        import server
        monkeypatch.setattr(server, "ENV_FILE", env_file)

        captured_model_name = {}
        original_create = server._create_proxy_model

        def spy_create(model_name, proxy_url, api_key):
            captured_model_name["value"] = model_name
            return original_create(model_name, proxy_url, api_key)

        monkeypatch.setattr(server, "_create_proxy_model", spy_create)

        with patch("scout.runner.run_scout_streaming", new_callable=AsyncMock, return_value=infopack):
            resp = client.post(f"/api/scout/{session_id}")

        assert resp.status_code == 200
        # The scout should have used the per-agent model, not TEST_MODEL
        assert captured_model_name.get("value") == "custom-scout-model"

    def test_scout_reads_persisted_scout_model_fresh_each_call(
        self, app_client, tmp_path, monkeypatch,
    ):
        # Pins the "inline scout model dropdown writes to /api/settings and
        # the next Auto-detect picks it up without a restart" contract
        # (PLAN-ui-visibility-improvements Step 1.2). The scout endpoint
        # calls `load_dotenv(ENV_FILE, override=True)` on every invocation;
        # this test proves that mutation of the env file between two calls
        # flips the model the scout is built with.
        client, session_id = app_client
        infopack = _fake_infopack()

        # Point server.ENV_FILE at a writable tmp file. Required because the
        # real .env would otherwise be mutated by load_dotenv's override.
        env_file = tmp_path / ".env"
        env_file.write_text(
            'TEST_MODEL=test-model\n'
            'GOOGLE_API_KEY=test-key-12345\n'
            'XBRL_DEFAULT_MODELS={"scout":"first-model"}\n'
        )
        import server
        monkeypatch.setattr(server, "ENV_FILE", env_file)

        captured: list[str] = []
        original_create = server._create_proxy_model

        def spy_create(model_name, proxy_url, api_key):
            captured.append(model_name)
            return original_create(model_name, proxy_url, api_key)

        monkeypatch.setattr(server, "_create_proxy_model", spy_create)

        with patch(
            "scout.runner.run_scout_streaming",
            new_callable=AsyncMock,
            return_value=infopack,
        ):
            resp1 = client.post(f"/api/scout/{session_id}")
            assert resp1.status_code == 200
            assert captured, "scout never constructed a model on first call"
            assert captured[-1] == "first-model"

            # Simulate the inline dropdown's POST /api/settings: overwrite
            # the .env file with a new scout value. The next scout call must
            # see it without a server restart.
            env_file.write_text(
                'TEST_MODEL=test-model\n'
                'GOOGLE_API_KEY=test-key-12345\n'
                'XBRL_DEFAULT_MODELS={"scout":"second-model"}\n'
            )

            resp2 = client.post(f"/api/scout/{session_id}")
            assert resp2.status_code == 200
            assert captured[-1] == "second-model", (
                f"scout did not re-read XBRL_DEFAULT_MODELS.scout; captured={captured}"
            )

    def test_scout_scanned_pdf_flag_forwards_force_vision(self, app_client):
        """POST body {"scanned_pdf": true} must forward force_vision_inventory=True
        to run_scout_streaming so the discoverer skips the regex fast path."""
        client, session_id = app_client
        infopack = _fake_infopack()

        captured: dict = {}

        async def fake_streaming(pdf_path, model, on_event=None, *, force_vision_inventory=False, **_kwargs):
            captured["force_vision_inventory"] = force_vision_inventory
            return infopack

        with patch("scout.runner.run_scout_streaming", side_effect=fake_streaming):
            resp = client.post(
                f"/api/scout/{session_id}",
                json={"scanned_pdf": True},
            )

        assert resp.status_code == 200
        assert captured.get("force_vision_inventory") is True

    def test_scout_default_body_leaves_force_vision_off(self, app_client):
        """No body → force_vision_inventory stays False (today's behaviour)."""
        client, session_id = app_client
        infopack = _fake_infopack()

        captured: dict = {}

        async def fake_streaming(pdf_path, model, on_event=None, *, force_vision_inventory=False, **_kwargs):
            captured["force_vision_inventory"] = force_vision_inventory
            return infopack

        with patch("scout.runner.run_scout_streaming", side_effect=fake_streaming):
            resp = client.post(f"/api/scout/{session_id}")

        assert resp.status_code == 200
        assert captured.get("force_vision_inventory") is False

    def test_scout_endpoint_error_handling(self, app_client):
        """Scout failure emits an error event, not a crash."""
        client, session_id = app_client

        with patch("scout.runner.run_scout_streaming", new_callable=AsyncMock, side_effect=RuntimeError("LLM timeout")):
            resp = client.post(f"/api/scout/{session_id}")

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) >= 1
        assert "LLM timeout" in error_events[0]["data"]["message"]


    def test_scout_task_registered_in_task_registry(self, app_client):
        """Scout task should be registered so abort endpoints can cancel it."""
        client, session_id = app_client
        import task_registry

        register_calls = []
        original_register = task_registry.register

        def spy_register(sid, aid, task):
            register_calls.append((sid, aid))
            return original_register(sid, aid, task)

        with patch.object(task_registry, "register", side_effect=spy_register):
            infopack = _fake_infopack()
            with patch("scout.runner.run_scout_streaming", new_callable=AsyncMock, return_value=infopack):
                resp = client.post(f"/api/scout/{session_id}")

        assert resp.status_code == 200
        # Scout task MUST be registered with agent_id "scout"
        assert len(register_calls) >= 1, "task_registry.register was never called for scout"
        session_ids = [s for s, a in register_calls]
        agent_ids = [a for s, a in register_calls]
        assert session_id in session_ids, f"Scout not registered for session {session_id}"
        assert "scout" in agent_ids, f"Scout not registered with agent_id 'scout', got: {agent_ids}"

    def test_scout_emits_structured_tool_events(self, app_client):
        """Scout SSE stream should contain tool_call and tool_result events, not just text status."""
        client, session_id = app_client
        infopack = _fake_infopack()

        # Mock run_scout_streaming to yield structured events
        async def fake_streaming(pdf_path, model, on_event=None, *, force_vision_inventory=False, **_kwargs):
            if on_event:
                await on_event("tool_call", {
                    "tool_name": "find_toc",
                    "tool_call_id": "tc_1",
                    "args": {},
                })
                await on_event("tool_result", {
                    "tool_name": "find_toc",
                    "tool_call_id": "tc_1",
                    "result_summary": "Found TOC on page 3",
                    "duration_ms": 150,
                })
            return infopack

        with patch("scout.runner.run_scout_streaming", new_callable=AsyncMock, side_effect=fake_streaming):
            resp = client.post(f"/api/scout/{session_id}")

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        event_types = [e["event"] for e in events]

        # Must contain structured tool events, not just "status" text
        assert "tool_call" in event_types, f"No tool_call events in: {event_types}"
        assert "tool_result" in event_types, f"No tool_result events in: {event_types}"

        # Verify tool_call shape
        tc = next(e for e in events if e["event"] == "tool_call")
        assert tc["data"]["tool_name"] == "find_toc"
        assert tc["data"]["tool_call_id"] == "tc_1"

        # Verify tool_result shape
        tr = next(e for e in events if e["event"] == "tool_result")
        assert tr["data"]["tool_name"] == "find_toc"
        assert tr["data"]["duration_ms"] == 150

    def test_scout_cancellation_emits_event(self, app_client):
        """When scout task is cancelled, it should emit a cancellation SSE event."""
        client, session_id = app_client

        async def cancelled_scout(**kwargs):
            raise asyncio.CancelledError()

        with patch("scout.runner.run_scout_streaming", new_callable=AsyncMock, side_effect=cancelled_scout):
            resp = client.post(f"/api/scout/{session_id}")

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        # Should have a status event indicating cancellation, not an unhandled error
        event_types = [e["event"] for e in events]
        has_cancel = any(
            e["event"] == "scout_cancelled" or
            (e["event"] == "status" and "cancel" in str(e["data"]).lower())
            for e in events
        )
        assert has_cancel, f"No cancellation event found in: {event_types}"


class TestScoutTraceRoute:
    """Item 2 (PLAN-orchestration-hardening): after a scout run, the SCOUT
    trace is reachable through the EXISTING per-agent trace route — proving
    the run_agents whitelist gate was actually opened (a SCOUT row exists),
    not just that the file landed on disk."""

    def test_scout_trace_route_returns_200_after_scout_run(
        self, app_client, session_dir,
    ):
        client, session_id = app_client
        _sid, d, tmp_path = session_dir

        # Seed the audit DB the way an upload would: schema + a draft runs
        # row carrying this session's id and output dir.
        import sqlite3
        import server
        from db import repository as repo
        from db.schema import init_db
        init_db(server.AUDIT_DB_PATH)
        conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
        try:
            run_id = repo.create_run(
                conn, pdf_filename="f.pdf", session_id=session_id,
                output_dir=str(d), config=None, scout_enabled=True,
                status="draft",
            )
            conn.commit()
        finally:
            conn.close()

        captured: dict = {}

        async def fake_streaming(pdf_path, model, on_event=None, *,
                                 force_vision_inventory=False,
                                 output_dir=None, **_kwargs):
            captured["output_dir"] = output_dir
            # Simulate item 2's trace persistence inside the scout.
            if output_dir:
                (Path(output_dir) / "SCOUT_conversation_trace.json").write_text(
                    json.dumps({"messages": []}), encoding="utf-8",
                )
            return _fake_infopack()

        with patch("scout.runner.run_scout_streaming", side_effect=fake_streaming):
            resp = client.post(f"/api/scout/{session_id}")
        assert resp.status_code == 200

        # The endpoint threaded the session dir in as the trace destination.
        assert captured.get("output_dir") == str(d)

        # The SCOUT run_agents row exists and is terminal.
        conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
        try:
            row = conn.execute(
                "SELECT statement_type, status FROM run_agents "
                "WHERE run_id = ?", (run_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None and row[0] == "SCOUT"
        assert row[1] == "succeeded"

        # And the trace route serves it — whitelist gate open.
        trace_resp = client.get(f"/api/runs/{run_id}/agents/SCOUT/trace")
        assert trace_resp.status_code == 200, trace_resp.text
        assert "messages" in trace_resp.json()


def _parse_sse(text: str) -> list[dict]:
    """Parse SSE text into list of {event, data} dicts."""
    events = []
    current_event = None
    current_data = None
    for line in text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            current_data = line[6:].strip()
        elif line == "" and current_event and current_data:
            try:
                data = json.loads(current_data)
            except json.JSONDecodeError:
                data = current_data
            events.append({"event": current_event, "data": data})
            current_event = None
            current_data = None
    return events


# --- Scout attempt generation (peer-review HIGH, 2026-06-21) ------------------

def test_scout_attempt_generation_supersedes_and_gates_ownership():
    """A re-scout claims a newer generation, so the OLD attempt's finally sees
    it's no longer current and skips finalize/unregister — preventing it from
    clobbering the reused SCOUT row or unregistering the new attempt's task."""
    import api.uploads as up

    sid = "race-session"
    # Isolate the module-level counter for this session.
    up._scout_attempt_gen.pop(sid, None)

    a1 = up._claim_scout_attempt(sid)
    assert up._scout_attempt_is_current(sid, a1) is True

    # A second scout takes over.
    a2 = up._claim_scout_attempt(sid)
    assert a2 != a1
    # The OLD attempt is no longer current → its finally will skip cleanup.
    assert up._scout_attempt_is_current(sid, a1) is False
    # The NEW attempt owns the row + "scout" task slot.
    assert up._scout_attempt_is_current(sid, a2) is True

    # A lone attempt (no successor) stays current and cleans up normally.
    sid2 = "solo-session"
    up._scout_attempt_gen.pop(sid2, None)
    solo = up._claim_scout_attempt(sid2)
    assert up._scout_attempt_is_current(sid2, solo) is True
