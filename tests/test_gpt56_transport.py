"""GPT-5.6 and Gemini 3 need transports this repo was not using.

Peer review, 2026-08-01. Two configurations that cannot be repaired by prompt
wording, both confirmed against the vendors' own documentation.

GPT-5.6
    "For GPT-5.6, function tools in Chat Completions are compatible only with
    effective reasoning `none`" and "If omitted, GPT-5.6 defaults to `medium`"
    (OpenAI migration guide). Every agent here is a multi-turn function-tool
    caller, and `_create_proxy_model` built every OpenAI model as an
    `OpenAIChatModel`. Sending nothing was not neutral: it selected the
    incompatible case. OpenAI's instruction is to "migrate that flow to
    Responses ... otherwise report it as a compatibility blocker".

    5.6 also dropped `minimal` from its reasoning vocabulary (it lists `none`,
    `low`, `medium`, `high`, `xhigh`, `max`), so the value this repo offered
    was one the model does not accept.

Gemini 3
    Google requires the `thought_signature` from each prior functionCall to be
    echoed back on the next request, "even when using MINIMAL thinking
    levels"; omitting it is a 400. The OpenAI chat format has no field for it,
    so a Gemini 3 model on an OpenAI-compatible proxy dies on the second tool
    call. The Mac local-dev proxy already bypasses to a native GoogleModel;
    the enterprise proxy has no native path, so this must fail loudly at
    construction rather than mid-run.
"""
from __future__ import annotations

import pytest

from model_settings import (
    DEFAULT_MODEL_ID,
    THINKING_LEVELS,
    build_model_settings,
    configured_reasoning_summary,
    describe_model_runtime,
    normalize_thinking_level,
    use_responses_api,
)


class _FakeChat:
    """Stands in for OpenAIChatModel — build_model_settings keys off the
    Python type NAME, so a stub with the right name exercises the branch."""
    def __init__(self, model_name: str):
        self.model_name = model_name


_FakeChat.__name__ = "OpenAIChatModel"


class _FakeResponses:
    def __init__(self, model_name: str):
        self.model_name = model_name


_FakeResponses.__name__ = "OpenAIResponsesModel"


def test_product_default_is_gpt56_luna():
    assert DEFAULT_MODEL_ID == "openai.global.gpt-5.6-luna"


# ---------------------------------------------------------------------------
# Reasoning vocabulary
# ---------------------------------------------------------------------------

def test_none_is_an_offerable_level():
    """Without it there is no way to express reasoning-off: omitting the
    field means 'provider default', which on 5.6 is `medium`."""
    assert "none" in THINKING_LEVELS
    assert normalize_thinking_level("none") == "none"


def test_minimal_falls_back_to_low_on_gpt_56_not_to_none():
    """5.6 does not list `minimal`. The substitute must preserve INTENT.

    `minimal` means "the least reasoning available"; `none` means no
    reasoning at all. Folding one to the other inverted the operator's
    choice and silently disabled reasoning (peer review, 2026-08-02).

    Asserted on the RESPONSES transport, because that is where an operator's
    level is honoured at all — Chat Completions pins every 5.6 request to
    `none` for the function-tool compatibility rule (tested separately).
    """
    s = build_model_settings(_FakeResponses("gpt-5.6"), thinking_level="minimal")
    assert s.get("openai_reasoning_effort") == "low"


def test_an_unsupported_level_is_logged_not_silently_swapped(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="model_settings"):
        build_model_settings(_FakeResponses("gpt-5.6"), thinking_level="minimal")
    messages = [r.getMessage() for r in caplog.records]
    assert any("does not support thinking level" in m for m in messages), messages
    # It must name what it used instead, not just that something was wrong.
    assert any("using 'low'" in m for m in messages), messages


def test_supported_levels_are_model_aware():
    from model_settings import supported_thinking_levels

    assert "minimal" not in supported_thinking_levels("openai.global.gpt-5.6")
    assert "xhigh" in supported_thinking_levels("openai.global.gpt-5.6")
    assert "max" in supported_thinking_levels("openai.global.gpt-5.6")
    assert "minimal" in supported_thinking_levels("openai.gpt-5.4")
    assert "minimal" in supported_thinking_levels("vertex_ai.gemini-3.6-flash")
    # `none` is expressible everywhere — literal on OpenAI, `thinking=False`
    # on Anthropic and Google.
    for name in ("openai.global.gpt-5.6", "openai.gpt-5.4", "claude-sonnet-5"):
        assert "none" in supported_thinking_levels(name)


def test_the_picker_is_narrowed_per_model():
    """The API must hand the UI a per-model list, or the form keeps offering
    a level the run will substitute."""
    import server  # noqa: F401  (import order: config_routes imports server)
    from api.config_routes import _levels_by_model

    by_model = _levels_by_model()
    assert by_model, "no catalogued models"
    for model_id, levels in by_model.items():
        if "gpt-5.6" in model_id:
            assert "minimal" not in levels, model_id
            assert "xhigh" in levels, model_id
            assert "max" in levels, model_id
        assert "none" in levels, model_id


