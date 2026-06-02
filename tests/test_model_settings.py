"""build_model_settings — provider-correct prompt-cache settings (PLAN Phase 2).

The helper picks the caching mechanism from the model's TYPE (which is what
``_create_proxy_model`` hands the agent constructors). We test the dispatch with
lightweight stand-ins whose class ``__name__`` matches the real model classes —
that exercises the exact branch logic without needing provider API keys — and
assert the returned settings carry the right flags. Temperature stays pinned at
1.0 on every branch (the Gemini-through-proxy constraint; PLAN Phase 9 revisits).
"""
from __future__ import annotations

from model_settings import build_model_settings, PINNED_TEMPERATURE, LOWERED_TEMPERATURE


class AnthropicModel:  # noqa: D401 — stand-in matching the real class name
    model_name = "claude-sonnet-4-6"


class OpenAIChatModel:
    """Stand-in for the proxy/direct OpenAI-compatible model. ``model_name``
    is what _resolved_provider classifies on (peer-review F1)."""
    def __init__(self, model_name: str = "gpt-5.4"):
        self.model_name = model_name


class GoogleModel:
    model_name = "gemini-3-flash-preview"


def test_anthropic_caches_instructions_and_tools():
    s = build_model_settings(AnthropicModel(), cache_key="ignored-on-anthropic")
    # Phase 9: Anthropic accepts a non-default temperature → lowered.
    assert s["temperature"] == LOWERED_TEMPERATURE
    assert s["anthropic_cache_instructions"] is True
    assert s["anthropic_cache_tool_definitions"] is True
    # cache_key is an OpenAI-only concept — it must NOT leak onto Anthropic.
    assert "openai_prompt_cache_key" not in s


def test_openai_sets_cache_key_and_retention():
    # Default fixture model is gpt-5.4 — an OpenAI reasoning model, which
    # rejects a non-default temperature, so it stays pinned at 1.0 (Phase 9).
    s = build_model_settings(OpenAIChatModel(), cache_key="xbrl-face-SOFP")
    assert s["temperature"] == PINNED_TEMPERATURE
    assert s["openai_prompt_cache_key"] == "xbrl-face-SOFP"
    assert s["openai_prompt_cache_retention"] == "24h"
    # Anthropic flags must NOT leak onto the OpenAI path.
    assert "anthropic_cache_instructions" not in s


def test_openai_without_cache_key_still_sets_retention():
    s = build_model_settings(OpenAIChatModel())
    assert s["openai_prompt_cache_retention"] == "24h"
    assert "openai_prompt_cache_key" not in s


def test_google_gets_plain_settings_no_cache_flags():
    s = build_model_settings(GoogleModel(), cache_key="xbrl-face-SOFP")
    assert s["temperature"] == PINNED_TEMPERATURE
    assert "openai_prompt_cache_key" not in s
    assert "anthropic_cache_instructions" not in s


def test_proxy_routed_anthropic_does_not_get_openai_cache_params():
    """Peer-review F1: Claude wrapped as OpenAIChatModel by the enterprise proxy
    (model_name='bedrock.anthropic.claude-...') must NOT receive OpenAI-only
    cache params — the proxy could reject them. It falls back to plain settings
    (its caching is the documented Step 2.2 gap)."""
    s = build_model_settings(
        OpenAIChatModel("bedrock.anthropic.claude-sonnet-4-6"),
        cache_key="xbrl-face-SOFP",
    )
    assert "openai_prompt_cache_key" not in s
    assert "openai_prompt_cache_retention" not in s
    # Phase 9: classified as anthropic by model id → lowered temperature.
    assert s["temperature"] == LOWERED_TEMPERATURE


def test_proxy_routed_gemini_does_not_get_openai_cache_params():
    """Peer-review F1: Gemini wrapped as OpenAIChatModel by the enterprise proxy
    (model_name='vertex_ai.gemini-...') must NOT receive OpenAI cache params."""
    s = build_model_settings(
        OpenAIChatModel("vertex_ai.gemini-3.5-flash"),
        cache_key="xbrl-scout",
    )
    assert "openai_prompt_cache_key" not in s
    assert "openai_prompt_cache_retention" not in s


def test_openai_via_registry_prefix_still_gets_cache_params():
    """A proxied OpenAI model id ('openai.gpt-5.4') is still recognised."""
    s = build_model_settings(
        OpenAIChatModel("openai.gpt-5.4"), cache_key="xbrl-face-SOFP"
    )
    assert s["openai_prompt_cache_key"] == "xbrl-face-SOFP"


def test_bare_string_model_falls_back_to_plain_settings():
    # A caller may hand pydantic-ai a model name string; we must not crash and
    # must not attach provider-specific cache keys we can't be sure apply.
    s = build_model_settings("openai.gpt-5.4", cache_key="xbrl-scout")
    assert s["temperature"] == PINNED_TEMPERATURE
    assert "openai_prompt_cache_key" not in s
    assert "anthropic_cache_instructions" not in s


def test_temperature_override_is_honored():
    # An explicit temperature always wins over the provider-aware default.
    s = build_model_settings(OpenAIChatModel(), cache_key="k", temperature=0.0)
    assert s["temperature"] == 0.0


# ---------------------------------------------------------------------------
# Phase 9 — provider-aware temperature defaults.
# ---------------------------------------------------------------------------


def test_openai_reasoning_models_keep_pinned_temperature():
    """o-series and gpt-5.x reject a non-default temperature → stay at 1.0,
    direct and proxy-prefixed."""
    for name in ("gpt-5.4", "openai.gpt-5.4", "o3-mini", "openai.o1-preview"):
        s = build_model_settings(OpenAIChatModel(name), cache_key="k")
        assert s["temperature"] == PINNED_TEMPERATURE, name


def test_openai_non_reasoning_models_get_lowered_temperature():
    """Standard OpenAI chat models accept a lower temperature for less
    numeric-extraction jitter."""
    for name in ("gpt-4o", "openai.gpt-4o-mini", "gpt-4-turbo"):
        s = build_model_settings(OpenAIChatModel(name), cache_key="k")
        assert s["temperature"] == LOWERED_TEMPERATURE, name
        # Lowering must not disturb the OpenAI cache params.
        assert s["openai_prompt_cache_key"] == "k", name


def test_gemini_stays_pinned_at_one():
    """Gemini-3-through-proxy requires 1.0 (CLAUDE.md Temperature Constraint)
    — both direct and proxy-routed."""
    assert build_model_settings(GoogleModel())["temperature"] == PINNED_TEMPERATURE
    proxied = build_model_settings(OpenAIChatModel("vertex_ai.gemini-3.5-flash"))
    assert proxied["temperature"] == PINNED_TEMPERATURE
