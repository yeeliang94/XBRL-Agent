"""Per-role thinking level (reasoning effort).

Reasoning effort was never set: every agent ran at whatever its provider
defaults to, on every run, since the project started. This adds a per-role
control alongside the per-role model choice that was already there.

The invariant that matters most is the FIRST test: with nothing configured,
the settings this builds are byte-identical to what they were before. A
performance knob that changes behaviour when nobody has touched it is not a
knob, it is a silent migration.

We set pydantic-ai's UNIFIED `thinking` field and let it translate per
provider — it is model-profile-aware in ways a hand-rolled table is not. The
one exception is the proxy path, where an explicit `openai_reasoning_effort`
is set so LiteLLM sees the body parameter it translates from.

The first version rolled its own mapping and put a dict into `thinking`, whose
declared type is `bool | minimal|low|medium|high|xhigh`. These tests asserted
on the dict they had just built, so they agreed with the bug (peer review,
2026-08-01). They now assert against pydantic-ai's own maps.
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


def test_direct_gemini_carries_the_unified_level():
    """These three tests used to assert the hand-rolled DICT this code built,
    so they agreed with the bug rather than catching it. pydantic-ai's own
    translation is the thing worth asserting against."""
    s = build_model_settings(
        _model("gemini-3.5-flash", "GoogleModel"), thinking_level="medium",
    )
    assert s["thinking"] == "medium"


def test_direct_anthropic_carries_the_unified_level():
    s = build_model_settings(
        _model("claude-sonnet-4-6", "AnthropicModel"), thinking_level="high",
    )
    assert s["thinking"] == "high"
    assert s["anthropic_cache_instructions"] is True


def test_minimal_is_a_level_not_a_disabled_dict():
    """`{'type': 'disabled'}` is truthy, so on an adaptive Claude every level
    enabled thinking and the choice was ignored."""
    s = build_model_settings(
        _model("claude-sonnet-4-6", "AnthropicModel"), thinking_level="minimal",
    )
    assert s["thinking"] == "minimal"
    assert not isinstance(s["thinking"], dict)


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


# --------------------------------------------------------------------------
# Peer review, 2026-08-01 — the real provider translation
#
# The first version hand-rolled a per-provider mapping and put a DICT into
# `thinking`, whose declared type is `bool | minimal|low|medium|high|xhigh`.
# The tests then asserted on the dict they had just built, so they agreed with
# the bug. These check against pydantic-ai's own translation instead.
# --------------------------------------------------------------------------

def test_the_thinking_value_is_a_type_pydantic_ai_accepts():
    from pydantic_ai.models.anthropic import ANTHROPIC_THINKING_BUDGET_MAP

    for level in THINKING_LEVELS:
        s = build_model_settings(
            _model("claude-haiku-4-5", "AnthropicModel"), thinking_level=level,
        )
        # The exact expression that raised "unhashable type: dict".
        assert ANTHROPIC_THINKING_BUDGET_MAP[s["thinking"]] > 0


def test_each_level_reaches_anthropic_as_a_different_budget():
    """A non-empty dict was truthy, so every level enabled thinking and the
    choice was ignored. Distinct budgets prove the level survives."""
    from pydantic_ai.models.anthropic import ANTHROPIC_THINKING_BUDGET_MAP

    budgets = {
        lvl: ANTHROPIC_THINKING_BUDGET_MAP[
            build_model_settings(
                _model("claude-haiku-4-5", "AnthropicModel"),
                thinking_level=lvl,
            )["thinking"]
        ]
        for lvl in THINKING_LEVELS
    }
    assert len(set(budgets.values())) == len(THINKING_LEVELS), budgets
    assert budgets["minimal"] < budgets["high"]


def test_each_level_reaches_openai_as_a_reasoning_effort():
    from pydantic_ai.models.openai import OPENAI_REASONING_EFFORT_MAP

    for level in THINKING_LEVELS:
        assert OPENAI_REASONING_EFFORT_MAP[level] == level


def test_direct_gemini_uses_the_unified_field_not_a_fixed_budget():
    """pydantic-ai picks thinking_level or thinking_budget from the model's
    own profile. A fixed budget table here got that wrong on half the tiers."""
    s = build_model_settings(
        _model("gemini-3.6-flash", "GoogleModel"), thinking_level="medium",
    )
    assert s["thinking"] == "medium"
    assert "google_thinking_config" not in s


def test_every_level_we_offer_is_one_pydantic_ai_knows():
    from pydantic_ai.models.openai import OPENAI_REASONING_EFFORT_MAP
    from pydantic_ai.models.anthropic import ANTHROPIC_THINKING_BUDGET_MAP

    for level in THINKING_LEVELS:
        assert level in OPENAI_REASONING_EFFORT_MAP
        assert level in ANTHROPIC_THINKING_BUDGET_MAP


# --------------------------------------------------------------------------
# catalogue ↔ routing parity
# --------------------------------------------------------------------------

def test_every_catalogued_model_routes_to_the_right_provider():
    """`openai.global.gpt-5.6` lost only the `openai.` prefix, so the leftover
    `global.gpt-5.6` failed the gpt- check and direct mode built a GoogleModel
    for an OpenAI model."""
    from model_settings import classify_provider

    expected = {"openai": "openai", "google": "google", "anthropic": "anthropic"}
    for m in server._load_available_models():
        assert classify_provider(m["id"]) == expected[m["provider"]], m["id"]


def test_the_prefix_stripper_handles_the_global_segment():
    assert server._strip_provider_prefix("openai.global.gpt-5.6") == "gpt-5.6"
    assert server._strip_provider_prefix("openai.gpt-5.4") == "gpt-5.4"


def test_the_local_proxy_can_serve_every_catalogued_model():
    """config/models.json is shared with the Mac. An entry the local proxy
    does not declare is a model the dropdown offers and the proxy rejects."""
    import yaml

    cfg = yaml.safe_load(open("litellm_config.yaml", encoding="utf-8"))
    served = {m["model_name"] for m in cfg["model_list"]}
    catalogued = {m["id"] for m in server._load_available_models()}
    assert not (catalogued - served), sorted(catalogued - served)


def test_unconfirmed_pricing_is_flagged_rather_than_shown_as_exact():
    from pricing import pricing_is_unconfirmed

    assert pricing_is_unconfirmed("openai.global.gpt-5.6") is True
    assert pricing_is_unconfirmed("openai.gpt-5.4") is False


def test_a_failed_resolution_warns_once_instead_of_failing_silently(monkeypatch, caplog):
    """Swallowing everything left the Settings control looking active while
    doing nothing, with no diagnostic."""
    import logging

    import extraction.agent as ea

    ea._THINKING_WARNED.clear()
    monkeypatch.setattr(
        server, "thinking_level_for",
        lambda _r: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with caplog.at_level(logging.WARNING, logger="server"):
        assert ea._thinking_level_for("SOFP") is None
        assert ea._thinking_level_for("SOFP") is None
    assert sum("thinking level" in r.message for r in caplog.records) == 1
