"""Tests for the Phase 2 baseline-vs-treatment comparison helper.

The DB-reading layer is exercised end-to-end by the eval suite; here we pin the
PURE aggregation/delta/report core (no DB) — the N-repeats averaging and the
accept-or-revert verdict the proposal's Phase 2 gate depends on.
"""
from __future__ import annotations

import pytest

from scripts.compare_eval_runs import RunRecord, aggregate, delta, format_report


def _rec(run_id, score, matched=0, gold=0, missing=0, mismatch=0, extra=0,
         scale=0, tokens=0, tools=0):
    return RunRecord(
        run_id=run_id, score=score, gold_cells=gold, matched=matched,
        missing=missing, mismatch=mismatch, extra=extra, scale_mismatch=scale,
        total_tokens=tokens, tool_calls=tools,
    )


def test_aggregate_averages_across_repeats():
    recs = [
        _rec(1, score=0.80, matched=8, gold=10, tokens=1000, tools=20),
        _rec(2, score=0.90, matched=9, gold=10, tokens=1200, tools=22),
    ]
    agg = aggregate(recs)
    assert agg["n"] == 2
    assert agg["score"] == pytest.approx(0.85)
    assert agg["matched"] == 8.5
    assert agg["total_tokens"] == 1100
    assert agg["tool_calls"] == 21


def test_aggregate_empty_group():
    assert aggregate([]) == {"n": 0}


def test_delta_is_treatment_minus_baseline():
    base = aggregate([_rec(1, score=0.80, tokens=1000)])
    treat = aggregate([_rec(2, score=0.88, tokens=1300)])
    d = delta(base, treat)
    assert round(d["score"], 4) == 0.08
    assert d["total_tokens"] == 300


def test_report_verdict_improved_and_regressed():
    base = aggregate([_rec(1, score=0.80)])
    up = aggregate([_rec(2, score=0.85)])
    down = aggregate([_rec(3, score=0.70)])
    improved = format_report(base, up, delta(base, up), [1], [2], [])
    regressed = format_report(base, down, delta(base, down), [1], [3], [])
    assert "IMPROVED-OR-HELD" in improved
    assert "REGRESSED" in regressed


def test_report_warns_on_ungraded_and_short_group():
    base = aggregate([])  # nothing graded
    treat = aggregate([_rec(2, score=0.9)])
    out = format_report(base, treat, delta(base, treat), [1], [2], ungraded=[1])
    assert "SKIPPED: [1]" in out
    assert "Not enough graded runs" in out
