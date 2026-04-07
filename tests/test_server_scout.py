"""Tests for POST /api/scout/{session_id} endpoint (Phase 7, Step 7.1)."""
from __future__ import annotations

import json
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock

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

        with patch("scout.runner.run_scout", new_callable=AsyncMock, return_value=infopack):
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

    def test_scout_uses_per_agent_model_setting(self, app_client, monkeypatch):
        """Scout resolves its model from default_models.scout, not just TEST_MODEL."""
        client, session_id = app_client
        infopack = _fake_infopack()

        # Set a scout-specific model override via env
        monkeypatch.setenv("XBRL_DEFAULT_MODELS", json.dumps({"scout": "custom-scout-model"}))

        captured_model_name = {}

        # Intercept _create_proxy_model to capture the model name it receives
        original_create = None
        import server
        original_create = server._create_proxy_model

        def spy_create(model_name, proxy_url, api_key):
            captured_model_name["value"] = model_name
            return original_create(model_name, proxy_url, api_key)

        monkeypatch.setattr(server, "_create_proxy_model", spy_create)

        with patch("scout.runner.run_scout", new_callable=AsyncMock, return_value=infopack):
            resp = client.post(f"/api/scout/{session_id}")

        assert resp.status_code == 200
        # The scout should have used the per-agent model, not TEST_MODEL
        assert captured_model_name.get("value") == "custom-scout-model"

    def test_scout_endpoint_error_handling(self, app_client):
        """Scout failure emits an error event, not a crash."""
        client, session_id = app_client

        with patch("scout.runner.run_scout", new_callable=AsyncMock, side_effect=RuntimeError("LLM timeout")):
            resp = client.post(f"/api/scout/{session_id}")

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) >= 1
        assert "LLM timeout" in error_events[0]["data"]["message"]


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
