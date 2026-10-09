"""Cycle 4: Settings API — GET/POST /api/settings."""
import pytest

import server
from fastapi.testclient import TestClient
from server import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_appearance_overlay(monkeypatch):
    # Runtime persistence mutates os.environ; register the new key with the
    # fixture so tests cannot inherit a previous test's saved overlay.
    monkeypatch.setenv("XBRL_NOTES_APPEARANCE_OVERRIDES", "{}")
    monkeypatch.delenv("XBRL_NOTES_APPEARANCE_OVERRIDES")


def test_sparse_appearance_inherits_code_updates_and_reset_masks_legacy(monkeypatch):
    import notes.table_theme as theme
    monkeypatch.delenv(theme.OVERRIDES_ENV_VAR, raising=False)
    monkeypatch.delenv(theme.ENV_VAR, raising=False)
    response = client.post('/api/settings', json={
        'notes_appearance_overrides': {'fontSizePt': 12},
    })
    assert response.status_code == 200
    assert client.get('/api/settings').json()['notes_appearance_overrides'] == {'fontSizePt': 12}
    rejected = client.post('/api/settings', json={'notes_table_style': {'fontSizePt': 20}})
    assert rejected.status_code == 400
    assert 'notes_appearance_overrides' in rejected.json()['detail']
    assert theme.firm_theme()['fontSizePt'] == 12
    monkeypatch.setitem(theme.HOUSE_NOTES_TABLE_STYLE, 'paragraphSpacingPx', 14)
    assert theme.firm_theme()['paragraphSpacingPx'] == 14
    assert theme.firm_theme()['fontSizePt'] == 12
    monkeypatch.setenv(theme.ENV_VAR, '{"fontSizePt": 20}')
    assert client.post('/api/settings', json={'notes_appearance_reset': True}).status_code == 200
    settings = client.get('/api/settings').json()
    assert settings['notes_appearance_overrides'] == {}
    assert settings['notes_table_style'] == settings['notes_house_style']
    rejected = client.post('/api/settings', json={'notes_table_style': {'fontSizePt': 20}})
    assert rejected.status_code == 400
    assert client.get('/api/settings').json()['notes_table_style'] == settings['notes_house_style']
    monkeypatch.setitem(theme.HOUSE_NOTES_TABLE_STYLE, 'fontSizePt', 13)
    assert theme.firm_theme()['fontSizePt'] == 13


def test_sparse_appearance_preserves_legacy_and_resets_one_field(monkeypatch):
    import notes.table_theme as theme
    monkeypatch.delenv(theme.OVERRIDES_ENV_VAR, raising=False)
    monkeypatch.setenv(theme.ENV_VAR, '{}')
    assert client.post('/api/settings', json={'notes_appearance_overrides': {'borderStyle': 'none'}}).status_code == 200
    settings = client.get('/api/settings').json()
    assert settings['notes_table_style']['fontSizePt'] == 10
    assert settings['notes_table_style']['headerFill'] == '#f3f4f6'
    assert client.post('/api/settings', json={'notes_appearance_overrides': {'fontSizePt': None}}).status_code == 200
    settings = client.get('/api/settings').json()
    assert settings['notes_table_style']['fontSizePt'] == theme.house_style()['fontSizePt']
    assert settings['notes_table_style']['borderStyle'] == 'none'


@pytest.mark.parametrize('patch', [{'fontSizePt': 100}, {'unknown': None}, {'headerBold': 'yes'}])
def test_sparse_appearance_rejects_invalid_patch(patch):
    assert client.post('/api/settings', json={'notes_appearance_overrides': patch}).status_code == 400


@pytest.mark.parametrize('appearance', [
    {'notes_appearance_reset': True},
    {'notes_appearance_overrides': {'fontSizePt': 12}},
])
def test_legacy_appearance_save_cannot_be_combined_with_new_settings(appearance):
    before = client.get('/api/settings').json()['notes_table_style']
    response = client.post('/api/settings', json={
        'notes_table_style': {'fontSizePt': 20}, **appearance,
    })
    assert response.status_code == 400
    assert 'notes_appearance_overrides' in response.json()['detail']
    assert client.get('/api/settings').json()['notes_table_style'] == before


