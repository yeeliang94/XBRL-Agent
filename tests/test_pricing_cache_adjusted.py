"""Cache-aware cost accounting (PLAN-extraction-harness-efficiency Step 1).

``pricing.estimate_cost`` bills every prompt token at the full input rate —
the PRE-CACHE estimate that ``run_agents.total_cost`` has always stored.
``pricing.estimate_cost_cache_adjusted`` bills prompt-cache READS at the
model's ``cached_input_price_per_mtok`` instead.

Provider convention (verified against the pinned library, 2026-08-18): the
``prompt_tokens`` this app records ALREADY INCLUDE the cache reads for every
provider — OpenAI/Google natively, and Anthropic because genai-prices'
extractor sums ``input_tokens + cache_creation_input_tokens +
cache_read_input_tokens`` into ``input_tokens``. So uncached = prompt − reads
for all of them. The plan's original "Anthropic excludes them" premise was
wrong for pydantic-ai 2.9 / genai-prices 0.0.71 and is pinned here against
the library's own mapping table so an upgrade that changes it fails loudly.

A model with no cached rate falls back to the full input price, so an
unpriced model can never silently under-report. Zero cache reads reproduce
``estimate_cost`` exactly.
"""
from __future__ import annotations

import json

import pytest

import pricing
from pricing import (
    cache_adjustment_from_stored_cost,
    estimate_cost,
    estimate_cost_cache_adjusted,
    get_cache_write_price,
    get_cached_input_price,
)


def _reset() -> None:
    pricing._pricing_cache = None
    pricing._load_failed = False
    pricing._cached_price_cache = None


@pytest.fixture
def registry(tmp_path):
    """Point pricing at a tmp models.json with an OpenAI, an Anthropic and an
    un-cached-rate model; restore afterwards."""
    entries = [
        {"id": "openai.gpt-x", "input_price_per_mtok": 2.5,
         "output_price_per_mtok": 15.0, "cached_input_price_per_mtok": 0.25},
        {"id": "bedrock.anthropic.claude-y", "input_price_per_mtok": 3.0,
         "output_price_per_mtok": 15.0, "cached_input_price_per_mtok": 0.3,
         "cache_write_price_per_mtok": 3.75},
        {"id": "vertex_ai.gemini-z", "input_price_per_mtok": 1.0,
         "output_price_per_mtok": 5.0},  # no cached rate → full price
    ]
    path = tmp_path / "models.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    original = pricing._CONFIG_PATH
    pricing._CONFIG_PATH = path
    _reset()
    try:
        yield
    finally:
        pricing._CONFIG_PATH = original
        _reset()


def test_openai_shape_bills_uncached_portion_plus_cached_reads(registry):
    # prompt 1,000,000 of which 600,000 were cache reads (INCLUDED in prompt).
    cost = estimate_cost_cache_adjusted(
        prompt_tokens=1_000_000, completion_tokens=0, thinking_tokens=0,
        model="openai.gpt-x", cache_read_tokens=600_000,
    )
    expected = 400_000 / 1e6 * 2.5 + 600_000 / 1e6 * 0.25
    assert cost == pytest.approx(expected)


def test_anthropic_shape_uses_the_same_inclusive_prompt_rule_and_write_rate(registry):
    # prompt 650,000 INCLUDES the 500,000 cache reads and 50,000 writes
    # (genai-prices sums them into input_tokens). Reads discounted, writes at
    # the 1.25x write rate, the remaining 100,000 at the input rate.
    cost = estimate_cost_cache_adjusted(
        prompt_tokens=650_000, completion_tokens=0, thinking_tokens=0,
        model="bedrock.anthropic.claude-y",
        cache_read_tokens=500_000, cache_write_tokens=50_000,
    )
    expected = 100_000 / 1e6 * 3.0 + 500_000 / 1e6 * 0.3 + 50_000 / 1e6 * 3.75
    assert cost == pytest.approx(expected)
    assert get_cache_write_price("bedrock.anthropic.claude-y") == 3.75
    # No write rate declared → plain input rate, never a surcharge invented.
    assert get_cache_write_price("openai.gpt-x") == 2.5


