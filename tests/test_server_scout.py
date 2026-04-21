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

        async def fake_streaming(pdf_path, model, on_event=None, *, force_vision_inventory=False):
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

        async def fake_streaming(pdf_path, model, on_event=None, *, force_vision_inventory=False):
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
        async def fake_streaming(pdf_path, model, on_event=None, *, force_vision_inventory=False):
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
