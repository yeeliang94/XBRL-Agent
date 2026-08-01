"""Per-role thinking level (reasoning effort).

Reasoning effort was never set: every agent ran at whatever its provider
defaults to, on every run, since the project started. This adds a per-role
control alongside the per-role model choice that was already there.

The invariant that matters most is the FIRST test: with nothing configured,
the settings this builds are byte-identical to what they were before. A
performance knob that changes behaviour when nobody has touched it is not a
knob, it is a silent migration.

Provider mapping is not uniform, and the proxy makes it less uniform still:

* direct OpenAI takes the word (`minimal|low|medium|high`);
* direct Gemini and Claude take a token BUDGET;
* through the proxy, everything arrives as an OpenAI-shaped client, and
  LiteLLM translates `reasoning_effort` per provider — so the word is the
  right thing to send even for a proxied Gemini.
"""
from __future__ import annotations

import json

import pytest

import server
from model_settings import (
    THINKING_LEVELS,
    build_model_settings,
    normalize_thinking_level,
)


class _FakeModel:
    """Stands in for what `_create_proxy_model` returns."""

    def __init__(self, name: str, type_name: str = "OpenAIChatModel"):
        self.model_name = name
        self.__class__ = type(type_name, (_FakeModel,), {}) \
            if type(self).__name__ != type_name else type(self)


def _model(name: str, type_name: str = "OpenAIChatModel"):
    cls = type(type_name, (), {})
    m = cls()
    m.model_name = name
    return m


# --------------------------------------------------------------------------
# the default is "change nothing"
# --------------------------------------------------------------------------

def test_no_level_sends_no_reasoning_effort():
    """The whole safety of this change. Unset must equal today."""
    s = build_model_settings(_model("openai.gpt-5.4"), cache_key="k")
    assert "openai_reasoning_effort" not in s


def test_the_setting_is_empty_by_default(monkeypatch):
    monkeypatch.delenv("XBRL_THINKING_LEVELS", raising=False)
    assert server._thinking_levels() == {}
    assert server.thinking_level_for("SOFP") is None


def test_a_role_with_no_entry_sends_nothing(monkeypatch):
    monkeypatch.setenv("XBRL_THINKING_LEVELS", json.dumps({"SOFP": "high"}))
    assert server.thinking_level_for("SOFP") == "high"
    assert server.thinking_level_for("SOCI") is None


# --------------------------------------------------------------------------
# a bad value must not reach a provider
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", None, "very high", "HIGHEST", "1", 0])
def test_an_unrecognised_level_becomes_none(bad):
    assert normalize_thinking_level(bad) is None


@pytest.mark.parametrize("good", THINKING_LEVELS)
def test_every_declared_level_is_accepted(good):
    assert normalize_thinking_level(good) == good


def test_case_and_whitespace_are_tolerated():
    assert normalize_thinking_level("  High ") == "high"


def test_a_typo_in_the_env_is_dropped_not_forwarded(monkeypatch):
    """A wrong word would be rejected by the provider mid-run. Drop it here."""
    monkeypatch.setenv(
        "XBRL_THINKING_LEVELS", json.dumps({"SOFP": "high", "scout": "wrong"})
    )
    assert server._thinking_levels() == {"SOFP": "high"}


def test_malformed_json_degrades_to_nothing(monkeypatch):
    monkeypatch.setenv("XBRL_THINKING_LEVELS", "{not json")
    assert server._thinking_levels() == {}


def test_a_non_object_value_degrades_to_nothing(monkeypatch):
    monkeypatch.setenv("XBRL_THINKING_LEVELS", json.dumps(["high"]))
    assert server._thinking_levels() == {}


# --------------------------------------------------------------------------
# provider mapping
# --------------------------------------------------------------------------

def test_direct_openai_takes_the_word():
    s = build_model_settings(
        _model("openai.gpt-5.4"), cache_key="k", thinking_level="high",
    )
    assert s["openai_reasoning_effort"] == "high"