def test_reads_and_writes_are_clamped_to_the_prompt_count(registry):
    # Reads > prompt is contradictory telemetry: clamp, so $adj ≤ $pre.
    pre = estimate_cost(10, 0, 0, "openai.gpt-x")
    adj = estimate_cost_cache_adjusted(10, 0, 0, "openai.gpt-x", cache_read_tokens=1_000)
    assert adj == pytest.approx(10 / 1e6 * 0.25) and adj <= pre
    # Writes clamp to what is left after reads.
    adj2 = estimate_cost_cache_adjusted(
        100, 0, 0, "bedrock.anthropic.claude-y", cache_read_tokens=80, cache_write_tokens=50,
    )
    assert adj2 == pytest.approx(80 / 1e6 * 0.3 + 20 / 1e6 * 3.75)


def test_adjustment_from_stored_cost_keeps_retry_spend_and_legacy_rows(registry):
    # A stored total_cost with NO token splits (legacy row): unchanged.
    assert cache_adjustment_from_stored_cost(1.795, "openai.gpt-x") == pytest.approx(1.795)
    # With reads: subtract the read discount from the STORED figure — a
    # retried agent's failed-attempt spend (inside total_cost, outside the
    # token splits) survives.
    stored = 2.0  # includes $0.5 of failed-attempt spend
    adj = cache_adjustment_from_stored_cost(stored, "openai.gpt-x", cache_read_tokens=400_000,
                                            prompt_tokens=600_000)
    assert adj == pytest.approx(2.0 - 400_000 / 1e6 * (2.5 - 0.25))
    # Clamped when reads exceed a known prompt count; never below zero.
    assert cache_adjustment_from_stored_cost(0.01, "openai.gpt-x", cache_read_tokens=10**9,
                                             prompt_tokens=100) >= 0.0


def test_pinned_library_sums_anthropic_cache_tokens_into_input_tokens():
    """The convention above is only right while genai-prices maps Anthropic's
    cache_read_input_tokens (and cache_creation_input_tokens) INTO
    input_tokens. Read the library's extractor table directly; if an upgrade
    changes it, this fails and ``prompt_tokens_include_cache_reads`` must be
    revisited (else every Anthropic figure silently double-counts)."""
    from genai_prices import data as gp_data
    anthropic = next(p for p in gp_data.providers if p.id == "anthropic")
    default = next(e for e in anthropic.extractors if e.api_flavor == "default")
    dests_by_path = {}
    for m in default.mappings:
        dests_by_path.setdefault(m.path, set()).add(m.dest)
    assert "input_tokens" in dests_by_path["cache_read_input_tokens"]
    assert "input_tokens" in dests_by_path["cache_creation_input_tokens"]
    assert "cache_read_tokens" in dests_by_path["cache_read_input_tokens"]
    from pricing import prompt_tokens_include_cache_reads
    assert prompt_tokens_include_cache_reads() is True


def test_model_without_cached_rate_falls_back_to_full_price(registry):
    assert get_cached_input_price("vertex_ai.gemini-z") == 1.0
    adjusted = estimate_cost_cache_adjusted(
        prompt_tokens=1_000_000, completion_tokens=0, thinking_tokens=0,
        model="vertex_ai.gemini-z", cache_read_tokens=900_000,
    )
    assert adjusted == pytest.approx(estimate_cost(1_000_000, 0, 0, "vertex_ai.gemini-z"))


def test_zero_cache_reads_reproduces_pre_cache_estimate_exactly(registry):
    for model in ("openai.gpt-x", "bedrock.anthropic.claude-y", "vertex_ai.gemini-z"):
        assert estimate_cost_cache_adjusted(
            prompt_tokens=123_456, completion_tokens=7_890, thinking_tokens=100,
            model=model, cache_read_tokens=0,
        ) == estimate_cost(123_456, 7_890, 100, model)


def test_bare_model_name_resolves_cached_rate_via_prefix_strip(registry):
    # Direct mode hands over the bare name (gotcha in pricing._normalize).
    assert get_cached_input_price("gpt-x") == 0.25


def test_shipped_registry_prices_cache_reads_below_input_for_openai_and_anthropic():
    """The live config carries a cached rate on every OpenAI and Anthropic
    entry (the two providers whose cache-read discount is a published rate
    card). Absent → full price, which silently keeps overstating cost."""
    _reset()
    entries = pricing._read_models_json()
    assert entries, "config/models.json unreadable"
    for m in entries:
        provider = m.get("provider")
        if provider in ("openai", "anthropic"):
            assert "cached_input_price_per_mtok" in m, m["id"]
            assert m["cached_input_price_per_mtok"] < m["input_price_per_mtok"], m["id"]
