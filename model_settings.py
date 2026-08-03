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

import logging
import os
from typing import Any

from pydantic_ai.settings import ModelSettings

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Thinking level (reasoning effort)
#
# Never set before this: every model ran at whatever its provider defaults to.
#
# We set pydantic-ai's UNIFIED `thinking` field and let it translate. It maps
# the same four words into each provider's own form, and it is
# model-profile-aware in ways a hand-rolled table is not — an adaptive Claude
# gets `{'type': 'adaptive'}` rather than a budget, and a Gemini that supports
# thinking LEVELS gets a level rather than a token count.
#
# The first version of this did roll its own per-provider mapping, and put a
# dict into `thinking`, whose declared type is `bool | minimal|low|medium|
# high|xhigh`. On a budget-based Claude that crashes in
# ANTHROPIC_THINKING_BUDGET_MAP[thinking] with "unhashable type: dict"; on an
# adaptive Claude it silently enables thinking and ignores the level, because
# any non-empty dict is truthy (peer review, 2026-08-01).
#
# The proxy path is covered by the same field: pydantic-ai's OpenAI translator
# turns the unified value into the `reasoning_effort` body parameter, which is
# exactly what LiteLLM reads to set a thinking budget for a proxied Gemini or
# Claude.
#
# `None` means "send nothing", which is today's behaviour byte for byte. That
# is the default, and it keeps this inert until somebody picks a level.
# ---------------------------------------------------------------------------

# Deliberately a subset of pydantic-ai's vocabulary: `xhigh` is omitted because
# it is not supported across all three providers we route to.
#
# `none` was added 2026-08-01 (peer review). It is not a pydantic-ai thinking
# level — it is the OpenAI reasoning-effort value that GPT-5.6 requires when
# function tools go over Chat Completions, and it is the only way to express
# "reasoning off" now that omitting the field means "provider default", which
# on GPT-5.6 is `medium`. It is translated per provider in
# `build_model_settings`; it never reaches Anthropic or Google as a literal.
THINKING_LEVELS = ("none", "minimal", "low", "medium", "high")

# GPT-5.6 dropped `minimal` from its reasoning-effort vocabulary and added
# `none`/`xhigh`/`max`. Sending `minimal` to a 5.6-family model is a value the
# model does not list, so fold it to the nearest thing that exists.
_GPT_56_PLUS_MARKERS = ("gpt-5.6", "gpt-5-6")


def _is_gpt_56_plus(model_name: str) -> bool:
    name = (model_name or "").lower()
    return any(m in name for m in _GPT_56_PLUS_MARKERS)


def supported_thinking_levels(model_name: str) -> tuple[str, ...]:
    """The levels THIS model actually accepts.

    `minimal` is the only divergence today: GPT-5.6 lists `none`, `low`,
    `medium`, `high`, `xhigh` and `max`, and dropped `minimal`. Every other
    model we route to accepts the full set — `none` is universally
    expressible (OpenAI takes it as a literal, Anthropic and Google as
    `thinking=False`).

    Exposed through `/api/settings` so the picker cannot offer a level the
    selected model will not honour.
    """
    if _is_gpt_56_plus(model_name):
        return tuple(lvl for lvl in THINKING_LEVELS if lvl != "minimal")
    return THINKING_LEVELS


# What to use when a configured level is not in the model's vocabulary. The
# mapping preserves INTENT: `minimal` means "the least reasoning available",
# which is `low` on a model without `minimal` — NOT `none`, which means no
# reasoning at all. Folding it to `none` inverted the operator's choice and
# silently disabled reasoning (peer review, 2026-08-02).
_LEVEL_FALLBACK = {"minimal": "low"}


def _openai_reasoning_effort(model_name: str, level: str) -> str:
    """Translate an operator-chosen level into a value THIS model accepts.

    A substitution is logged, never silent — the operator picked a level and
    is entitled to know it was not the one used.
    """
    if level in supported_thinking_levels(model_name):
        return level
    substitute = _LEVEL_FALLBACK.get(level, "low")
    logger.warning(
        "Model %r does not support thinking level %r; using %r. Supported: %s.",
        model_name, level, substitute,
        ", ".join(supported_thinking_levels(model_name)),
    )
    return substitute


def _unified_thinking(level: str) -> Any:
    """Translate a level into pydantic-ai's unified `thinking` field.

    `none` is an OpenAI reasoning-effort STRING, not a pydantic-ai level —
    `ANTHROPIC_THINKING_BUDGET_MAP` has no such key, so passing it through
    raises. pydantic-ai spells "reasoning off" as `False`, which both the
    Anthropic and Google paths check for explicitly before any map lookup.
    """
    return False if level == "none" else level


# Prompt-cache lifetime. The two request shapes accept DIFFERENT vocabularies
# and are not interchangeable (peer review, 2026-08-02):
#
#   prompt_cache_retention   (legacy)  — "in_memory" | "24h"; GPT-5.5 and
#                                        5.5-pro accept only "24h"
#   prompt_cache_options.ttl (5.6+)    — "30m" is the ONLY supported value,
#                                        and is also the default
#
# Sending "24h" as a `ttl` is a 400 on every request, so these must stay two
# constants. The 5.6 window is shorter than the legacy one; 30m still spans
# the agents within a single run, which is where the reuse actually is.
CACHE_RETENTION = "24h"
CACHE_OPTIONS_TTL = "30m"


