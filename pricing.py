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

# Fan-out width for Sheet-12 (List of Notes) sub-agents when the model's
# entry in models.json omits ``notes_parallel`` or the model is unknown.
# Fail-open: 5 matches the pre-existing hardcoded width, so adding a new
# model without touching the config keeps today's behaviour.
DEFAULT_NOTES_PARALLEL = 5

# Separate lazy cache from pricing so a malformed pricing entry can't
# take down parallelism lookups (and vice versa).
_parallel_cache: dict[str, int] | None = None
_parallel_load_failed: bool = False

# Upper bound on a configured ``notes_parallel`` value. 10 is already past
# the point where any realistic provider TPM bucket survives a fan-out —
# we cap to keep an errant config (e.g. `999`) from spinning up hundreds
# of sub-agents and blowing through every retry budget at once. The 429
# retry path isn't designed to absorb that kind of burst.
_MAX_NOTES_PARALLEL = 10


def _read_models_json() -> list:
    """Read+parse models.json once. Wrapped so both `_load_pricing` and
    `_load_notes_parallel` share a single I/O + parse pass on the
    cold-cache path (peer-review S3). Returns [] on any failure — callers
    log + memoize the empty result so the warning fires at most once."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def _load_pricing() -> dict[str, Tuple[float, float]]:
    """Load pricing from models.json into {model_id: (input_price, output_price)}.

    Returns an empty dict (and logs once) if the file is missing or malformed,
    so that pricing failures never abort an extraction run.
    """
    global _pricing_cache, _load_failed
    if _pricing_cache is not None:
        return _pricing_cache

    try:
        models = _read_models_json()
        if not models:
            raise OSError(f"models.json missing or unreadable at {_CONFIG_PATH}")

        _pricing_cache = {}
        for m in models:
            _pricing_cache[m["id"]] = (
                m["input_price_per_mtok"],
                m["output_price_per_mtok"],
            )
    except (OSError, KeyError, TypeError) as exc:
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
    """Strip known provider prefixes so registry ids and bare names match.

    The registry (`config/models.json`) uses fully-qualified ids like
    `openai.gpt-5.4-mini` or `bedrock.anthropic.claude-haiku-4-5`, but
    `server._create_proxy_model` strips those prefixes in direct mode
    and constructs the upstream model with the bare name (e.g.
    `gpt-5.4-mini`). Without prefix-stripping here, direct-mode lookups
    fall through to the default and the per-model config is effectively
    ignored — that's the peer-review HIGH this helper exists to prevent.

    Kept in sync with ``server._PROVIDER_PREFIXES`` (server.py is the
    source of truth for provider routing); see CLAUDE.md "Files That
    Must Stay in Sync".
    """
    # Order matters: `bedrock.anthropic.` must be tried before the
    # shorter `bedrock.` prefix, else the short form partially strips
    # and we lose the `anthropic.` segment.
    for prefix in (
        "bedrock.anthropic.",
        "bedrock.",
        "openai.",
        "vertex_ai.",
        "google-gla:",
        "google-vertex:",
    ):
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


# Deduped warnings — same bad config entry can appear in every run in a
# long-running server; we log each unique message at WARNING level once
# per process, then downgrade to DEBUG so the log stream stays clean.
_warned_bad_parallel_keys: set[str] = set()


def _warn_bad_parallel_once(message: str) -> None:
    """Log a bad-config warning at most once per unique message."""
    if message in _warned_bad_parallel_keys:
        logger.debug("notes_parallel config issue (already warned): %s", message)
        return
    _warned_bad_parallel_keys.add(message)
    logger.warning("notes_parallel config issue: %s", message)


def _load_notes_parallel() -> dict[str, int]:
    """Load {model_id: notes_parallel} from models.json.

    Mirrors ``_load_pricing`` — lazy, cached, and failure-tolerant so a
    parse error never aborts a run. Entries missing the field are simply
    omitted from the map; callers fall back to ``DEFAULT_NOTES_PARALLEL``.
    """
    global _parallel_cache, _parallel_load_failed
    if _parallel_cache is not None:
        return _parallel_cache

    try:
        models = _read_models_json()
        if not models:
            raise OSError(f"models.json missing or unreadable at {_CONFIG_PATH}")

        _parallel_cache = {}
        for m in models:
            model_id = m.get("id")
            if not isinstance(model_id, str):
                # A registry entry without a string ``id`` is malformed;
                # skip it so one bad entry doesn't degrade every lookup
                # (peer-review suggestion on KeyError handling).
                _warn_bad_parallel_once(
                    f"skipping registry entry with missing/invalid id: {m!r}"
                )
                continue
            if "notes_parallel" not in m:
                # Missing field is fine — caller defaults to DEFAULT_NOTES_PARALLEL.
                # We only record entries that declared the knob explicitly.
                continue
            raw = m["notes_parallel"]
            # ``bool`` is an ``int`` subclass in Python, so plain
            # ``isinstance(raw, int)`` would accept ``true`` / ``false``
            # from JSON (true coerces to 1). Reject explicitly — a boolean
            # in this field is almost certainly a config typo, not intent.
            if isinstance(raw, bool) or not isinstance(raw, int):
                _warn_bad_parallel_once(
                    f"{model_id}: notes_parallel={raw!r} is not an int — "
                    f"falling back to default ({DEFAULT_NOTES_PARALLEL})"
                )
                continue
            if not (1 <= raw <= _MAX_NOTES_PARALLEL):
                # 0 would crash ``split_inventory_contiguous`` with
                # ZeroDivisionError; negatives silently produce no batches
                # (sheet skipped without a failure log). Both are peer-
                # review MEDIUM findings — fail safe to the default so
                # the sheet still runs with the pre-existing 5-way fallback.
                _warn_bad_parallel_once(
                    f"{model_id}: notes_parallel={raw} out of range "
                    f"[1, {_MAX_NOTES_PARALLEL}] — falling back to "
                    f"default ({DEFAULT_NOTES_PARALLEL})"
                )
                continue
            _parallel_cache[model_id] = raw
    except (OSError, TypeError, KeyError) as exc:
        if not _parallel_load_failed:
            logger.warning(
                "Failed to load notes_parallel from %s: %s — defaulting to %d",
                _CONFIG_PATH, exc, DEFAULT_NOTES_PARALLEL,
            )
            _parallel_load_failed = True
        _parallel_cache = {}

    return _parallel_cache


def resolve_notes_parallel(model) -> int:
    """Return the Sheet-12 fan-out width configured for ``model``.

    ``model`` may be a PydanticAI Model instance (``OpenAIChatModel``,
    ``GoogleModel``, ``AnthropicModel``) or a plain string. Lookup mirrors
    ``get_model_pricing``: exact match first, then normalised (proxy
    prefix stripped) match, then ``DEFAULT_NOTES_PARALLEL``.

    Cheap/fast models ship requests through the provider's TPM bucket
    faster than slow ones, so a 5-way fan-out on e.g. ``gpt-5.4-mini``
    reliably triggers HTTP 429. The per-model override in models.json
    lets the operator dial parallelism down for those without losing
    the parallelism benefit on slow, heavy models.
    """
    parallel_map = _load_notes_parallel()
    if not parallel_map:
        return DEFAULT_NOTES_PARALLEL

    name = _resolve_model_name(model)

    if name in parallel_map:
        return parallel_map[name]

    norm = _normalize(name)
    for model_id, value in parallel_map.items():
        if _normalize(model_id) == norm:
            return value

    # Unknown model → safe default. Don't warn — the retry path still
    # catches overruns and warning on every call would spam a run that
    # uses a newly-released model id not yet in the registry.
    return DEFAULT_NOTES_PARALLEL


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