def test_get_settings_default(tmp_path, monkeypatch):
    """Returns defaults when neither local settings nor .env exists."""
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
    assert data["model"] == "openai.global.gpt-6-luna"
    assert data["reasoning_summary"] == "auto"
    assert data["api_key_set"] is False
    assert "proxy_url" in data


def test_default_model_is_gpt_6_luna_for_every_agent_role(tmp_path, monkeypatch):
    """When TEST_MODEL and XBRL_DEFAULT_MODELS are unset, every agent role
    (scout + 5 statement types) resolves to GPT-6 Luna.

    Pins the decision that GPT-6 Luna is the global default across platforms
    (Mac direct + Windows proxy). If someone reverts the settings/server
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
        assert defaults[role] == "openai.global.gpt-6-luna", (
            f"Agent role {role!r} defaulted to {defaults[role]!r}, "
            f"expected 'openai.global.gpt-6-luna'."
        )


def test_reasoning_summary_setting_roundtrips_and_validates(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    response = client.post("/api/settings", json={"reasoning_summary": "detailed"})
    assert response.status_code == 200
    assert client.get("/api/settings").json()["reasoning_summary"] == "detailed"

    bad = client.post("/api/settings", json={"reasoning_summary": "private_chain"})
    assert bad.status_code == 400


def test_post_settings_writes_local_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.setattr(server, "SETTINGS_FILE", settings_file)
    resp = client.post("/api/settings", json={
        "model": "vertex_ai.gemini-3-flash-preview",
        "api_key": "test-key-123",
        "proxy_url": "https://genai-sharedservice-emea.pwc.com",
    })
    assert resp.status_code == 200
    saved = __import__("json").loads(settings_file.read_text())
    assert saved["TEST_MODEL"] == "vertex_ai.gemini-3-flash-preview"
    assert saved["GOOGLE_API_KEY"] == "test-key-123"
    assert saved["LLM_PROXY_URL"] == "https://genai-sharedservice-emea.pwc.com"
    assert not env_file.exists()


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


@pytest.mark.parametrize("field", ["auto_review", "notes_auto_review"])
@pytest.mark.parametrize("value", [False, True])
def test_legacy_review_setting_is_rejected_without_writes(tmp_path, monkeypatch, field, value):
    """Retired controls cannot silently persist a different value."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.delenv("XBRL_AUTO_REVIEW", raising=False)

    # Default is on.
    assert client.get("/api/settings").json()["auto_review"] is True
    assert client.get("/api/config").json()["auto_review"] is True

    from runtime_settings import read_settings
    before = read_settings(server.SETTINGS_FILE)
    resp = client.post("/api/settings", json={field: value, "model": "other-model"})
    assert resp.status_code == 400
    assert "always enabled" in resp.json()["detail"]
    assert read_settings(server.SETTINGS_FILE) == before
    assert client.get("/api/settings").json()["auto_review"] is True
    assert server._auto_review_enabled() is True


def test_pdf_notes_formatting_cannot_be_disabled_by_legacy_settings(monkeypatch):
    monkeypatch.setenv("XBRL_PDF_NOTES_AUTO_FORMAT", "false")
    assert "pdf_notes_auto_format" not in client.get("/api/settings").json()
    assert "pdf_notes_auto_format" not in client.get("/api/config").json()
    response = client.post("/api/settings", json={"pdf_notes_auto_format": False})
    assert response.status_code == 200
    assert "XBRL_PDF_NOTES_AUTO_FORMAT" not in server.SETTINGS_FILE.read_text()


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
    assert "XBRL_NOTES_COVERAGE" in server.SETTINGS_FILE.read_text()
    assert client.get("/api/settings").json()["notes_coverage"] is False
    assert server._notes_coverage_enabled() is False


