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


def test_gemini_on_local_proxy_routes_direct_to_google(monkeypatch):
    """Gemini-3 can't round-trip thought_signatures through the OpenAI proxy.

    On the local-dev proxy, a Gemini model must bypass the proxy and use
    pydantic-ai's native GoogleModel (which preserves the signature). Pinned
    so the proxy path is never silently restored for Gemini.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "real-gemini-key")

    model = server._create_proxy_model(
        "vertex_ai.gemini-3-flash-preview",
        proxy_url="http://localhost:4000/v1",
        api_key="sk-local-dev-key",
    )
    assert type(model).__module__ == "pydantic_ai.models.google"
    # Bare name reaches the constructor, not the prefixed registry id.
    assert getattr(model, "model_name", "") == "gemini-3-flash-preview"


def test_gemini_on_enterprise_proxy_stays_on_proxy(monkeypatch):
    """The remote enterprise proxy must NOT be bypassed — direct Google is
    firewall-blocked (403) on Windows. Gemini there stays on OpenAIChatModel."""
    monkeypatch.setenv("GEMINI_API_KEY", "real-gemini-key")

    model = server._create_proxy_model(
        "vertex_ai.gemini-3-flash-preview",
        proxy_url="https://genai-sharedservice-emea.pwc.com/v1",
        api_key="enterprise-key",
    )
    assert type(model).__module__ == "pydantic_ai.models.openai"


def test_gemini_on_local_proxy_without_key_falls_back_to_proxy(monkeypatch):
    """No real Google key (neither GEMINI_API_KEY nor GOOGLE_API_KEY) → no
    direct path available, stay on the proxy."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    model = server._create_proxy_model(
        "vertex_ai.gemini-3-flash-preview",
        proxy_url="http://localhost:4000/v1",
        api_key="sk-local-dev-key",
    )
    assert type(model).__module__ == "pydantic_ai.models.openai"


def test_gemini_bypass_uses_google_api_key_when_gemini_unset(monkeypatch):
    """The Settings UI writes the user's key to GOOGLE_API_KEY, so the local
    bypass must accept it — not only GEMINI_API_KEY. Pins the HIGH peer-review
    fix: reading GEMINI_API_KEY alone silently broke the default Mac flow."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "real-google-key")

    model = server._create_proxy_model(
        "vertex_ai.gemini-3-flash-preview",
        proxy_url="http://localhost:4000/v1",
        api_key="sk-local-dev-key",
    )
    assert type(model).__module__ == "pydantic_ai.models.google"


def test_proxy_auth_prefers_llm_proxy_api_key(monkeypatch):
    """On the local proxy, OpenAI-routed calls authenticate with
    LLM_PROXY_API_KEY (the proxy master key), leaving GOOGLE_API_KEY free to
    carry the user's real Google key."""
    monkeypatch.setenv("LLM_PROXY_API_KEY", "sk-local-dev-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "real-google-key")

    model = server._create_proxy_model(
        "openai.gpt-5.4",
        proxy_url="http://localhost:4000/v1",
        api_key="real-google-key",
    )
    assert type(model).__module__ == "pydantic_ai.models.openai"
    # The provider's client must carry the proxy master key, not the Google key.
    api_key = getattr(model.client, "api_key", None)
    assert api_key == "sk-local-dev-key"


def test_openai_on_local_proxy_stays_on_proxy(monkeypatch):
    """The bypass is Gemini-only — OpenAI models keep using the proxy."""
    monkeypatch.setenv("GEMINI_API_KEY", "real-gemini-key")

    model = server._create_proxy_model(
        "openai.gpt-5.4",
        proxy_url="http://localhost:4000/v1",
        api_key="sk-local-dev-key",
    )
    assert type(model).__module__ == "pydantic_ai.models.openai"


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
