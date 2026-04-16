"""Tests for `_create_proxy_model` + `_detect_provider`.

Peer-review regression: the provider-detection helper compared against bare
prefixes (`gpt-`, `claude-`), but the actual registry IDs in
`config/models.json` are fully qualified (`openai.gpt-5.4`,
`bedrock.anthropic.claude-sonnet-4-6`, `vertex_ai.gemini-3-flash-preview`).
In direct mode (proxy_url=""), those IDs fell into the Google branch and
either misrouted or failed. Direct mode was only saved in practice because
the local LiteLLM proxy is usually on.

These tests exercise each provider's prefixed form with proxy_url="" to
confirm the right model class is constructed.
"""
from __future__ import annotations

import pytest

import server


@pytest.mark.parametrize(
    "model_name,expected_provider",
    [
        # Registry IDs from config/models.json — the actual strings users see.
        ("openai.gpt-5.4", "openai"),
        ("openai.gpt-5.4-mini", "openai"),
        ("bedrock.anthropic.claude-sonnet-4-6", "anthropic"),
        ("bedrock.anthropic.claude-opus-4-6", "anthropic"),
        ("vertex_ai.gemini-3-flash-preview", "google"),
        ("vertex_ai.gemini-3.1-pro-preview", "google"),
        # Bare names (legacy callers / CLI direct) should still detect.
        ("gpt-5.4", "openai"),
        ("claude-sonnet-4-6", "anthropic"),
        ("gemini-3-flash-preview", "google"),
        # PydanticAI namespaced form.
        ("google-gla:gemini-3-flash-preview", "google"),
    ],
)
def test_detect_provider_recognizes_registry_ids(model_name, expected_provider):
    assert server._detect_provider(model_name) == expected_provider


# pydantic-ai's Anthropic integration requires a compatible anthropic SDK
# version; skip when the install chain can't be loaded in this environment.
_has_anthropic = True
try:  # pragma: no cover — depends on env
    from pydantic_ai.models.anthropic import AnthropicModel  # noqa: F401
    from pydantic_ai.providers.anthropic import AnthropicProvider  # noqa: F401
    AnthropicProvider(api_key="probe")
except Exception:
    _has_anthropic = False


@pytest.mark.parametrize(
    "model_name,expected_module",
    [
        ("openai.gpt-5.4", "pydantic_ai.models.openai"),
        pytest.param(
            "bedrock.anthropic.claude-sonnet-4-6",
            "pydantic_ai.models.anthropic",
            marks=pytest.mark.skipif(
                not _has_anthropic,
                reason="anthropic SDK not installed in this environment",
            ),
        ),
        ("vertex_ai.gemini-3-flash-preview", "pydantic_ai.models.google"),
    ],
)
def test_create_proxy_model_direct_mode_routes_to_correct_provider(
    model_name, expected_module, monkeypatch
):
    """In direct mode (proxy_url=""), each registry ID must construct a
    model of the right provider's class — not fall through to Google."""
    # Ensure every provider has credentials available so we get past the
    # "key not set" guards in each branch.
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")

    model = server._create_proxy_model(model_name, proxy_url="", api_key="test-gemini")
    assert type(model).__module__ == expected_module


def test_create_proxy_model_strips_prefix_before_construction(monkeypatch):
    """The model constructor should receive a bare model name, not the
    prefixed registry form — otherwise the upstream API gets a string it
    doesn't recognise."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")

    model = server._create_proxy_model(
        "openai.gpt-5.4", proxy_url="", api_key="test-key"
    )
    # OpenAIChatModel stores the id on model_name (see _model_id helper).
    assert getattr(model, "model_name", "") == "gpt-5.4"