def test_a_proxied_gemini_still_receives_the_level():
    """It arrives as an OpenAI-shaped client, and LiteLLM translates the word
    into Gemini's thinking budget. Sending nothing would mean the setting
    silently did nothing for every Gemini run."""
    s = build_model_settings(
        _model("vertex_ai.gemini-3.5-flash"), cache_key="k",
        thinking_level="low",
    )
    assert s["openai_reasoning_effort"] == "low"


def test_a_proxied_gemini_still_gets_no_openai_cache_params():
    """The existing guard must survive: OpenAI-only cache fields are still
    withheld from a proxied Gemini even though it now carries a level."""
    s = build_model_settings(
        _model("vertex_ai.gemini-3.5-flash"), cache_key="k",
        thinking_level="low",
    )
    assert "openai_prompt_cache_key" not in s
    assert "openai_prompt_cache_retention" not in s


def test_direct_gemini_takes_a_token_budget():
    s = build_model_settings(
        _model("gemini-3.5-flash", "GoogleModel"), thinking_level="medium",
    )
    assert s["google_thinking_config"]["thinking_budget"] > 0


def test_direct_anthropic_takes_a_token_budget():
    s = build_model_settings(
        _model("claude-sonnet-4-6", "AnthropicModel"), thinking_level="high",
    )
    assert s["thinking"]["type"] == "enabled"
    assert s["thinking"]["budget_tokens"] > 0


def test_minimal_turns_anthropic_thinking_off_rather_than_sending_zero():
    """A zero budget is not a valid enabled-thinking request."""
    s = build_model_settings(
        _model("claude-sonnet-4-6", "AnthropicModel"), thinking_level="minimal",
    )
    assert s["thinking"] == {"type": "disabled"}


def test_an_unclassifiable_model_gets_no_level():
    """We cannot know which control it takes, so we send none rather than
    guess and have the request rejected."""
    s = build_model_settings("some-bare-string", thinking_level="high")
    assert "openai_reasoning_effort" not in s
    assert "thinking" not in s


def test_the_temperature_policy_is_unchanged_by_a_level():
    """Gemini must stay pinned at 1.0 (CLAUDE.md gotcha #5) regardless."""
    s = build_model_settings(
        _model("vertex_ai.gemini-3.5-flash"), thinking_level="high",
    )
    assert s["temperature"] == 1.0


def test_openai_cache_params_survive_alongside_a_level():
    s = build_model_settings(
        _model("openai.gpt-5.4"), cache_key="xbrl-face-SOFP",
        thinking_level="high",
    )
    assert s["openai_prompt_cache_key"] == "xbrl-face-SOFP"
    assert s["openai_prompt_cache_retention"] == "24h"
    assert s["openai_reasoning_effort"] == "high"


# --------------------------------------------------------------------------
# every role is wired
# --------------------------------------------------------------------------

def test_every_agent_role_can_be_configured(monkeypatch):
    levels = {role: "low" for role in server._AGENT_ROLES}
    monkeypatch.setenv("XBRL_THINKING_LEVELS", json.dumps(levels))
    for role in server._AGENT_ROLES:
        assert server.thinking_level_for(role) == "low", role


@pytest.mark.parametrize("module,role", [
    ("extraction.agent", "SOFP"),
    ("notes.agent", "corporate_info"),
    ("scout.agent", "scout"),
    ("correction.reviewer_agent", "reviewer"),
    ("notes.reviewer_agent", "notes_reviewer"),
    ("notes.formatting_agent", "notes_formatter"),
    ("scout.notes_discoverer_vision", "scout"),
])
def test_each_agent_module_resolves_its_role(module, role, monkeypatch):
    """A factory that never calls the resolver would silently ignore the
    setting — the defect this whole file exists to prevent."""
    import importlib

    mod = importlib.import_module(module)
    monkeypatch.setenv("XBRL_THINKING_LEVELS", json.dumps({role: "high"}))
    assert mod._thinking_level_for(role) == "high"


def test_a_resolver_failure_degrades_to_none(monkeypatch):
    """A settings lookup must never fail a run."""
    import extraction.agent as ea

    monkeypatch.setattr(
        server, "thinking_level_for",
        lambda _r: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert ea._thinking_level_for("SOFP") is None
