"""Cycle 4: Settings API — GET/POST /api/settings."""
import server
from fastapi.testclient import TestClient
from server import app


client = TestClient(app)


def test_get_settings_default(tmp_path, monkeypatch):
    """Returns defaults when no .env exists."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    # Clear env so defaults apply
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TEST_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "vertex_ai.gemini-3-flash-preview"
    assert data["api_key_set"] is False
    assert "proxy_url" in data


def test_post_settings_writes_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    resp = client.post("/api/settings", json={
        "model": "vertex_ai.gemini-3-flash-preview",
        "api_key": "test-key-123",
        "proxy_url": "https://genai-sharedservice-emea.pwc.com",
    })
    assert resp.status_code == 200
    assert env_file.exists()
    content = env_file.read_text()
    assert "TEST_MODEL" in content
    assert "GOOGLE_API_KEY" in content
    assert "LLM_PROXY_URL" in content


def test_get_settings_shows_masked_key(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GOOGLE_API_KEY=abcdef1234567890abcdef\n"
        "TEST_MODEL=vertex_ai.gemini-3-flash-preview\n"
        "LLM_PROXY_URL=https://proxy.example.com\n"
    )
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    resp = client.get("/api/settings")
    data = resp.json()
    assert data["api_key_set"] is True
    # Key should be partially masked
    assert "..." in data["api_key_preview"]
