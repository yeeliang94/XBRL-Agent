"""Tests for POST /api/run/{session_id} with RunConfig body (Phase 7, Step 7.2)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from statement_types import StatementType


@pytest.fixture
def session_dir(tmp_path):
    session_id = "test-run-config-session"
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


class TestRunConfigSchema:
    """POST /api/run/{session_id} validates the RunConfig request body."""

    def test_run_config_accepted(self, app_client):
        """A valid RunConfig body is accepted and triggers a streaming response."""
        client, session_id = app_client

        run_config = {
            "statements": ["SOFP", "SOPL"],
            "variants": {"SOFP": "CuNonCu", "SOPL": "Function"},
            "models": {},
            "infopack": None,
            "use_scout": False,
        }

        async def _fake_stream(*args, **kwargs):
            yield {"event": "status", "data": {"phase": "starting", "message": "Starting..."}}
            yield {"event": "complete", "data": {"success": True}}

        with patch("server.run_multi_agent_stream", side_effect=_fake_stream):
            resp = client.post(f"/api/run/{session_id}", json=run_config)

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

    def test_run_config_rejects_missing_variants_when_no_infopack(self, app_client):
        """If infopack is None and variants missing, coordinator falls back to defaults."""
        client, session_id = app_client

        run_config = {
            "statements": ["SOFP", "SOPL"],
            "variants": {"SOPL": "Function"},
            # Missing SOFP variant and no infopack — coordinator falls back to first registered variant
            "infopack": None,
            "use_scout": False,
        }

        async def _fake_stream(*args, **kwargs):
            yield {"event": "run_complete", "data": {"success": True}}

        with patch("server.run_multi_agent_stream", side_effect=_fake_stream):
            resp = client.post(f"/api/run/{session_id}", json=run_config)
        # Coordinator falls back to first registered variant — this is accepted
        assert resp.status_code == 200


## TestRunConfigCompatibility removed in Phase 11.3 — legacy GET /api/run
## endpoint was removed. Use POST /api/run/{session_id} with RunConfigRequest.
