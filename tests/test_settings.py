"""Tests for extended settings endpoints (Phase 8).

Validates that GET /api/settings returns model list + per-agent defaults,
POST /api/settings persists per-agent model overrides and scout toggle,
and available_models is loaded from config/models.json.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path):
    """Create a TestClient with a temporary .env and config dir."""
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_MODEL=vertex_ai.gemini-3-flash-preview\nGOOGLE_API_KEY=sk-test1234\n")

    # Copy real models.json into a temp config dir
    real_config = Path(__file__).resolve().parent.parent / "config" / "models.json"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "models.json").write_text(real_config.read_text())

    with patch("server.ENV_FILE", env_file), \
         patch("server.CONFIG_DIR", config_dir):
        from server import app
        yield TestClient(app)


class TestGetSettings:
    """GET /api/settings returns extended fields."""

    def test_returns_available_models(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "available_models" in data
        models = data["available_models"]
        assert isinstance(models, list)
        assert len(models) >= 7
        # Each model entry has expected fields
        first = models[0]
        assert "id" in first
        assert "display_name" in first
        assert "provider" in first
        assert "supports_vision" in first

    def test_returns_default_models(self, client):
        resp = client.get("/api/settings")
        data = resp.json()
        assert "default_models" in data
        defaults = data["default_models"]
        # Should have keys for scout + each statement type
        assert "scout" in defaults
        assert "SOFP" in defaults
        assert "SOPL" in defaults
        assert "SOCI" in defaults
        assert "SOCF" in defaults
        assert "SOCIE" in defaults

    def test_returns_scout_enabled_default(self, client):
        resp = client.get("/api/settings")
        data = resp.json()
        assert "scout_enabled_default" in data
        assert isinstance(data["scout_enabled_default"], bool)
        # Default should be True
        assert data["scout_enabled_default"] is True

    def test_returns_tolerance(self, client):
        resp = client.get("/api/settings")
        data = resp.json()
        assert "tolerance_rm" in data
        assert isinstance(data["tolerance_rm"], (int, float))
        assert data["tolerance_rm"] == 1.0

    def test_backward_compat_fields_still_present(self, client):
        """Existing fields (model, proxy_url, api_key_set, api_key_preview)
        must still be returned so the existing SettingsModal doesn't break."""
        resp = client.get("/api/settings")
        data = resp.json()
        assert "model" in data
        assert "proxy_url" in data
        assert "api_key_set" in data
        assert "api_key_preview" in data


class TestUpdateSettings:
    """POST /api/settings accepts new extended fields."""

    def test_update_per_agent_model(self, client):
        resp = client.post("/api/settings", json={
            "default_models": {"SOFP": "claude-opus-4-6", "scout": "claude-haiku-4-5"},
        })
        assert resp.status_code == 200

        # Verify it persisted
        resp2 = client.get("/api/settings")
        data = resp2.json()
        assert data["default_models"]["SOFP"] == "claude-opus-4-6"
        assert data["default_models"]["scout"] == "claude-haiku-4-5"

    def test_update_scout_enabled_default(self, client):
        resp = client.post("/api/settings", json={
            "scout_enabled_default": False,
        })
        assert resp.status_code == 200

        resp2 = client.get("/api/settings")
        assert resp2.json()["scout_enabled_default"] is False

    def test_update_tolerance(self, client):
        resp = client.post("/api/settings", json={
            "tolerance_rm": 2.0,
        })
        assert resp.status_code == 200

        resp2 = client.get("/api/settings")
        assert resp2.json()["tolerance_rm"] == 2.0

    def test_backward_compat_model_update(self, client):
        """Updating 'model' (old-style) still works."""
        resp = client.post("/api/settings", json={"model": "gpt-5.4"})
        assert resp.status_code == 200

        resp2 = client.get("/api/settings")
        assert resp2.json()["model"] == "gpt-5.4"

    def test_default_models_rejects_unknown_keys(self, client):
        # Peer-review #2 hardening: accept only known agent roles + notes
        # template values. Unknown keys would otherwise land in .env via
        # set_key and persist across restarts until someone hand-edits the
        # file out.
        resp = client.post("/api/settings", json={
            "default_models": {"totally_made_up_role": "gemini-3-flash"},
        })
        assert resp.status_code == 400
        assert "Unknown default_models key" in resp.json()["detail"]

    def test_default_models_rejects_non_string_values(self, client):
        # Client could currently POST nested structures that json.dumps()
        # happily writes to .env; reject with 400.
        resp = client.post("/api/settings", json={
            "default_models": {"scout": {"nested": [1, 2, 3]}},
        })
        assert resp.status_code == 400
        assert "non-empty string" in resp.json()["detail"]

    def test_default_models_rejects_overlong_string(self, client):
        resp = client.post("/api/settings", json={
            "default_models": {"scout": "x" * 200},
        })
        assert resp.status_code == 400
        assert "too long" in resp.json()["detail"]

    def test_default_models_accepts_all_known_agent_roles(self, client):
        # Guard against over-constraining the validator — every role
        # _load_extended_settings fills must be writable via POST.
        resp = client.post("/api/settings", json={
            "default_models": {
                "scout": "gemini-3-flash",
                "SOFP": "gemini-3-flash",
                "SOPL": "gemini-3-flash",
                "SOCI": "gemini-3-flash",
                "SOCF": "gemini-3-flash",
                "SOCIE": "gemini-3-flash",
                "CORP_INFO": "gemini-3-flash",
                "ACC_POLICIES": "gemini-3-flash",
                "LIST_OF_NOTES": "gemini-3-flash",
                "ISSUED_CAPITAL": "gemini-3-flash",
                "RELATED_PARTY": "gemini-3-flash",
            },
        })
        assert resp.status_code == 200

    def test_scout_model_round_trip_reaches_load_extended_settings(self, client):
        # Pins the three-link chain the inline scout model dropdown (PreRunPanel)
        # relies on: POST writes → env-file reload → both GET /api/settings AND
        # _load_extended_settings (what /api/scout reads at run time) see the
        # new value. Without this guard, a refactor of the settings writer
        # could silently decouple the persisted value from the scout code path.
        from server import _load_extended_settings

        resp = client.post("/api/settings", json={
            "default_models": {"scout": "claude-haiku-4-5"},
        })
        assert resp.status_code == 200

        data = client.get("/api/settings").json()
        assert data["default_models"]["scout"] == "claude-haiku-4-5"

        # The server code that actually builds the scout model reads this
        # helper — not the HTTP GET — so pin both ends are in sync.
        assert _load_extended_settings()["default_models"]["scout"] == "claude-haiku-4-5"


class TestModelsConfig:
    """config/models.json is the source of truth for available models."""

    def test_editing_config_file_changes_available_models(self, client, tmp_path):
        """Reloading picks up changes to the config file without redeploy."""
        config_file = tmp_path / "config" / "models.json"
        models = json.loads(config_file.read_text())
        models.append({
            "id": "test-model-new",
            "display_name": "Test New Model",
            "provider": "test",
            "supports_vision": False,
            "notes": "Added at runtime",
        })
        config_file.write_text(json.dumps(models))

        resp = client.get("/api/settings")
        ids = [m["id"] for m in resp.json()["available_models"]]
        assert "test-model-new" in ids