def test_scout_limits_round_trip_and_apply_without_restart(tmp_path, monkeypatch):
    """The Settings controls persist the two Scout guards, and the runtime
    resolvers see the new values immediately."""
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(server, "SETTINGS_FILE", settings_file)
    monkeypatch.delenv("XBRL_SCOUT_WALLCLOCK_S", raising=False)
    monkeypatch.delenv("XBRL_SCOUT_MAX_TURNS", raising=False)

    defaults = client.get("/api/settings").json()
    assert defaults["scout_wallclock_seconds"] == 600
    assert defaults["scout_max_turns"] == 40

    response = client.post(
        "/api/settings",
        json={"scout_wallclock_seconds": 900, "scout_max_turns": 32},
    )
    assert response.status_code == 200

    from scout.limits import resolve_scout_max_turns, resolve_scout_wallclock

    assert resolve_scout_wallclock() == 900
    assert resolve_scout_max_turns() == 32
    saved = client.get("/api/settings").json()
    assert saved["scout_wallclock_seconds"] == 900
    assert saved["scout_max_turns"] == 32


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scout_wallclock_seconds", -1),
        ("scout_wallclock_seconds", "never"),
        ("scout_max_turns", 0),
        ("scout_max_turns", 41),
        ("scout_max_turns", 2.5),
    ],
)
def test_scout_limits_reject_unsafe_values(field, value):
    response = client.post("/api/settings", json={field: value})
    assert response.status_code == 400


def test_clean_run_triage_has_no_settings_fork(tmp_path, monkeypatch):
    """Stale saved values cannot disable triage or select a full pass."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.setenv("XBRL_SPOT_CHECK", "false")
    monkeypatch.setenv("XBRL_SPOT_CHECK_MODE", "full")
    s = client.get("/api/settings").json()
    assert "spot_check" not in s
    assert "spot_check_mode" not in s
    cfg = client.get("/api/config").json()
    assert "spot_check" not in cfg
    assert "spot_check_mode" not in cfg


@pytest.mark.parametrize("field,value", [("spot_check", False), ("spot_check_mode", "full")])
def test_removed_spot_check_settings_rejected(tmp_path, monkeypatch, field, value):
    """Old clients receive a clear error instead of a false saved preference."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    resp = client.post("/api/settings", json={field: value})
    assert resp.status_code == 400


def test_notes_source_integrity_round_trips(tmp_path, monkeypatch):
    """Gotcha #31's rollout mode is operator-settable from Settings. All three
    values are offered — the operator asked for the full
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
    """The firm notes-table theme persists locally and reads back via both
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
    """Bad colour / enum / range fails loudly (400), never lands in settings."""
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

def test_house_notes_table_style_matches_shipped_test_appearance(monkeypatch):
    """Fresh installations use the chosen appearance without local settings."""
    import server
    monkeypatch.delenv("XBRL_NOTES_TABLE_STYLE", raising=False)
    style = server._notes_table_style()
    assert style["borderStyle"] == "none"
    assert style["headerRule"] is False
    assert style["headerBold"] is True
    assert style["headerFill"] == "transparent"
    assert style["totalsDoubleUnderline"] is False
    assert style["fontSizePt"] == 11
    assert style["cellPaddingPx"] == [3, 3]
    assert style["paragraphSpacingPx"] == 3


def test_operator_can_still_opt_out_to_the_historic_look(monkeypatch):
    """An explicit `{}` means "each surface's historic default" — the escape
    hatch, so the house style is a preference and not a hard-coded look."""
    import server
    monkeypatch.setenv("XBRL_NOTES_TABLE_STYLE", "{}")
    assert server._notes_table_style() == {}


def test_malformed_house_style_degrades_to_the_house_default(monkeypatch):
    import server
    monkeypatch.setenv("XBRL_NOTES_TABLE_STYLE", "not json{")
    assert server._notes_table_style()["headerRule"] is False


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
    monkeypatch.setattr(server, "SETTINGS_FILE", tmp_path / "settings.json")
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
    _env(tmp_path, monkeypatch)
    r = client.post("/api/settings", json={"thinking_levels": {"SOFP": "high"}})
    assert r.status_code == 200
    assert server._thinking_levels()["SOFP"] == "high"


@pytest.mark.parametrize("level", ["xhigh", "max"])
def test_gpt56_levels_can_be_saved(tmp_path, monkeypatch, level):
    """The API accepts every level it advertises for a GPT-5.6 role."""
    _env(tmp_path, monkeypatch)
    r = client.post(
        "/api/settings", json={"thinking_levels": {"SOFP": level}},
    )
    assert r.status_code == 200, r.json()
    assert server._thinking_levels()["SOFP"] == level


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
    assert not server.SETTINGS_FILE.exists(), (
        "validation must finish before the local settings file is created"
    )


