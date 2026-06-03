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

Temperature is provider-aware (PLAN Phase 9). Gemini stays pinned at 1.0 —
Gemini-3-through-proxy requires it (the "Temperature Constraint" rule in
CLAUDE.md). Anthropic and *non-reasoning* OpenAI chat models drop to a
lower, lower-variance temperature. OpenAI reasoning models (o-series and
gpt-5.x — including the default ``gpt-5.4``) reject a non-default
temperature, so they keep 1.0; unknown / bare-string models keep 1.0 too
(safe default). A caller may still pass an explicit ``temperature=`` to
override the resolved default. This module is the single place the policy
lives.

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
# and CLAUDE.md "Temperature Constraint"). Also the safe default for OpenAI
# reasoning models (which reject non-default temperature) and unknown models.
PINNED_TEMPERATURE = 1.0

# Lower, lower-variance temperature for providers/models that accept it
# (Anthropic + non-reasoning OpenAI chat). Numeric extraction benefits from
# less sampling jitter; we stay above 0.0 to avoid degenerate loops.
LOWERED_TEMPERATURE = 0.2

# OpenAI model-id markers for the reasoning family (o1/o3/o4 + gpt-5.x).
# These reject a non-default temperature, so they keep PINNED_TEMPERATURE.
# Checked as substrings against the (proxy-prefixed) lowercased model id, so
# "openai.gpt-5.4" and a bare "o3-mini" both match.
_OPENAI_REASONING_MARKERS = ("o1-", "o3-", "o4-", "gpt-5")


def _default_temperature(model: Any) -> float:
    """Resolve the provider-aware default temperature for ``model``.

    Gemini → 1.0 (required). Anthropic → lowered. OpenAI → lowered UNLESS it
    is a reasoning model (o-series / gpt-5.x), which keeps 1.0 because those
    reject a non-default temperature. Unknown / bare-string → 1.0 (safe).
    """
    provider = _resolved_provider(model)
    if provider == "google":
        return PINNED_TEMPERATURE
    if provider == "anthropic":
        return LOWERED_TEMPERATURE
    if provider == "openai":
        name = (getattr(model, "model_name", "") or "").lower()
        if any(m in name for m in _OPENAI_REASONING_MARKERS):
            return PINNED_TEMPERATURE
        return LOWERED_TEMPERATURE
    return PINNED_TEMPERATURE


def classify_provider(model_name: str) -> str:
    """Classify a registry model-id STRING into its provider family.

    Returns 'openai' | 'anthropic' | 'google' | 'unknown'. Pure string logic
    (no pydantic-ai import needed), so a standalone diagnostic that only has a
    model name — e.g. ``scripts/cache_report.py`` reading the audit DB — shares
    exactly this classification instead of re-implementing it.

    Order matters: anthropic/google markers (incl. their proxy prefixes
    ``vertex_ai.*`` / ``bedrock.anthropic.*``) are checked before the OpenAI
    markers.
    """
    name = (model_name or "").lower()
    if not name:
        return "unknown"
    if any(k in name for k in ("vertex_ai", "gemini", "google")):
        return "google"
    if any(k in name for k in ("anthropic", "claude", "bedrock")):
        return "anthropic"
    if name.startswith(("gpt-", "o1-", "o3-", "o4-")) or "openai" in name:
        return "openai"
    return "unknown"


def _resolved_provider(model: Any) -> str:
    """Best-effort provider of the *underlying* model, even when the Python
    type is ``OpenAIChatModel`` because of proxy routing.

    The enterprise proxy wraps EVERY model — including ``vertex_ai.*`` (Gemini)
    and ``bedrock.anthropic.*`` (Claude) — as an ``OpenAIChatModel`` pointed at
    the OpenAI-compatible endpoint (server._create_proxy_model). Dispatching on
    the Python type alone would then attach OpenAI-only cache params
    (`prompt_cache_key`) to a Gemini/Claude request, which the proxy may reject
    if it doesn't drop unknown params. So we read the registry model id off
    ``model.model_name`` and classify by it via :func:`classify_provider`.
    """
    return classify_provider(getattr(model, "model_name", "") or "")


def build_model_settings(
    model: Any,
    *,
    cache_key: str | None = None,
    temperature: float | None = None,
) -> ModelSettings:
    """Return cache-enabled, provider-correct ``ModelSettings`` for ``model``.

    ``model`` is the object ``_create_proxy_model`` returned (or a bare string
    when a caller hands a model name straight to pydantic-ai). ``cache_key`` is a
    stable per-agent-type label used only on the OpenAI path for cache-shard
    locality (ignored elsewhere). ``temperature`` defaults to the provider-aware
    value from ``_default_temperature`` (Phase 9); pass an explicit float to
    override. Any model whose type we don't recognise falls back to a plain
    ``ModelSettings(temperature=...)`` — behaviour is never worse than before
    this change.
    """
    if temperature is None:
        temperature = _default_temperature(model)
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

    # OpenAIChatModel is the Python type for direct OpenAI AND every
    # proxy-routed model. Only attach OpenAI-only cache params when the
    # UNDERLYING model is actually OpenAI — otherwise a proxy-routed Gemini/
    # Claude would receive `prompt_cache_key` it can't honour and the proxy may
    # reject (peer-review F1). Proxy-routed Anthropic/Gemini fall through to
    # plain settings (their caching can't be driven from here anyway — the
    # Step 2.2 known gap), which is behaviour-neutral vs. before this change.
    if type_name in ("OpenAIChatModel", "OpenAIModel", "OpenAIResponsesModel"):
        if _resolved_provider(model) == "openai":
            from pydantic_ai.models.openai import OpenAIChatModelSettings

            settings: dict[str, Any] = {
                "temperature": temperature,
                # Extend retention past the default few minutes so reuse
                # survives across the agents in a run (and short gaps between).
                "openai_prompt_cache_retention": "24h",
            }
            if cache_key:
                settings["openai_prompt_cache_key"] = cache_key
            return OpenAIChatModelSettings(**settings)
        return ModelSettings(temperature=temperature)

    # GoogleModel / bare string / unknown — implicit caching only.
    return ModelSettings(temperature=temperature)
