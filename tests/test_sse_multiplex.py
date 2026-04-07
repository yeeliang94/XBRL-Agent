"""Tests for multi-agent SSE multiplexing (Phase 7, Step 7.3)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from statement_types import StatementType


@pytest.fixture
def session_dir(tmp_path):
    session_id = "test-multiplex-session"
    d = tmp_path / "output" / session_id
    d.mkdir(parents=True)
    (d / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    return session_id, d, tmp_path


@pytest.fixture
def app_client(session_dir, monkeypatch):
    session_id, d, tmp_path = session_dir
    import server
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(server, "AUDIT_DB_PATH", tmp_path / "output" / "xbrl_agent.db")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    return TestClient(server.app), session_id


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


class TestEventsTaggedByAgent:
    """Each SSE event from multi-agent runs includes agent_id and agent_role."""

    def test_events_tagged_by_agent(self, app_client):
        """Events from multi-agent stream should have agent_id and agent_role fields."""
        client, session_id = app_client

        run_config = {
            "statements": ["SOFP", "SOPL"],
            "variants": {"SOFP": "CuNonCu", "SOPL": "Function"},
            "models": {},
            "infopack": None,
            "use_scout": False,
        }

        with patch("server.run_multi_agent_stream") as mock_stream:
            async def _fake_stream(*args, **kwargs):
                # Simulate events from two agents
                yield {"event": "status", "data": {
                    "phase": "reading_template", "message": "Reading...",
                    "agent_id": "sofp_0", "agent_role": "SOFP",
                }}
                yield {"event": "tool_call", "data": {
                    "tool_name": "read_template", "args": {},
                    "agent_id": "sofp_0", "agent_role": "SOFP",
                }}
                yield {"event": "status", "data": {
                    "phase": "reading_template", "message": "Reading...",
                    "agent_id": "sopl_1", "agent_role": "SOPL",
                }}
                yield {"event": "complete", "data": {
                    "success": True,
                    "agent_id": "sofp_0", "agent_role": "SOFP",
                }}
                yield {"event": "complete", "data": {
                    "success": True,
                    "agent_id": "sopl_1", "agent_role": "SOPL",
                }}
                yield {"event": "run_complete", "data": {
                    "success": True,
                    "merged_workbook": "filled.xlsx",
                }}
            mock_stream.return_value = _fake_stream()

            resp = client.post(f"/api/run/{session_id}", json=run_config)

        assert resp.status_code == 200
        events = _parse_sse(resp.text)

        # All per-agent events should have agent_id and agent_role
        agent_events = [e for e in events if e["event"] not in ("run_complete",)]
        for evt in agent_events:
            data = evt["data"]
            assert "agent_id" in data, f"Missing agent_id in {evt}"
            assert "agent_role" in data, f"Missing agent_role in {evt}"

        # Verify we see events from both agents
        agent_ids = {e["data"]["agent_id"] for e in agent_events}
        assert len(agent_ids) == 2

        agent_roles = {e["data"]["agent_role"] for e in agent_events}
        assert "SOFP" in agent_roles
        assert "SOPL" in agent_roles

    def test_run_complete_event_emitted(self, app_client):
        """A final run_complete event is emitted after all agents finish."""
        client, session_id = app_client

        run_config = {
            "statements": ["SOFP"],
            "variants": {"SOFP": "CuNonCu"},
            "models": {},
            "infopack": None,
            "use_scout": False,
        }

        with patch("server.run_multi_agent_stream") as mock_stream:
            async def _fake_stream(*args, **kwargs):
                yield {"event": "complete", "data": {
                    "success": True, "agent_id": "sofp_0", "agent_role": "SOFP",
                }}
                yield {"event": "run_complete", "data": {
                    "success": True,
                    "merged_workbook": "filled.xlsx",
                    "cross_checks": [],
                }}
            mock_stream.return_value = _fake_stream()

            resp = client.post(f"/api/run/{session_id}", json=run_config)

        events = _parse_sse(resp.text)
        run_completes = [e for e in events if e["event"] == "run_complete"]
        assert len(run_completes) == 1
        assert run_completes[0]["data"]["success"] is True
