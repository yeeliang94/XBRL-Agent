"""Provider-aware ModelSettings builder — PLAN Phase 2 (prompt caching).

The static system prompt + tool definitions are byte-identical across every
turn of an agent, so caching them turns the dominant ``T*S`` cost term (the
prompt re-encoded across T turns) into one full charge plus cheap cache reads.
The MECHANISM differs by provider, and pydantic-ai 1.77 exposes each one as a
provider-specific ``ModelSettings`` subclass — so we pick it from the model
TYPE that ``server._create_proxy_model`` handed us:

- ``AnthropicModel`` (direct mode): Anthropic needs explicit cache breakpoints.
  We cache the instructions (system prompt) and the tool definitions — the two
  stable, re-sent-every-turn blocks.
- ``OpenAIChatModel`` (direct OpenAI **and** the proxy/LiteLLM path, which wraps
  every proxied model as an OpenAI-compatible client): OpenAI already
  auto-caches >1024-token prefixes server-side; we set ``prompt_cache_key`` for
  cache-shard locality (an agent-type's requests share a shard) and 24h
  retention to extend reuse beyond the few-minute default.
- ``GoogleModel`` / anything else (incl. bare-string models): implicit caching
  only — return a plain ``ModelSettings`` unchanged from today.

Temperature stays pinned at 1.0 here — Gemini-3-through-proxy requires it (the
"Temperature Constraint" rule in CLAUDE.md). PLAN Phase 9 will make it
provider-aware; this module is the single place that change will land.

KNOWN GAP (PLAN Step 2.2): Claude routed through the proxy arrives as an
``OpenAIChatModel``, so it takes the OpenAI branch and the ``anthropic_cache_*``
flags never apply. Caching Anthropic via LiteLLM needs ``cache_control`` markers
the OpenAI wire format can't carry from here; that path stays uncached until the
proxy itself injects them. The default model is OpenAI, so this is not the
common path — but it is why the fix is two branches, not one.
"""
from __future__ import annotations

from typing import Any

from pydantic_ai.settings import ModelSettings

# Pinned temperature (Gemini-3-through-proxy requires 1.0; see module docstring
# and CLAUDE.md "Temperature Constraint"). PLAN Phase 9 makes this per-provider.
PINNED_TEMPERATURE = 1.0


def build_model_settings(
    model: Any,
    *,
    cache_key: str | None = None,
    temperature: float = PINNED_TEMPERATURE,
) -> ModelSettings:
    """Return cache-enabled, provider-correct ``ModelSettings`` for ``model``.

    ``model`` is the object ``_create_proxy_model`` returned (or a bare string
    when a caller hands a model name straight to pydantic-ai). ``cache_key`` is a
    stable per-agent-type label used only on the OpenAI path for cache-shard
    locality (ignored elsewhere). Any model whose type we don't recognise falls
    back to a plain ``ModelSettings(temperature=...)`` — behaviour is never worse
    than before this change.
    """
    type_name = type(model).__name__

    if type_name == "AnthropicModel":
        # Direct Anthropic. Cache the two stable blocks; the default 5m TTL
        # comfortably covers a single agent's multi-turn loop.
        from pydantic_ai.models.anthropic import AnthropicModelSettings

        return AnthropicModelSettings(
            temperature=temperature,
            anthropic_cache_instructions=True,
            anthropic_cache_tool_definitions=True,
        )

    # OpenAIChatModel covers direct OpenAI AND every proxy-routed model. The
    # deprecated aliases are matched too so a caller on an older construction
    # path still benefits.
    if type_name in ("OpenAIChatModel", "OpenAIModel", "OpenAIResponsesModel"):
        from pydantic_ai.models.openai import OpenAIChatModelSettings

        settings: dict[str, Any] = {
            "temperature": temperature,
            # Extend retention past the default few minutes so reuse survives
            # across the agents in a run (and short gaps between runs).
            "openai_prompt_cache_retention": "24h",
        }
        if cache_key:
            settings["openai_prompt_cache_key"] = cache_key
        return OpenAIChatModelSettings(**settings)

    # GoogleModel / bare string / unknown — implicit caching only.
    return ModelSettings(temperature=temperature)
