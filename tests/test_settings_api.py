"""Cycle 4: Settings API — GET/POST /api/settings."""
import pytest

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


def test_pdf_notes_auto_format_toggle_round_trips(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("XBRL_PDF_NOTES_AUTO_FORMAT", raising=False)

    assert client.get("/api/settings").json()["pdf_notes_auto_format"] is False
    assert client.get("/api/config").json()["pdf_notes_auto_format"] is False

    resp = client.post("/api/settings", json={"pdf_notes_auto_format": True})
    assert resp.status_code == 200
    assert "XBRL_PDF_NOTES_AUTO_FORMAT" in env_file.read_text()
    from dotenv import load_dotenv
    load_dotenv(env_file, override=True)
    assert server._pdf_notes_auto_format_enabled() is True


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


def test_notes_source_integrity_round_trips(tmp_path, monkeypatch):
    """Gotcha #31's rollout mode is operator-settable from Settings rather than
    .env-only. All three values are offered — the operator asked for the full
    ladder, and `shadow` is useless without `enforce` to graduate to."""
    from notes.source_models import IntegrityMode

    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("XBRL_NOTES_SOURCE_INTEGRITY", raising=False)

    s = client.get("/api/settings").json()
    assert s["notes_source_integrity"] == "off"  # shipped default
    # The vocabulary is served, so a new mode needs no frontend edit.
    assert s["notes_source_integrity_choices"] == ["off", "shadow", "enforce"]

    from dotenv import load_dotenv
    for mode in ("shadow", "enforce", "off"):
        assert client.post(
            "/api/settings", json={"notes_source_integrity": mode},
        ).status_code == 200
        load_dotenv(env_file, override=True)
        assert client.get("/api/settings").json()["notes_source_integrity"] == mode
        # The run path reads the same value the form just wrote.
        assert server._notes_integrity_mode() is IntegrityMode(mode)


def test_notes_source_integrity_rejects_invalid_value(tmp_path, monkeypatch):
    """`integrity_mode()` fails CLOSED to `off` on an unrecognised value, so an
    unvalidated write would look saved in the form and silently do nothing on
    the next run. The 400 is what makes the setting honest."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    resp = client.post("/api/settings", json={"notes_source_integrity": "on"})
    assert resp.status_code == 400
    assert "off, shadow, enforce" in resp.json()["detail"]


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

    # Default: the shipped firm house style (2026-07-20) — accountant "ruled",
    # not the historic boxed grid. Both endpoints must serve the SAME resolved
    # theme, or the editor preview and the clipboard paste disagree.
    assert (client.get("/api/settings").json()["notes_table_style"]
            == server.HOUSE_NOTES_TABLE_STYLE)
    assert (client.get("/api/config").json()["notes_table_style"]
            == server.HOUSE_NOTES_TABLE_STYLE)

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


def test_notes_table_style_prose_fields_round_trip(tmp_path, monkeypatch):
    """The prose theme fields (house style item 1) persist and read back."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("XBRL_NOTES_TABLE_STYLE", raising=False)
    from dotenv import load_dotenv

    resp = client.post("/api/settings", json={
        "notes_table_style": {
            "headingSizePt": 13,
            "headingWeight": 700,
            "listMarker": "dash",
            "totalsDoubleUnderline": True,
        },
    })
    assert resp.status_code == 200
    load_dotenv(env_file, override=True)
    style = client.get("/api/settings").json()["notes_table_style"]
    assert style["headingSizePt"] == 13
    assert style["headingWeight"] == 700
    assert style["listMarker"] == "dash"
    assert style["totalsDoubleUnderline"] is True


def test_notes_table_style_prose_fields_reject_malformed(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    for bad in (
        {"headingSizePt": 99},              # out of range
        {"headingWeight": 150},             # below 400
        {"listMarker": "wingdings"},        # not an enum member
        {"totalsDoubleUnderline": "yes"},   # not a boolean
    ):
        resp = client.post("/api/settings", json={"notes_table_style": bad})
        assert resp.status_code == 400, bad


# --- Shipped firm house style (2026-07-20) ----------------------------------

def test_house_notes_table_style_is_accountant_ruled(monkeypatch):
    """The look the product owner chose: ruled, not boxed; headers bold and
    unfilled; totals underlines stay MANUAL (auto-detect invented rules on rows
    that merely contained the word "total")."""
    import server
    monkeypatch.delenv("XBRL_NOTES_TABLE_STYLE", raising=False)
    style = server._notes_table_style()
    assert style["borderStyle"] == "none"
    assert style["headerRule"] is True
    assert style["headerBold"] is True
    assert style["headerFill"] == "transparent"
    assert style["totalsDoubleUnderline"] is False
    # Density unchanged — the only values proven in mTool's TX27 popup.
    assert style["fontSizePt"] == 10
    assert style["cellPaddingPx"] == [4, 8]


def test_operator_can_still_opt_out_to_the_historic_look(monkeypatch):
    """An explicit `{}` means "each surface's historic default" — the escape
    hatch, so the house style is a preference and not a hard-coded look."""
    import server
    monkeypatch.setenv("XBRL_NOTES_TABLE_STYLE", "{}")
    assert server._notes_table_style() == {}


def test_malformed_house_style_degrades_to_the_house_default(monkeypatch):
    import server
    monkeypatch.setenv("XBRL_NOTES_TABLE_STYLE", "not json{")
    assert server._notes_table_style()["headerRule"] is True


def test_house_style_callers_cannot_mutate_the_shared_constant(monkeypatch):
    import server
    monkeypatch.delenv("XBRL_NOTES_TABLE_STYLE", raising=False)
    server._notes_table_style()["borderStyle"] = "double"
    assert server.HOUSE_NOTES_TABLE_STYLE["borderStyle"] == "none"



# --------------------------------------------------------------------------
# Per-role thinking level (reasoning effort)
#
# Never set before this: every agent ran at its provider default. The tests
# below cover the two directions that matter — a level can be chosen, and a
# role can be put BACK to the provider default. Without the second, the first
# choice would be permanent.
# --------------------------------------------------------------------------

def _env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    # config_routes reads server.ENV_FILE at call time, so patching the one
    # attribute is enough.
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("XBRL_THINKING_LEVELS", raising=False)
    return env_file


def test_settings_exposes_thinking_levels_and_its_choices(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    body = client.get("/api/settings").json()
    assert body["thinking_levels"] == {}
    # `none` leads: GPT-5.6 function tools on Chat Completions require
    # effective reasoning `none`, and omitting the field selects `medium`
    # instead — so "off" has to be selectable (peer review, 2026-08-01).
    assert body["thinking_level_choices"] == [
        "none", "minimal", "low", "medium", "high", "xhigh", "max",
    ]


def test_a_level_can_be_saved_and_read_back(tmp_path, monkeypatch):
    env_file = _env(tmp_path, monkeypatch)
    r = client.post("/api/settings", json={"thinking_levels": {"SOFP": "high"}})
    assert r.status_code == 200
    assert '"SOFP": "high"' in env_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("level", ["xhigh", "max"])
def test_gpt56_levels_can_be_saved(tmp_path, monkeypatch, level):
    """The API accepts every level it advertises for a GPT-5.6 role."""
    env_file = _env(tmp_path, monkeypatch)
    r = client.post(
        "/api/settings", json={"thinking_levels": {"SOFP": level}},
    )
    assert r.status_code == 200, r.json()
    assert f'"SOFP": "{level}"' in env_file.read_text(encoding="utf-8")


def test_invalid_thinking_level_does_not_partially_save_model(
    tmp_path, monkeypatch,
):
    """Validate the whole request before mutating the shared env file."""
    env_file = _env(tmp_path, monkeypatch)
    env_file.write_text("TEST_MODEL=openai.gpt-5.4\n", encoding="utf-8")

    r = client.post(
        "/api/settings",
        json={
            "model": "openai.gpt-5.6",
            "thinking_levels": {"SOFP": "extreme"},
        },
    )

    assert r.status_code == 400
    written = env_file.read_text(encoding="utf-8")
    assert "TEST_MODEL=openai.gpt-5.4" in written
    assert "openai.gpt-5.6" not in written


def test_clearing_a_role_returns_it_to_the_provider_default(tmp_path, monkeypatch):
    """Without this there is no way back to "send nothing" once a level is
    set — a merge-only update would make the first choice permanent."""
    env_file = _env(tmp_path, monkeypatch)
    client.post("/api/settings", json={"thinking_levels": {"SOFP": "high"}})
    client.post("/api/settings", json={"thinking_levels": {"SOFP": ""}})
    assert "SOFP" not in env_file.read_text(encoding="utf-8")


def test_other_roles_survive_an_update_to_one(tmp_path, monkeypatch):
    env_file = _env(tmp_path, monkeypatch)
    client.post("/api/settings", json={"thinking_levels": {"SOFP": "high"}})
    client.post("/api/settings", json={"thinking_levels": {"scout": "low"}})
    written = env_file.read_text(encoding="utf-8")
    assert '"SOFP": "high"' in written and '"scout": "low"' in written


def test_an_unknown_level_is_refused(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    r = client.post("/api/settings", json={"thinking_levels": {"SOFP": "extreme"}})
    assert r.status_code == 400
    assert "minimal" in r.json()["detail"]


def test_an_unknown_role_is_refused(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    r = client.post("/api/settings", json={"thinking_levels": {"nope": "high"}})
    assert r.status_code == 400
    assert "Unknown thinking_levels key" in r.json()["detail"]


def test_a_non_object_payload_is_refused(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    r = client.post("/api/settings", json={"thinking_levels": ["high"]})
    assert r.status_code == 400


def test_a_blank_value_under_an_unknown_key_is_still_refused(tmp_path, monkeypatch):
    """The key check runs BEFORE the empty-value skip, and the Settings form
    posts every role on every save. So one wrong key rejects the whole PATCH
    — the model, proxy and API-key fields included.

    That is what shipped: the form's five notes rows carried the CLI's
    spelling (`corporate_info`) rather than the NotesTemplateType values, and
    the General tab could not save anything at all (2026-08-03). Pinning the
    blank case is the point — the broken rows were usually blank.
    """
    _env(tmp_path, monkeypatch)
    r = client.post(
        "/api/settings",
        json={"thinking_levels": {"SOFP": "high", "corporate_info": ""}},
    )
    assert r.status_code == 400
    assert "corporate_info" in r.json()["detail"]


def test_every_notes_role_the_runtime_looks_up_is_an_accepted_key(
    tmp_path, monkeypatch,
):
    """`notes/agent.py` resolves its level with `template_type.value`. If the
    settings API accepted a different spelling, a saved level would simply
    never be read — a silent no-op rather than an error."""
    from notes_types import NotesTemplateType

    env_file = _env(tmp_path, monkeypatch)
    for nt in NotesTemplateType:
        r = client.post(
            "/api/settings", json={"thinking_levels": {nt.value: "low"}},
        )
        assert r.status_code == 200, (nt.value, r.json())

    monkeypatch.setenv(
        "XBRL_THINKING_LEVELS",
        env_file.read_text(encoding="utf-8")
        .split("XBRL_THINKING_LEVELS=", 1)[1]
        .strip()
        .strip("'\""),
    )
    for nt in NotesTemplateType:
        assert server.thinking_level_for(nt.value) == "low", nt.value


def test_the_new_proxy_models_are_offered(tmp_path, monkeypatch):
    """The ids come from the enterprise proxy's own list. Note the `global.`
    segment on the OpenAI ones — our older entries omit it."""
    _env(tmp_path, monkeypatch)
    ids = {m["id"] for m in server._load_available_models()}
    assert "openai.global.gpt-5.6" in ids
    assert "vertex_ai.gemini-3.6-flash" in ids
