"""build_model_settings — provider-correct prompt-cache settings (PLAN Phase 2).

The helper picks the caching mechanism from the model's TYPE (which is what
``_create_proxy_model`` hands the agent constructors). We test the dispatch with
lightweight stand-ins whose class ``__name__`` matches the real model classes —
that exercises the exact branch logic without needing provider API keys — and
assert the returned settings carry the right flags. Temperature stays pinned at
1.0 on every branch (the Gemini-through-proxy constraint; PLAN Phase 9 revisits).
"""
from __future__ import annotations

from model_settings import build_model_settings, PINNED_TEMPERATURE


class AnthropicModel:  # noqa: D401 — stand-in matching the real class name
    pass


class OpenAIChatModel:
    pass


class GoogleModel:
    pass


def test_anthropic_caches_instructions_and_tools():
    s = build_model_settings(AnthropicModel(), cache_key="ignored-on-anthropic")
    assert s["temperature"] == PINNED_TEMPERATURE
    assert s["anthropic_cache_instructions"] is True
    assert s["anthropic_cache_tool_definitions"] is True
    # cache_key is an OpenAI-only concept — it must NOT leak onto Anthropic.
    assert "openai_prompt_cache_key" not in s


def test_openai_sets_cache_key_and_retention():
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


def test_bare_string_model_falls_back_to_plain_settings():
    # A caller may hand pydantic-ai a model name string; we must not crash and
    # must not attach provider-specific cache keys we can't be sure apply.
    s = build_model_settings("openai.gpt-5.4", cache_key="xbrl-scout")
    assert s["temperature"] == PINNED_TEMPERATURE
    assert "openai_prompt_cache_key" not in s
    assert "anthropic_cache_instructions" not in s


def test_temperature_override_is_honored():
    # Phase 9 will lower temperature off Gemini — confirm the seam works now.
    s = build_model_settings(OpenAIChatModel(), cache_key="k", temperature=0.0)
    assert s["temperature"] == 0.0
