"""Tests for the Phase 2 baseline-vs-treatment comparison helper.

The DB-reading layer is exercised end-to-end by the eval suite; here we pin the
PURE aggregation/delta/report core (no DB) — the N-repeats averaging and the
accept-or-revert verdict the proposal's Phase 2 gate depends on.
"""
from __future__ import annotations

import sqlite3
import types

import pytest

from scripts.compare_eval_runs import (
    RunRecord, aggregate, delta, format_report, load_record,
)


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


def test_load_record_with_default_tuple_factory(tmp_path):
    """Regression: the CLI opens SQLite with the DEFAULT (tuple) row factory,
    but fetch_run_agents indexes rows by column NAME. load_record must force
    sqlite3.Row itself so the Windows compare command doesn't crash with
    'TypeError: tuple indices must be integers or slices, not str'."""
    from db import repository as repo
    from db import schema

    db_path = str(tmp_path / "t.db")
    schema.init_db(db_path)
    # Open exactly the way scripts.compare_eval_runs.main does — NO row_factory.
    conn = sqlite3.connect(db_path)
    assert conn.row_factory is None  # the buggy-by-default setup

    run_id = repo.create_run(conn, status="completed")
    agent_id = repo.create_run_agent(conn, run_id, "SOCIE", "Default", "m")
    conn.execute(
        "UPDATE run_agents SET total_tokens=?, tool_call_count=? WHERE id=?",
        (1500, 30, agent_id),
    )
    card = types.SimpleNamespace(
        gold_cells=10, matched=8, missing=1, mismatch=1, extra=2, scale_mismatch=0,
    )
    repo.save_eval_score(conn, run_id, benchmark_id=1, card=card)
    conn.commit()

    rec = load_record(conn, run_id)
    conn.close()

    assert rec is not None
    assert rec.score == pytest.approx(0.8)
    assert rec.matched == 8 and rec.gold_cells == 10
    assert rec.total_tokens == 1500
    assert rec.tool_calls == 30


def test_load_record_returns_none_for_ungraded_run(tmp_path):
    from db import repository as repo
    from db import schema

    db_path = str(tmp_path / "t.db")
    schema.init_db(db_path)
    conn = sqlite3.connect(db_path)
    run_id = repo.create_run(conn, status="completed")  # no eval score attached
    conn.commit()
    assert load_record(conn, run_id) is None
    conn.close()
