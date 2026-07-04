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
    assert data["model"] == "openai.gpt-5.4"
    assert data["api_key_set"] is False
    assert "proxy_url" in data


def test_default_model_is_gpt_5_4_for_every_agent_role(tmp_path, monkeypatch):
    """When TEST_MODEL and XBRL_DEFAULT_MODELS are unset, every agent role
    (scout + 5 statement types) resolves to openai.gpt-5.4.

    Pins the decision that GPT-5.4 is the global default across platforms
    (Mac direct + Windows proxy). If someone reverts the .env / server.py
    default back to a Gemini id, this test catches it before a run goes
    out with the wrong model.
    """
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("TEST_MODEL", raising=False)
    monkeypatch.delenv("XBRL_DEFAULT_MODELS", raising=False)

    from server import _load_extended_settings, _AGENT_ROLES

    defaults = _load_extended_settings()["default_models"]
    for role in _AGENT_ROLES:
        assert defaults[role] == "openai.gpt-5.4", (
            f"Agent role {role!r} defaulted to {defaults[role]!r}, "
            f"expected 'openai.gpt-5.4'."
        )


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


def test_auto_review_toggle_round_trips(tmp_path, monkeypatch):
    """The Settings auto-review toggle persists to XBRL_AUTO_REVIEW and is
    reflected by GET /api/settings + /api/config (docs/Archive/PLAN-reviewer-agent.md)."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("XBRL_AUTO_REVIEW", raising=False)

    # Default is on.
    assert client.get("/api/settings").json()["auto_review"] is True
    assert client.get("/api/config").json()["auto_review"] is True

    # Turn it off → persisted + re-read fresh from the env file.
    resp = client.post("/api/settings", json={"auto_review": False})
    assert resp.status_code == 200
    assert "XBRL_AUTO_REVIEW" in env_file.read_text()
    from dotenv import load_dotenv
    load_dotenv(env_file, override=True)
    assert client.get("/api/settings").json()["auto_review"] is False
    assert server._auto_review_enabled() is False


def test_notes_coverage_toggle_round_trips(tmp_path, monkeypatch):
    """The notes coverage checklist toggle persists to XBRL_NOTES_COVERAGE and
    is reflected by GET /api/settings + /api/config (default ON, suite forces
    OFF — delenv here to verify the true default)."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("XBRL_NOTES_COVERAGE", raising=False)

    assert client.get("/api/settings").json()["notes_coverage"] is True
    assert client.get("/api/config").json()["notes_coverage"] is True

    resp = client.post("/api/settings", json={"notes_coverage": False})
    assert resp.status_code == 200
    assert "XBRL_NOTES_COVERAGE" in env_file.read_text()
    from dotenv import load_dotenv
    load_dotenv(env_file, override=True)
    assert client.get("/api/settings").json()["notes_coverage"] is False
    assert server._notes_coverage_enabled() is False


def test_spot_check_toggle_and_mode_round_trip(tmp_path, monkeypatch):
    """Issue 1: the clean-run spot-check toggle + depth persist to
    XBRL_SPOT_CHECK / XBRL_SPOT_CHECK_MODE and are reflected by GET
    /api/settings + /api/config."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("XBRL_SPOT_CHECK", raising=False)
    monkeypatch.delenv("XBRL_SPOT_CHECK_MODE", raising=False)

    # Defaults: on / light.
    s = client.get("/api/settings").json()
    assert s["spot_check"] is True
    assert s["spot_check_mode"] == "light"
    cfg = client.get("/api/config").json()
    assert cfg["spot_check"] is True and cfg["spot_check_mode"] == "light"

    # Switch to full + off, persisted + re-read fresh.
    resp = client.post("/api/settings", json={"spot_check": False, "spot_check_mode": "full"})
    assert resp.status_code == 200
    from dotenv import load_dotenv
    load_dotenv(env_file, override=True)
    s2 = client.get("/api/settings").json()
    assert s2["spot_check"] is False
    assert s2["spot_check_mode"] == "full"
    assert server._spot_check_enabled() is False
    assert server._spot_check_mode() == "full"


def test_spot_check_mode_rejects_invalid_value(tmp_path, monkeypatch):
    """An unknown spot_check_mode is a 400, not silently coerced server-side."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    resp = client.post("/api/settings", json={"spot_check_mode": "deep"})
    assert resp.status_code == 400


def test_reviewer_model_name_reads_default_models(tmp_path, monkeypatch):
    monkeypatch.delenv("XBRL_DEFAULT_MODELS", raising=False)
    assert server._reviewer_model_name() is None  # unset → inherit run model
    monkeypatch.setenv("XBRL_DEFAULT_MODELS", '{"reviewer": "google.gemini-3"}')
    assert server._reviewer_model_name() == "google.gemini-3"


def test_notes_formatter_model_round_trips(tmp_path, monkeypatch):
    """notes_formatter is a first-class agent role: the settings PUT accepts
    a default model for it and _notes_formatter_model_name reads it back."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("XBRL_DEFAULT_MODELS", raising=False)

    assert "notes_formatter" in server._AGENT_ROLES
    assert server._notes_formatter_model_name() is None  # unset → inherit

    resp = client.post("/api/settings", json={
        "default_models": {"notes_formatter": "openai.gpt-5.4"},
    })
    assert resp.status_code == 200
    from dotenv import load_dotenv
    load_dotenv(env_file, override=True)
    assert server._notes_formatter_model_name() == "openai.gpt-5.4"
    assert (
        server._load_extended_settings()["default_models"]["notes_formatter"]
        == "openai.gpt-5.4"
    )


def test_notes_table_style_round_trips(tmp_path, monkeypatch):
    """The firm notes-table theme persists to .env and reads back via both
    /api/settings and /api/config (docs/PLAN-notes-table-theme.md)."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("XBRL_NOTES_TABLE_STYLE", raising=False)
    from dotenv import load_dotenv

    # Default: empty object (each surface keeps its historic look).
    assert client.get("/api/settings").json()["notes_table_style"] == {}
    assert client.get("/api/config").json()["notes_table_style"] == {}

    resp = client.post("/api/settings", json={
        "notes_table_style": {
            "borderStyle": "single",
            "borderColor": "#185FA5",
            "headerFill": "transparent",
            "fontSizePt": 11,
            "cellPaddingPx": [4, 8],
        },
    })
    assert resp.status_code == 200
    load_dotenv(env_file, override=True)
    style = client.get("/api/settings").json()["notes_table_style"]
    assert style["borderColor"] == "#185fa5"   # lowercased by the validator
    assert style["headerFill"] == "transparent"
    assert style["fontSizePt"] == 11
    # Same value visible on the lightweight /api/config surface.
    assert client.get("/api/config").json()["notes_table_style"]["borderColor"] == "#185fa5"


def test_notes_table_style_rejects_malformed(tmp_path, monkeypatch):
    """Bad colour / enum / range fails loudly (400), never lands in .env."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    for bad in (
        {"borderColor": "red"},            # keyword we don't accept
        {"borderColor": "url(x)"},          # unsafe
        {"borderStyle": "rainbow"},         # not an enum member
        {"fontSizePt": 999},                # out of range
        {"cellPaddingPx": [4]},             # malformed tuple
        "not-an-object",                    # wrong type entirely
    ):
        resp = client.post("/api/settings", json={"notes_table_style": bad})
        assert resp.status_code == 400, bad