def test_clearing_a_role_returns_it_to_the_provider_default(tmp_path, monkeypatch):
    """Without this there is no way back to "send nothing" once a level is
    set — a merge-only update would make the first choice permanent."""
    env_file = _env(tmp_path, monkeypatch)
    client.post("/api/settings", json={"thinking_levels": {"SOFP": "high"}})
    client.post("/api/settings", json={"thinking_levels": {"SOFP": ""}})
    assert "SOFP" not in server._thinking_levels()


def test_other_roles_survive_an_update_to_one(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    client.post("/api/settings", json={"thinking_levels": {"SOFP": "high"}})
    client.post("/api/settings", json={"thinking_levels": {"scout": "low"}})
    assert server._thinking_levels() == {"SOFP": "high", "scout": "low"}


def test_one_role_model_override_does_not_pin_every_role(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_MODEL", "openai.gpt-5.4")
    monkeypatch.delenv("XBRL_DEFAULT_MODELS", raising=False)

    response = client.post(
        "/api/settings",
        json={"default_models": {"scout": "openai.gpt-5.6"}},
    )

    assert response.status_code == 200
    stored = __import__("json").loads(server.SETTINGS_FILE.read_text())
    assert __import__("json").loads(stored["XBRL_DEFAULT_MODELS"]) == {
        "scout": "openai.gpt-5.6",
    }


def test_role_model_can_return_to_following_global(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_MODEL", "openai.gpt-5.4")
    monkeypatch.delenv("XBRL_DEFAULT_MODELS", raising=False)
    client.post(
        "/api/settings",
        json={"default_models": {"scout": "openai.gpt-5.6"}},
    )

    response = client.post(
        "/api/settings", json={"default_models": {"scout": ""}},
    )

    assert response.status_code == 200
    assert server._configured_default_models() == {}
    assert server._load_extended_settings()["default_models"]["scout"] == (
        "openai.gpt-5.4"
    )


def test_reset_key_removes_local_override_and_restores_env_fallback(
    tmp_path, monkeypatch,
):
    env_file = _env(tmp_path, monkeypatch)
    env_file.write_text("TEST_MODEL=openai.gpt-5.4\n", encoding="utf-8")
    import runtime_settings
    runtime_settings._FALLBACKS.pop("TEST_MODEL", None)
    runtime_settings._APPLIED.pop("TEST_MODEL", None)
    server._reload_runtime_settings()
    client.post("/api/settings", json={"model": "openai.gpt-5.6"})
    assert client.get("/api/settings").json()["model"] == "openai.gpt-5.6"

    response = client.post("/api/settings", json={"reset_keys": ["model"]})

    assert response.status_code == 200
    assert client.get("/api/settings").json()["model"] == "openai.gpt-5.4"
    saved = __import__("json").loads(server.SETTINGS_FILE.read_text())
    assert "TEST_MODEL" not in saved


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

    _env(tmp_path, monkeypatch)
    for nt in NotesTemplateType:
        r = client.post(
            "/api/settings", json={"thinking_levels": {nt.value: "low"}},
        )
        assert r.status_code == 200, (nt.value, r.json())

    for nt in NotesTemplateType:
        assert server.thinking_level_for(nt.value) == "low", nt.value


def test_the_new_proxy_models_are_offered(tmp_path, monkeypatch):
    """The ids come from the enterprise proxy's own list. Note the `global.`
    segment on the OpenAI ones — our older entries omit it."""
    _env(tmp_path, monkeypatch)
    ids = {m["id"] for m in server._load_available_models()}
    assert "openai.global.gpt-5.6" in ids
    assert "vertex_ai.gemini-3.6-flash" in ids


@pytest.mark.parametrize("sidecar", [True, False])
@pytest.mark.parametrize("auto_format", [True, False])
def test_legacy_pdf_settings_do_not_reject_other_settings(tmp_path, monkeypatch, sidecar, auto_format):
    _env(tmp_path, monkeypatch)
    response = client.post("/api/settings", json={
        "pdf_sidecar": sidecar, "pdf_notes_auto_format": auto_format,
        "notes_coverage": False,
    })
    assert response.status_code == 200
    settings = client.get("/api/settings").json()
    assert settings["auto_review"] is True
    assert "pdf_sidecar" not in settings
    assert "pdf_notes_auto_format" not in settings
    saved = server.SETTINGS_FILE.read_text()
    assert "XBRL_PDF_SIDECAR" not in saved
    assert "XBRL_PDF_NOTES_AUTO_FORMAT" not in saved
    assert "pdf_sidecar" not in client.get("/api/config").json()
    assert "XBRL_PDF_SIDECAR_PAGE_CAP" not in _advanced(settings)
    response = client.post("/api/settings", json={
        "advanced_settings": {"XBRL_PDF_SIDECAR_PAGE_CAP": 80},
    })
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Advanced settings (former env-only switches, settings_catalog.py)
# ---------------------------------------------------------------------------

def _advanced(body: dict) -> dict:
    return {row["key"]: row for row in body["advanced_settings"]}


def test_reviewer_time_settings_apply_to_next_review(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    keys = {
        "XBRL_CORRECTION_WALLCLOCK_S": 600,
        "XBRL_NOTES_VALIDATOR_WALLCLOCK_S": 600,
        "XBRL_NOTES_REVIEWER_EXTRA_ITEM_S": 20,
        "XBRL_NOTES_REVIEWER_MAX_WALLCLOCK_S": 1200,
    }
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    rows = _advanced(client.get("/api/settings").json())
    for key, default in keys.items():
        assert rows[key]["default"] == default
        assert rows[key]["restart"] is False
    updates = dict(zip(keys, [900, 720, 30, 1500]))
    response = client.post("/api/settings", json={"advanced_settings": updates})
    assert response.status_code == 200
    rows = _advanced(client.get("/api/settings").json())
    assert all(rows[key]["value"] == value for key, value in updates.items())
    assert server._resolve_wallclock("XBRL_CORRECTION_WALLCLOCK_S", 600) == 900
    base = server._resolve_wallclock("XBRL_NOTES_VALIDATOR_WALLCLOCK_S", 600)
    assert server._notes_reviewer_wallclock_limit(base, 13) == 810
    assert server._notes_reviewer_wallclock_limit(base, 100) == 1500
    response = client.post("/api/settings", json={"advanced_settings": dict.fromkeys(keys)})
    assert response.status_code == 200
    base = server._resolve_wallclock("XBRL_NOTES_VALIDATOR_WALLCLOCK_S", 600)
    assert server._notes_reviewer_wallclock_limit(base, 13) == 660


def test_advanced_setting_saves_reaches_the_pipeline_and_resets(tmp_path, monkeypatch):
    env_file = _env(tmp_path, monkeypatch)
    env_file.write_text("XBRL_MAX_CONCURRENT_AGENTS=2\n", encoding="utf-8")
    for key in ("XBRL_MAX_CONCURRENT_AGENTS", "XBRL_TEMPLATE_IN_PROMPT",
                "XBRL_REVIEWER_COMPACT_CONTEXT", "XBRL_MAX_AGENT_ITERATIONS"):
        monkeypatch.delenv(key, raising=False)
    import runtime_settings
    runtime_settings._FALLBACKS.pop("XBRL_MAX_CONCURRENT_AGENTS", None)
    runtime_settings._APPLIED.pop("XBRL_MAX_CONCURRENT_AGENTS", None)
    from agent_tracing import resolve_max_iterations
    import os
    from agent_concurrency import max_concurrent_agents
    from extraction.agent import _template_in_prompt_enabled
    from correction.history_processors import reviewer_compact_context_enabled

    rows = _advanced(client.get("/api/settings").json())
    assert rows["XBRL_MAX_AGENT_ITERATIONS"]["value"] == 60
    assert rows["XBRL_MAX_AGENT_ITERATIONS"]["default"] == 60
    assert rows["XBRL_MAX_AGENT_ITERATIONS"]["max"] == 120
    assert rows["XBRL_MAX_AGENT_ITERATIONS"]["restart"] is True
    assert rows["XBRL_MAX_CONCURRENT_AGENTS"]["value"] == 2
    assert rows["XBRL_MAX_CONCURRENT_AGENTS"]["saved_here"] is False
    assert rows["XBRL_TEMPLATE_IN_PROMPT"]["value"] is False
    assert rows["XBRL_REVIEWER_COMPACT_CONTEXT"]["value"] is False

    response = client.post("/api/settings", json={"advanced_settings": {
        "XBRL_MAX_CONCURRENT_AGENTS": 3,
        "XBRL_TEMPLATE_IN_PROMPT": True,
        "XBRL_MAX_AGENT_ITERATIONS": 120,
        "XBRL_REVIEWER_COMPACT_CONTEXT": True,
    }})
    assert response.status_code == 200
    rows = _advanced(client.get("/api/settings").json())
    assert rows["XBRL_MAX_CONCURRENT_AGENTS"]["value"] == 3
    assert rows["XBRL_MAX_CONCURRENT_AGENTS"]["saved_here"] is True
    # "Use default" must preview the .env value it restores, not the built-in 0.
    assert rows["XBRL_MAX_CONCURRENT_AGENTS"]["fallback"] == 2
    assert rows["XBRL_TEMPLATE_IN_PROMPT"]["fallback"] is False
    assert resolve_max_iterations(os.environ.get("XBRL_MAX_AGENT_ITERATIONS")) == 120
    assert rows["XBRL_MAX_AGENT_ITERATIONS"]["value"] == 120
    assert max_concurrent_agents() == 3
    assert _template_in_prompt_enabled() is True
    assert reviewer_compact_context_enabled() is True
    assert rows["XBRL_REVIEWER_COMPACT_CONTEXT"]["saved_here"] is True

    client.post("/api/settings", json={"advanced_settings": {
        "XBRL_MAX_CONCURRENT_AGENTS": None,
        "XBRL_TEMPLATE_IN_PROMPT": None,
        "XBRL_MAX_AGENT_ITERATIONS": None,
        "XBRL_REVIEWER_COMPACT_CONTEXT": None,
    }})
    rows = _advanced(client.get("/api/settings").json())
    assert rows["XBRL_MAX_CONCURRENT_AGENTS"]["value"] == 2
    assert rows["XBRL_MAX_CONCURRENT_AGENTS"]["saved_here"] is False
    assert _template_in_prompt_enabled() is False
    assert rows["XBRL_MAX_AGENT_ITERATIONS"]["value"] == 60
    assert resolve_max_iterations(os.environ.get("XBRL_MAX_AGENT_ITERATIONS")) == 60
    assert reviewer_compact_context_enabled() is False


@pytest.mark.parametrize("payload", [
    {"XBRL_NOT_A_SETTING": 1},
    {"SESSION_SECRET": "x"},
    {"XBRL_MAX_AGENT_ITERATIONS": 121},
    {"XBRL_MAX_AGENT_ITERATIONS": 0},
    {"XBRL_MAX_AGENT_ITERATIONS": 2.5},
    {"XBRL_FACT_BASED_CHECKS": "yes"},
    {"XBRL_WRITE_FRESHNESS": "strict"},
    {"XBRL_SOFT_COMPACT_TOKENS": -1},
    ["XBRL_LOG_LEVEL"],
    {"XBRL_CACHE_PROBE": True},
    {"XBRL_STAGE_RESUME": True},
])
def test_invalid_advanced_settings_are_refused_before_any_write(
    tmp_path, monkeypatch, payload,
):
    _env(tmp_path, monkeypatch)
    rows = _advanced(client.get("/api/settings").json())
    assert not {"XBRL_CACHE_PROBE", "XBRL_STAGE_RESUME"}.intersection(rows)
    response = client.post("/api/settings", json={
        "model": "openai.gpt-5.4", "advanced_settings": payload,
    })
    assert response.status_code == 400
    assert not server.SETTINGS_FILE.exists()


@pytest.mark.parametrize("key,raw,expected", [
    *[("XBRL_FACE_WALLCLOCK_S", raw, 1800.0)
      for raw in ("nan", "inf", "-Infinity", "abc")],
    ("XBRL_MAX_AGENT_ITERATIONS", "abc", 60),
    ("XBRL_MAX_AGENT_ITERATIONS", "0", 60),
    ("XBRL_MAX_AGENT_ITERATIONS", "500", 120),
])
def test_malformed_env_value_shows_the_default_not_a_server_error(
    tmp_path, monkeypatch, key, raw, expected,
):
    _env(tmp_path, monkeypatch)
    monkeypatch.setenv(key, raw)
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert _advanced(response.json())[key]["value"] == expected


def test_every_advanced_setting_is_read_by_product_code():
    """A listed setting that nothing reads would look saved and do nothing."""
    from pathlib import Path
    from settings_catalog import ADVANCED_SETTINGS

    root = Path(server.__file__).parent
    source = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.rglob("*.py")
        if not {"venv", "tests", "node_modules"} & set(path.relative_to(root).parts)
        and path.name != "settings_catalog.py"
    )
    unread = [s.key for s in ADVANCED_SETTINGS if f'"{s.key}"' not in source]
    assert unread == []


def test_shared_reset_restores_application_defaults_not_old_deployment(tmp_path, monkeypatch):
    """Confirmed reset restores the agreed defaults without losing connection or guidance."""
    from runtime_settings import update_settings, read_settings
    monkeypatch.setattr(server, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(server, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setenv("TEST_MODEL", "old-deployment-model")
    monkeypatch.setenv("SCOUT_MODEL", "old-scout-model")
    monkeypatch.setenv("XBRL_NOTES_SOURCE_INTEGRITY", "shadow")
    monkeypatch.setenv("XBRL_SCOUT_WALLCLOCK_S", "300")
    monkeypatch.setenv("XBRL_SCOUT_MAX_TURNS", "20")
    monkeypatch.setenv("XBRL_ENTITY_MEMORY", "true")
    monkeypatch.setenv("XBRL_DEFAULT_MODELS", '{"reviewer":"old-review-model"}')
    monkeypatch.setenv("XBRL_MAX_CONCURRENT_AGENTS", "2")
    update_settings(server.SETTINGS_FILE, {
        "GOOGLE_API_KEY": "test-secret-keep", "LLM_PROXY_URL": "https://service.example.com",
        "XBRL_TEAM_GUIDANCE": "keep guidance", "XBRL_NOTES_APPEARANCE_OVERRIDES": '{"headerFill":"#abcdef","borderStyle":"single"}',
    })
    assert client.post("/api/settings", json={"reset_shared_defaults": True}).status_code == 200
    settings = client.get("/api/settings").json()
    assert settings["model"] == "openai.global.gpt-6-luna"
    assert settings["scout_wallclock_seconds"] == 600
    assert settings["scout_max_turns"] == 40
    assert settings["entity_memory"] is False
    assert settings["notes_source_integrity"] == "shadow"
    assert settings["default_model_overrides"] == {}
    assert all(value == "openai.global.gpt-6-luna" for value in settings["default_models"].values())
    assert settings["thinking_levels"] == {}
    assert settings["reasoning_summary"] == "auto"
    assert settings["auto_review"] and settings["notes_auto_review"] and settings["notes_coverage"]
    assert settings["tolerance_rm"] == 1
    assert settings["notes_table_style"]["borderStyle"] == "none"
    assert settings["notes_table_style"]["headerFill"] == "transparent"
    assert settings["notes_appearance_overrides"] == {}
    assert all(row["value"] == row["default"] for row in settings["advanced_settings"])
    saved = read_settings(server.SETTINGS_FILE)
    assert saved["GOOGLE_API_KEY"] == "test-secret-keep"
    assert saved["LLM_PROXY_URL"] == "https://service.example.com"
    assert saved["XBRL_TEAM_GUIDANCE"] == "keep guidance"
    assert saved["SCOUT_MODEL"] == ""


@pytest.mark.parametrize("body", [{"reset_shared_defaults": False}, {"reset_shared_defaults": 1}, {"reset_shared_defaults": True, "model": "other"}])
def test_shared_reset_rejects_ambiguous_request_without_writes(body):
    from runtime_settings import read_settings
    before = read_settings(server.SETTINGS_FILE)
    assert client.post("/api/settings", json=body).status_code == 400
    assert read_settings(server.SETTINGS_FILE) == before
