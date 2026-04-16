"""Model-aware pricing lookup from config/models.json."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "models.json"

# Loaded lazily on first call, then cached.
_pricing_cache: dict[str, Tuple[float, float]] | None = None
_load_failed: bool = False


def _load_pricing() -> dict[str, Tuple[float, float]]:
    """Load pricing from models.json into {model_id: (input_price, output_price)}.

    Returns an empty dict (and logs once) if the file is missing or malformed,
    so that pricing failures never abort an extraction run.
    """
    global _pricing_cache, _load_failed
    if _pricing_cache is not None:
        return _pricing_cache

    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            models = json.load(f)

        _pricing_cache = {}
        for m in models:
            _pricing_cache[m["id"]] = (
                m["input_price_per_mtok"],
                m["output_price_per_mtok"],
            )
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        if not _load_failed:
            logger.warning("Failed to load pricing from %s: %s", _CONFIG_PATH, exc)
            _load_failed = True
        _pricing_cache = {}

    return _pricing_cache


def _resolve_model_name(model) -> str:
    """Extract model name string from a PydanticAI model object or plain string."""
    if isinstance(model, str):
        return model
    # PydanticAI model objects (OpenAIChatModel, GoogleModel, AnthropicModel)
    # all expose .model_name
    return getattr(model, "model_name", str(model))


def _normalize(name: str) -> str:
    """Strip known prefixes (e.g. 'vertex_ai.', 'google-gla:') for matching."""
    for prefix in ("vertex_ai.", "google-gla:", "google-vertex:"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def get_model_pricing(model) -> Tuple[float, float]:
    """Return (input_price_per_mtok, output_price_per_mtok) for a model.

    Tries exact match first, then normalized (prefix-stripped) match.
    Returns (0, 0) with a warning if no match is found.
    """
    pricing = _load_pricing()
    if not pricing:
        return (0.0, 0.0)

    name = _resolve_model_name(model)

    # Exact match
    if name in pricing:
        return pricing[name]

    # Normalized match (strip proxy prefixes)
    norm = _normalize(name)
    for model_id, prices in pricing.items():
        if _normalize(model_id) == norm:
            return prices

    logger.warning("No pricing found for model '%s' — cost estimate will be $0", name)
    return (0.0, 0.0)


def estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    thinking_tokens: int,
    model,
) -> float:
    """Calculate estimated cost in USD.

    Thinking tokens (Claude extended-thinking, OpenAI reasoning) are billed
    as OUTPUT by the provider, not input. Charging them at the input rate
    materially undercounts cost for Claude/GPT-5 reasoning runs (fix for
    peer-review finding C5).
    """
    input_price, output_price = get_model_pricing(model)
    input_cost = (prompt_tokens / 1_000_000) * input_price
    output_cost = ((completion_tokens + thinking_tokens) / 1_000_000) * output_price
    return input_cost + output_cost
