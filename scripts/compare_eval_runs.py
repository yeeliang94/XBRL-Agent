"""Compare two groups of graded runs — the Phase 2 accept-or-revert delta.

Skill-first harness (docs/PROPOSAL-skill-first-harness.md §8): Phase 0 records a
baseline scorecard with the workflow-reference activation gate OFF; Phase 2
re-runs the SAME benchmark scenarios with the gate ON. This script reads the
eval scores + token/tool rollups the existing pipeline already persists and
prints the per-group average + the delta — so run-to-run LLM variance doesn't
masquerade as a treatment effect (the proposal asks for N >= 3 repeats each).

Read-only: it touches no run, writes nothing, and reuses the existing
``db.repository`` read APIs (``fetch_eval_score_for_run`` / ``fetch_run_agents``).
The aggregation core (`aggregate`, `delta`) is pure so it is unit-testable
without a DB.

Usage (run on Windows where the gold lives, after both groups have completed):

    python -m scripts.compare_eval_runs \
        --db output/app.db \
        --baseline 161,162,163 \
        --treatment 167,168,169

Per-statement / per-cell signal: the eval scorecard is per (run, benchmark)
aggregate. For the per-statement breakdown the proposal's Phase 2 gate wants,
either attach a single-statement benchmark per scenario, or inspect the
Concepts/Values diff in the run page — this script gives the headline accuracy
+ cost delta that decides accept-or-revert for the branch.
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

# Allow `python scripts/compare_eval_runs.py` as well as `-m scripts...`.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.repository import (  # noqa: E402
    fetch_eval_score_for_run,
    fetch_run,
    fetch_run_agents,
)


@dataclass
class RunRecord:
    """One graded run's headline numbers (the inputs to aggregation)."""

    run_id: int
    score: float           # matched / gold_cells, in [0, 1]
    gold_cells: int
    matched: int
    missing: int
    mismatch: int
    extra: int
    scale_mismatch: int
    total_tokens: int
    tool_calls: int


def _mean(xs: Sequence[float]) -> float:
    return statistics.fmean(xs) if xs else 0.0


def aggregate(records: Sequence[RunRecord]) -> dict:
    """Average a group of run records. Pure — no DB. Empty group → zeros."""
    n = len(records)
    if n == 0:
        return {"n": 0}
    return {
        "n": n,
        "score": _mean([r.score for r in records]),
        "gold_cells": _mean([r.gold_cells for r in records]),
        "matched": _mean([r.matched for r in records]),
        "missing": _mean([r.missing for r in records]),
        "mismatch": _mean([r.mismatch for r in records]),
        "extra": _mean([r.extra for r in records]),
        "scale_mismatch": _mean([r.scale_mismatch for r in records]),
        "total_tokens": _mean([r.total_tokens for r in records]),
        "tool_calls": _mean([r.tool_calls for r in records]),
    }


def delta(baseline: dict, treatment: dict) -> dict:
    """treatment - baseline for every shared numeric field. Pure — no DB."""
    keys = {"score", "gold_cells", "matched", "missing", "mismatch",
            "extra", "scale_mismatch", "total_tokens", "tool_calls"}
    out = {}
    for k in keys:
        if k in baseline and k in treatment:
            out[k] = treatment[k] - baseline[k]
    return out


def load_record(conn: sqlite3.Connection, run_id: int) -> Optional[RunRecord]:
    """Read one run's scorecard + token/tool totals, or None if it isn't graded."""
    score = fetch_eval_score_for_run(conn, run_id)
    if score is None:
        return None
    agents = fetch_run_agents(conn, run_id)
    total_tokens = sum(
        a.total_tokens or (a.prompt_tokens + a.completion_tokens) for a in agents
    )
    tool_calls = sum(a.tool_call_count for a in agents)
    return RunRecord(
        run_id=run_id,
        score=score["score"],
        gold_cells=score["gold_cells"],
        matched=score["matched_cells"],
        missing=score["missing_cells"],
        mismatch=score["mismatch_cells"],
        extra=score["extra_cells"],
        scale_mismatch=score["scale_mismatch"],
        total_tokens=total_tokens,
        tool_calls=tool_calls,
    )