def _openai_cache_settings(model_name: str) -> dict[str, Any]:
    """Prompt-cache parameters in the shape THIS model expects.

    GPT-5.6 deprecates the flat `prompt_cache_retention` field in favour of a
    `prompt_cache_options` object carrying a `ttl`. pydantic-ai 2.9.0 has no
    typed field for the new shape, so it goes through `extra_body`.

    OpenAI's own migration note says to "verify the live docs before rewriting
    it", and the deprecated field still works, so the new shape is OPT-IN:
    set `XBRL_OPENAI_CACHE_OPTIONS=1` once the request shape is confirmed
    against a live 5.6 call. Default behaviour is byte-identical to before.
    """
    if _is_gpt_56_plus(model_name) and os.environ.get(
        "XBRL_OPENAI_CACHE_OPTIONS", ""
    ).strip() in ("1", "true", "yes"):
        return {"extra_body": {"prompt_cache_options": {"ttl": CACHE_OPTIONS_TTL}}}
    return {"openai_prompt_cache_retention": CACHE_RETENTION}


def use_responses_api(model_name: str, proxy_url: str = "") -> bool:
    """Should this OpenAI model be created as an `OpenAIResponsesModel`?

    OpenAI's GPT-5.6 guidance is explicit: "Use the Responses API for
    reasoning, tool-calling, and multi-turn workflows", and function tools on
    Chat Completions are compatible only with effective reasoning `none`.
    Every agent in this repo is a multi-turn tool caller, so on GPT-5.6 the
    Chat Completions transport costs the model its reasoning.

    Scope is deliberately narrow:

    - Only the 5.6 family. `gpt-5.4` works on Chat Completions today and this
      is not the change to disturb it with.
    - Only the DIRECT OpenAI path by default. The enterprise LiteLLM proxy may
      not expose `/v1/responses`, and defaulting it on there would break every
      Windows run to fix a model nobody has run yet. Set
      `XBRL_OPENAI_RESPONSES=1` to enable it on the proxy once confirmed, or
      `=0` to force the old transport everywhere.
    """
    override = os.environ.get("XBRL_OPENAI_RESPONSES", "").strip().lower()
    if override in ("0", "false", "no"):
        return False
    if override in ("1", "true", "yes"):
        return True
    if not _is_gpt_56_plus(model_name):
        return False
    return not proxy_url


def normalize_thinking_level(value: Any) -> str | None:
    """Accept a level or return None. Unknown values fail to None — a typo in
    Settings must not start sending an effort the provider will reject."""
    if not value:
        return None
    level = str(value).strip().lower()
    return level if level in THINKING_LEVELS else None


def build_model_settings(
    model: Any,
    *,
    cache_key: str | None = None,
    temperature: float | None = None,
    thinking_level: str | None = None,
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
    level = normalize_thinking_level(thinking_level)

    if type_name == "AnthropicModel":
        # Direct Anthropic. Cache the two stable blocks; the default 5m TTL
        # comfortably covers a single agent's multi-turn loop.
        from pydantic_ai.models.anthropic import AnthropicModelSettings

        kwargs: dict[str, Any] = {
            "temperature": temperature,
            "anthropic_cache_instructions": True,
            "anthropic_cache_tool_definitions": True,
        }
        if level:
            kwargs["thinking"] = _unified_thinking(level)
        return AnthropicModelSettings(**kwargs)

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

            model_name = getattr(model, "model_name", "") or ""
            settings: dict[str, Any] = {"temperature": temperature}
            settings.update(_openai_cache_settings(model_name))
            if cache_key:
                settings["openai_prompt_cache_key"] = cache_key

            effort = _openai_reasoning_effort(model_name, level) if level else None
            # GPT-5.6: "function tools in Chat Completions are compatible only
            # with effective reasoning `none`" (OpenAI migration guide). Every
            # agent here uses function tools, and OMITTING the field is not
            # neutral — 5.6 then defaults to `medium`, which is the
            # incompatible case. So on the Chat Completions transport the
            # effort is pinned to `none` and the operator's choice is honoured
            # only on the Responses transport (see `use_responses_api`).
            if type_name != "OpenAIResponsesModel" and _is_gpt_56_plus(model_name):
                if effort not in (None, "none"):
                    logger.warning(
                        "Model %r is on Chat Completions, where GPT-5.6 function "
                        "tools require reasoning 'none'; ignoring the configured "
                        "level %r. Enable the Responses API (XBRL_OPENAI_RESPONSES=1) "
                        "to use reasoning with tools.", model_name, effort,
                    )
                effort = "none"
            if effort:
                settings["openai_reasoning_effort"] = effort
            return OpenAIChatModelSettings(**settings)

        # Proxy-routed Gemini / Claude arrive here as OpenAIChatModel. They
        # cannot take the OpenAI-only cache params (the existing guard above),
        # but `reasoning_effort` IS a unified LiteLLM parameter — the proxy
        # translates it into each provider's own thinking budget. So the level
        # still reaches them, using the one field the wire format carries.
        if level:
            from pydantic_ai.models.openai import OpenAIChatModelSettings

            return OpenAIChatModelSettings(
                temperature=temperature, openai_reasoning_effort=level,
            )
        return ModelSettings(temperature=temperature)

    # Direct GoogleModel — the unified field again. pydantic-ai picks
    # thinking_level or thinking_budget from the model's own profile, which a
    # fixed budget table here would get wrong on half the Gemini tiers.
    if type_name == "GoogleModel" and level:
        return ModelSettings(temperature=temperature, thinking=_unified_thinking(level))

    # Bare string / unknown — implicit caching only, and no level to attach to
    # a model we cannot classify.
    return ModelSettings(temperature=temperature)
