"""Tests for ``pricing.resolve_notes_parallel``.

Validates the model-aware Sheet-12 fan-out width resolver. The registry
in ``config/models.json`` carries a ``notes_parallel`` field per model;
cheap/fast models drop to 2, heavy/slow models stay at 5. Unknown models
fall back to ``DEFAULT_NOTES_PARALLEL``.
"""
from __future__ import annotations

import json

import pricing
from pricing import DEFAULT_NOTES_PARALLEL, resolve_notes_parallel


def _reset_cache() -> None:
    """Drop the module-level lazy cache between tests so a cache primed by
    an earlier test (possibly against a different config) can't bleed in."""
    pricing._parallel_cache = None
    pricing._parallel_load_failed = False
    # Also clear the dedup set so each validation test starts from a
    # clean slate and its own warning-emission assertions are not
    # silenced by a prior test's identical message.
    pricing._warned_bad_parallel_keys.clear()


class _FakeModel:
    """Stand-in for a PydanticAI ``OpenAIChatModel`` / ``GoogleModel`` /
    ``AnthropicModel``. Only the ``model_name`` attribute is needed by the
    resolver, so keeping this inline avoids pulling the real SDK classes
    into the unit-test path."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name


def test_cheap_model_resolves_to_two():
    _reset_cache()
    # Directly the canonical id from the registry — this is the 429-
    # offender the whole feature was built for, so guard it explicitly.
    assert resolve_notes_parallel("openai.gpt-5.4-mini") == 2


def test_heavy_model_resolves_to_five():
    _reset_cache()
    assert resolve_notes_parallel("openai.gpt-5.4") == 5


def test_proxy_prefix_is_normalised():
    _reset_cache()
    # PydanticAI direct-mode Google models strip the registry's
    # ``vertex_ai.`` prefix and present as ``google-gla:gemini-...``. The
    # resolver's normalisation step has to match across that rename or
    # direct-mode runs quietly default to 5 instead of the configured 2.
    assert resolve_notes_parallel("google-gla:gemini-3-flash-preview") == 2


def test_pydantic_ai_model_instance():
    _reset_cache()
    # The real call site passes a PydanticAI Model instance, not a string.
    # ``model_name`` is the attribute every provider class exposes.
    model = _FakeModel(model_name="bedrock.anthropic.claude-opus-4-6")
    assert resolve_notes_parallel(model) == 5


def test_unknown_model_falls_back_to_default():
    _reset_cache()
    # New/unreleased model not yet in the registry: the resolver must
    # not raise and must not warn per-call — returning the safe default
    # is the contract that lets operators drop in new model ids without
    # a registry edit. The 429 retry path catches real overruns.
    assert resolve_notes_parallel("future-model-not-yet-added") == DEFAULT_NOTES_PARALLEL


def test_cache_survives_repeated_calls():
    _reset_cache()
    # First call primes the cache; second must hit it (same answer,
    # no second file read). Guards against a regression where the
    # lazy-load sentinel is accidentally reset per-call.
    first = resolve_notes_parallel("openai.gpt-5.4-mini")
    assert pricing._parallel_cache is not None
    second = resolve_notes_parallel("openai.gpt-5.4-mini")
    assert first == second == 2


def test_all_cheap_bucket_resolves_to_two():
    _reset_cache()
    # Lock the whole cheap bucket so a future registry edit that
    # accidentally bumps one of these to 5 is caught here, not in a
    # production run that starts hitting 429s again.
    cheap = [
        "vertex_ai.gemini-3-flash-preview",
        "vertex_ai.gemini-3.1-flash-lite-preview",
        "bedrock.anthropic.claude-haiku-4-5",
        "openai.gpt-5.4-mini",
        "openai.gpt-5.4-nano",
    ]
    for model_id in cheap:
        assert resolve_notes_parallel(model_id) == 2, f"{model_id} should be 2"


def test_all_heavy_bucket_resolves_to_five():
    _reset_cache()
    # Symmetric guard on the heavy bucket.
    heavy = [
        "vertex_ai.gemini-3.1-pro-preview",
        "bedrock.anthropic.claude-sonnet-4-6",
        "bedrock.anthropic.claude-opus-4-6",
        "openai.gpt-5.4",
    ]
    for model_id in heavy:
        assert resolve_notes_parallel(model_id) == 5, f"{model_id} should be 5"


# ---------------------------------------------------------------------------
# Peer-review HIGH regression: direct-mode bare names must resolve.
#
# ``server._create_proxy_model`` strips the registry prefix and constructs
# the upstream PydanticAI model with the bare name (e.g. ``gpt-5.4-mini``).
# If ``_normalize`` fails to strip the matching prefix on the registry
# side, the lookup falls through to ``DEFAULT_NOTES_PARALLEL`` and the
# whole feature is a no-op on the direct-API runtime path. These tests
# pin the fix.
# ---------------------------------------------------------------------------

def test_bare_openai_name_resolves_to_cheap_bucket():
    _reset_cache()
    # This is the exact bug the peer reviewer caught: a bare OpenAI name
    # silently defaulted to 5 because ``_normalize`` only stripped Google
    # prefixes. If this regresses, the feature quietly re-breaks.
    assert resolve_notes_parallel("gpt-5.4-mini") == 2


def test_bare_anthropic_name_resolves_to_cheap_bucket():
    _reset_cache()
    assert resolve_notes_parallel("claude-haiku-4-5") == 2


def test_bare_anthropic_heavy_name_resolves_to_five():
    _reset_cache()
    # Symmetric case — bare heavy model must also round-trip.
    assert resolve_notes_parallel("claude-opus-4-6") == 5


def test_bare_openai_heavy_name_resolves_to_five():
    _reset_cache()
    assert resolve_notes_parallel("gpt-5.4") == 5


# ---------------------------------------------------------------------------
# Peer-review MEDIUM regression: bounds + type validation.
#
# A ``notes_parallel`` of 0 raises ``ZeroDivisionError`` inside
# ``split_inventory_contiguous``; negatives silently produce zero batches;
# booleans sneak past ``isinstance(x, int)`` because ``bool`` is an int
# subclass. All three must fall back to ``DEFAULT_NOTES_PARALLEL`` and
# log once — the sheet still runs with safe parallelism rather than
# crashing or silently skipping.
# ---------------------------------------------------------------------------

def _write_registry(tmp_path, entries) -> None:
    """Point the pricing module at a tmp models.json containing ``entries``.

    Swaps ``pricing._CONFIG_PATH`` in-place (restore is the caller's job
    — tests that call this wrap in try/finally). Keeps the test
    file-based rather than monkeypatching ``_load_notes_parallel`` so
    the validation logic is exercised end-to-end, which is the whole
    point of these regressions.
    """
    path = tmp_path / "models.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    pricing._CONFIG_PATH = path  # caller must restore


def _with_registry(tmp_path, entries, model_id):
    """Run one resolver call against a tmp registry and return the int."""
    original = pricing._CONFIG_PATH
    try:
        _reset_cache()
        _write_registry(tmp_path, entries)
        return resolve_notes_parallel(model_id)
    finally:
        pricing._CONFIG_PATH = original
        _reset_cache()


def test_zero_value_falls_back_to_default(tmp_path, caplog):
    # ``0`` would crash ``split_inventory_contiguous`` with ZeroDivisionError;
    # the resolver must silently fall back to the safe default.
    entries = [{"id": "test.model", "notes_parallel": 0}]
    with caplog.at_level("WARNING"):
        assert _with_registry(tmp_path, entries, "test.model") == DEFAULT_NOTES_PARALLEL
    assert any("out of range" in r.message for r in caplog.records)


def test_negative_value_falls_back_to_default(tmp_path, caplog):
    # Negatives silently produce zero batches in ``split_inventory_contiguous``
    # (sheet skipped without a failure log) — the resolver must reject.
    entries = [{"id": "test.model", "notes_parallel": -3}]
    with caplog.at_level("WARNING"):
        assert _with_registry(tmp_path, entries, "test.model") == DEFAULT_NOTES_PARALLEL
    assert any("out of range" in r.message for r in caplog.records)


def test_excessive_value_falls_back_to_default(tmp_path, caplog):
    # 999 parallel sub-agents would blow through every TPM bucket instantly.
    # The cap prevents a typo'd config from doing that.
    entries = [{"id": "test.model", "notes_parallel": 999}]
    with caplog.at_level("WARNING"):
        assert _with_registry(tmp_path, entries, "test.model") == DEFAULT_NOTES_PARALLEL
    assert any("out of range" in r.message for r in caplog.records)


def test_boolean_is_rejected(tmp_path, caplog):
    # Python quirk: ``isinstance(True, int) == True``. Without an
    # explicit bool guard, a config typo of ``"notes_parallel": true``
    # would coerce to 1. Reject explicitly — almost certainly a mistake.
    entries = [{"id": "test.model", "notes_parallel": True}]
    with caplog.at_level("WARNING"):
        assert _with_registry(tmp_path, entries, "test.model") == DEFAULT_NOTES_PARALLEL
    assert any("not an int" in r.message for r in caplog.records)


def test_non_int_is_rejected(tmp_path, caplog):
    # A stray string like ``"notes_parallel": "5"`` must not silently
    # coerce or crash — fall back and log.
    entries = [{"id": "test.model", "notes_parallel": "5"}]
    with caplog.at_level("WARNING"):
        assert _with_registry(tmp_path, entries, "test.model") == DEFAULT_NOTES_PARALLEL
    assert any("not an int" in r.message for r in caplog.records)


def test_missing_id_entry_is_skipped_without_poisoning_others(tmp_path, caplog):
    # A malformed entry missing ``id`` must be skipped cleanly so sibling
    # entries still resolve. Before the KeyError fix this would abort the
    # load, set _parallel_load_failed, and leave every model on default.
    entries = [
        {"notes_parallel": 2},  # malformed — no id
        {"id": "good.model", "notes_parallel": 3},
    ]
    with caplog.at_level("WARNING"):
        assert _with_registry(tmp_path, entries, "good.model") == 3
    assert any("missing/invalid id" in r.message for r in caplog.records)


def test_bad_config_warns_once_then_dedups(tmp_path, caplog):
    # Same bad entry shouldn't spam the log across repeated resolver calls
    # in a long-running server. After the first WARN the dedup set
    # downgrades repeats to DEBUG.
    entries = [{"id": "bad.model", "notes_parallel": 0}]
    original = pricing._CONFIG_PATH
    try:
        _reset_cache()
        _write_registry(tmp_path, entries)
        with caplog.at_level("WARNING"):
            # First call populates the cache (and warns).
            resolve_notes_parallel("bad.model")
        first_warn_count = sum(
            1 for r in caplog.records if r.levelname == "WARNING"
        )
        # Subsequent calls hit the cache — no new records expected at all,
        # but clearing the cache and re-loading must dedupe at WARNING.
        pricing._parallel_cache = None
        caplog.clear()
        with caplog.at_level("WARNING"):
            resolve_notes_parallel("bad.model")
        second_warn_count = sum(
            1 for r in caplog.records if r.levelname == "WARNING"
        )
        assert first_warn_count >= 1
        assert second_warn_count == 0, (
            "Repeat of identical bad-config message should downgrade to DEBUG"
        )
    finally:
        pricing._CONFIG_PATH = original
        _reset_cache()


def test_bedrock_non_anthropic_prefix_also_strips():
    _reset_cache()
    # ``bedrock.`` is the shorter prefix; ordering inside ``_normalize``
    # must try ``bedrock.anthropic.`` first so anthropic ids don't get
    # partially stripped to ``anthropic.claude-...``. We don't have a
    # non-anthropic bedrock model in the registry today, but an unknown
    # ``bedrock.some-future-model`` must still reach the default without
    # raising.
    assert resolve_notes_parallel("bedrock.future-non-anthropic-model") == DEFAULT_NOTES_PARALLEL