def _load_group(conn: sqlite3.Connection, run_ids: Sequence[int]) -> tuple[list[RunRecord], list[int]]:
    """Return (records, ungraded_run_ids) so the caller can warn, never silently drop."""
    records, ungraded = [], []
    for rid in run_ids:
        rec = load_record(conn, rid)
        (records if rec is not None else ungraded).append(rec if rec is not None else rid)
    return records, ungraded


def format_report(
    baseline: dict, treatment: dict, d: dict,
    baseline_ids: Sequence[int], treatment_ids: Sequence[int],
    ungraded: Sequence[int],
) -> str:
    lines = ["=== Skill-first harness — Phase 2 accept-or-revert delta ===", ""]
    lines.append(f"Baseline (gate OFF) runs {list(baseline_ids)} — n={baseline.get('n', 0)} graded")
    lines.append(f"Treatment (gate ON) runs {list(treatment_ids)} — n={treatment.get('n', 0)} graded")
    if ungraded:
        lines.append(f"WARNING: {len(ungraded)} run(s) had no eval score and were SKIPPED: {list(ungraded)}")
    lines.append("")
    if not baseline.get("n") or not treatment.get("n"):
        lines.append("Not enough graded runs in one of the groups to compare.")
        return "\n".join(lines)
    rows = [
        ("score (matched/gold)", baseline["score"], treatment["score"], d["score"], True),
        ("matched", baseline["matched"], treatment["matched"], d["matched"], False),
        ("missing", baseline["missing"], treatment["missing"], d["missing"], False),
        ("mismatch", baseline["mismatch"], treatment["mismatch"], d["mismatch"], False),
        ("extra (flag)", baseline["extra"], treatment["extra"], d["extra"], False),
        ("scale_mismatch (flag)", baseline["scale_mismatch"], treatment["scale_mismatch"], d["scale_mismatch"], False),
        ("total tokens", baseline["total_tokens"], treatment["total_tokens"], d["total_tokens"], False),
        ("tool calls", baseline["tool_calls"], treatment["tool_calls"], d["tool_calls"], False),
    ]
    lines.append(f"{'metric':<24}{'baseline':>14}{'treatment':>14}{'delta':>14}")
    for name, b, t, dv, is_score in rows:
        if is_score:
            lines.append(f"{name:<24}{b:>14.4f}{t:>14.4f}{dv:>+14.4f}")
        else:
            lines.append(f"{name:<24}{b:>14.1f}{t:>14.1f}{dv:>+14.1f}")
    lines.append("")
    verdict = "IMPROVED-OR-HELD" if d["score"] >= 0 else "REGRESSED"
    lines.append(f"Headline accuracy delta: {d['score']:+.4f}  →  {verdict}")
    lines.append(
        "Gate: keep the references only if accuracy improves-or-holds AND no "
        "material token regression. A per-statement regression can hide inside "
        "an aggregate hold — confirm SOCIE/SOCF per-statement before accepting "
        "(proposal §8 Phase 2)."
    )
    return "\n".join(lines)


def _parse_ids(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Compare baseline vs treatment graded runs.")
    ap.add_argument("--db", required=True, help="Path to the SQLite DB (e.g. output/app.db).")
    ap.add_argument("--baseline", required=True, help="Comma-separated gate-OFF run ids.")
    ap.add_argument("--treatment", required=True, help="Comma-separated gate-ON run ids.")
    args = ap.parse_args(argv)

    baseline_ids = _parse_ids(args.baseline)
    treatment_ids = _parse_ids(args.treatment)

    conn = sqlite3.connect(args.db)
    try:
        b_recs, b_ungraded = _load_group(conn, baseline_ids)
        t_recs, t_ungraded = _load_group(conn, treatment_ids)
    finally:
        conn.close()

    b_agg, t_agg = aggregate(b_recs), aggregate(t_recs)
    print(format_report(
        b_agg, t_agg, delta(b_agg, t_agg),
        baseline_ids, treatment_ids, list(b_ungraded) + list(t_ungraded),
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