def test_runtime_description_is_best_effort_for_hostile_provider_metadata():
    """Trace metadata can never suppress the trace or fail an agent run."""
    from model_settings import describe_model_runtime

    class HostileUrl:
        def __str__(self):
            raise RuntimeError("unprintable URL")

    model = _FakeChat("gpt-5.6")
    model.provider = type("Provider", (), {"base_url": HostileUrl()})()

    description = describe_model_runtime(model, role="SOFP")

    assert description["model"] == "gpt-5.6"
    assert description["endpoint"] in {"provider_default", "unknown"}


def test_minimal_survives_on_older_openai_models():
    s = build_model_settings(_FakeChat("gpt-5.4"), thinking_level="minimal")
    assert s.get("openai_reasoning_effort") == "minimal"


# ---------------------------------------------------------------------------
# The incompatible combination
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_gpt56_on_chat_completions_is_pinned_to_none(level):
    """Function tools + Chat Completions + any reasoning is the unsupported
    case. Pin it rather than let the request go out incompatible."""
    s = build_model_settings(_FakeChat("gpt-5.6"), thinking_level=level)
    assert s.get("openai_reasoning_effort") == "none"


def test_gpt56_with_no_level_still_sends_none_explicitly():
    """The critical one: omitting the field is NOT the same as `none`,
    because 5.6 then defaults to `medium`."""
    s = build_model_settings(_FakeChat("gpt-5.6"))
    assert s.get("openai_reasoning_effort") == "none"


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_gpt56_on_responses_keeps_the_operator_choice(level):
    """Responses is the transport where reasoning and tools coexist, so the
    pin must NOT apply there — otherwise the migration buys nothing."""
    s = build_model_settings(_FakeResponses("gpt-5.6"), thinking_level=level)
    assert s.get("openai_reasoning_effort") == level
    assert "temperature" not in s


def test_responses_requests_provider_reasoning_summary_by_default(monkeypatch):
    monkeypatch.delenv("XBRL_REASONING_SUMMARY", raising=False)
    from pydantic_ai.models import openai as openai_models

    original = openai_models.OpenAIResponsesModelSettings
    called = False

    def responses_settings(**kwargs):
        nonlocal called
        called = True
        return original(**kwargs)

    monkeypatch.setattr(
        openai_models, "OpenAIResponsesModelSettings", responses_settings,
    )
    settings = build_model_settings(_FakeResponses("gpt-5.6-luna"))
    assert called
    assert settings.get("openai_reasoning_summary") == "auto"
    assert configured_reasoning_summary() == "auto"


@pytest.mark.parametrize("visibility", ["auto", "concise", "detailed"])
def test_responses_honours_configured_summary_visibility(monkeypatch, visibility):
    monkeypatch.setenv("XBRL_REASONING_SUMMARY", visibility)
    settings = build_model_settings(_FakeResponses("gpt-5.6-luna"))
    assert settings.get("openai_reasoning_summary") == visibility


def test_summary_visibility_is_independent_from_reasoning_effort(monkeypatch):
    monkeypatch.setenv("XBRL_REASONING_SUMMARY", "off")
    settings = build_model_settings(
        _FakeResponses("gpt-5.6-luna"), thinking_level="high",
    )
    assert settings.get("openai_reasoning_effort") == "high"
    assert "openai_reasoning_summary" not in settings


def test_summary_option_is_never_sent_to_chat_completions(monkeypatch):
    monkeypatch.setenv("XBRL_REASONING_SUMMARY", "detailed")
    settings = build_model_settings(_FakeChat("gpt-5.6-luna"))
    assert "openai_reasoning_summary" not in settings


def test_chat_reasoning_none_keeps_supported_temperature():
    s = build_model_settings(_FakeChat("gpt-5.6"), thinking_level="medium")
    assert s["openai_reasoning_effort"] == "none"
    assert s["temperature"] == 1.0


@pytest.mark.parametrize("level", ["xhigh", "max"])
def test_gpt56_responses_exposes_extended_effort_levels(level):
    assert normalize_thinking_level(level) == level
    settings = build_model_settings(
        _FakeResponses("gpt-5.6-luna"), thinking_level=level
    )
    assert settings.get("openai_reasoning_effort") == level


def test_runtime_description_distinguishes_responses_from_chat(monkeypatch):
    import json

    monkeypatch.setenv("XBRL_THINKING_LEVELS", json.dumps({"SOFP": "medium"}))
    monkeypatch.setenv("XBRL_REASONING_SUMMARY", "auto")
    responses = describe_model_runtime(_FakeResponses("gpt-5.6-luna"), role="SOFP")
    chat = describe_model_runtime(_FakeChat("gpt-5.6-luna"), role="SOFP")

    assert responses["transport"] == "responses"
    assert responses["effective_reasoning_effort"] == "medium"
    assert responses["reasoning_summary_visibility"] == "auto"
    assert chat["transport"] == "chat_completions"
    assert chat["configured_reasoning_effort"] == "medium"
    assert chat["effective_reasoning_effort"] == "none"
    assert chat["reasoning_summary_visibility"] == "off"


def test_run_runtime_snapshot_records_each_planned_role(monkeypatch):
    import json
    import server
    from notes_types import NotesTemplateType
    from statement_types import StatementType

    monkeypatch.setenv("XBRL_THINKING_LEVELS", json.dumps({"SOFP": "high"}))
    default = _FakeResponses("gpt-5.6-luna")
    snapshot = server._model_runtime_snapshot(
        default,
        {StatementType.SOFP},
        {},
        {NotesTemplateType.CORP_INFO},
        {},
    )
    assert set(snapshot) == {"default", "SOFP", "notes:CORP_INFO"}
    assert snapshot["SOFP"]["transport"] == "responses"
    assert snapshot["SOFP"]["effective_reasoning_effort"] == "high"


def test_older_models_are_untouched_on_chat_completions():
    """No regression for the model actually in production today."""
    s = build_model_settings(_FakeChat("gpt-5.4"), thinking_level="high")
    assert s.get("openai_reasoning_effort") == "high"

    plain = build_model_settings(_FakeChat("gpt-5.4"))
    assert "openai_reasoning_effort" not in plain


# ---------------------------------------------------------------------------
# Transport selection
# ---------------------------------------------------------------------------

def test_responses_api_selected_for_56_on_the_direct_path():
    assert use_responses_api("gpt-5.6") is True
    assert use_responses_api("openai.global.gpt-5.6") is True


def test_responses_api_not_forced_onto_the_enterprise_proxy():
    """The proxy may not expose /v1/responses; defaulting it on there would
    break every Windows run to fix a model nobody has run yet."""
    assert use_responses_api("gpt-5.6", "https://genai-sharedservice-emea.pwc.com/v1") is False


def test_responses_api_not_applied_to_older_models():
    assert use_responses_api("gpt-5.4") is False
    assert use_responses_api("openai.global.gpt-5.5-pro") is False


@pytest.mark.parametrize("value,expected", [("1", True), ("0", False)])
def test_responses_api_override(monkeypatch, value, expected):
    monkeypatch.setenv("XBRL_OPENAI_RESPONSES", value)
    assert use_responses_api("gpt-5.4", "https://proxy/v1") is expected


# ---------------------------------------------------------------------------
# Prompt cache shape
# ---------------------------------------------------------------------------

def test_cache_retention_field_is_the_default_shape():
    """`prompt_cache_retention` is deprecated on 5.6 but still works, and
    OpenAI says to verify before rewriting — so the old shape stays default."""
    s = build_model_settings(_FakeChat("gpt-5.6"))
    assert s.get("openai_prompt_cache_retention") == "24h"
    assert "extra_body" not in s


def test_cache_options_shape_is_opt_in(monkeypatch):
    monkeypatch.setenv("XBRL_OPENAI_CACHE_OPTIONS", "1")
    s = build_model_settings(_FakeChat("gpt-5.6"))
    # `30m` is the ONLY value `prompt_cache_options.ttl` accepts, and it is
    # also the default. The legacy field's `24h` is not interchangeable —
    # sending it here is a 400 on every request (peer review, 2026-08-02).
    assert s.get("extra_body") == {"prompt_cache_options": {"ttl": "30m"}}
    assert "openai_prompt_cache_retention" not in s


def test_the_two_cache_vocabularies_are_not_shared():
    """One constant for both shapes is the bug this pins against."""
    from model_settings import CACHE_OPTIONS_TTL, CACHE_RETENTION

    assert CACHE_RETENTION == "24h"
    assert CACHE_OPTIONS_TTL == "30m"
    assert CACHE_RETENTION != CACHE_OPTIONS_TTL


def test_cache_options_opt_in_does_not_affect_older_models(monkeypatch):
    monkeypatch.setenv("XBRL_OPENAI_CACHE_OPTIONS", "1")
    s = build_model_settings(_FakeChat("gpt-5.4"))
    assert s.get("openai_prompt_cache_retention") == "24h"


# ---------------------------------------------------------------------------
# Gemini thought signatures
# ---------------------------------------------------------------------------

def test_gemini3_on_the_enterprise_proxy_is_refused():
    import server
    with pytest.raises(ValueError, match="thought_signature"):
        server._warn_if_gemini_loses_thought_signatures(
            "vertex_ai.gemini-3.6-flash", "https://genai-sharedservice-emea.pwc.com/v1",
        )


def test_gemini3_on_the_local_proxy_is_allowed():
    """The Mac path already bypasses to a native GoogleModel, which
    round-trips the signature correctly."""
    import server
    server._warn_if_gemini_loses_thought_signatures(
        "vertex_ai.gemini-3.6-flash", "http://localhost:4000/v1",
    )


def test_non_gemini_models_are_unaffected():
    import server
    for name in ("openai.global.gpt-5.6", "bedrock.anthropic.claude-sonnet-5"):
        server._warn_if_gemini_loses_thought_signatures(name, "https://proxy/v1")


def test_gemini_proxy_can_be_unblocked_once_proven(monkeypatch):
    import server
    monkeypatch.setenv("XBRL_ALLOW_GEMINI_PROXY", "1")
    server._warn_if_gemini_loses_thought_signatures(
        "vertex_ai.gemini-3.6-flash", "https://proxy/v1",
    )
