"""Per-document + suite scorecard assembly (Evals workspace, Step E4).

A scorecard rolls up everything already computed per run — grader accuracy +
failure taxonomy, run-to-run consistency, health (cross-check pass rate,
reviewer flags, failed agents, tokens/duration), and notes placement coverage —
into ONE document scorecard, then aggregates a suite's documents.

Two layers, like the grader:
  * pure aggregation (`aggregate_suite`) over already-loaded scorecards, so the
    suite math is unit-testable without a DB;
  * a thin DB reader (`build_document_scorecard`) that gathers a run's pieces.

Aggregation rules (PRD Scoring Design):
  * Suite headline = simple MEAN of per-document accuracy (each filing counts
    equally, so one giant group filing can't drown ten small companies).
  * Pooled slot figure (Σmatched / Σgold) is secondary.
  * The WORST document is always surfaced (regressions hide in averages).
  * A failed document is excluded from the aggregate and labelled; the summary
    states "N of M" documents contributed.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

# Notes coverage statuses that count against placement (skips excluded — a
# deliberate skip is not a failure; PRD Family 3).
_COVERAGE_MISSING = ("missing", "suspected_gap")


@dataclass
class DocumentScorecard:
    run_id: int
    label: str = ""
    status: str = ""
    # Accuracy (None when no gold attached).
    accuracy: Optional[float] = None
    gold_cells: int = 0
    matched_cells: int = 0
    taxonomy: dict = field(default_factory=dict)
    per_statement: dict = field(default_factory=dict)
    # Consistency (None unless the run is a repeat group with ≥2 finished).
    consistency: Optional[float] = None
    # Health.
    cross_check_pass_rate: Optional[float] = None
    reviewer_flags: int = 0
    failed_agents: int = 0
    total_tokens: int = 0
    duration_s: Optional[float] = None
    # Notes placement coverage (None when unavailable / feature off).
    notes_coverage: Optional[float] = None
    notes_coverage_available: bool = False

    @property
    def failed(self) -> bool:
        """A failed run is excluded from the suite aggregate + labelled."""
        return self.status == "failed"

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "label": self.label,
            "status": self.status,
            "failed": self.failed,
            "accuracy": self.accuracy,
            "gold_cells": self.gold_cells,
            "matched_cells": self.matched_cells,
            "taxonomy": self.taxonomy,
            "per_statement": self.per_statement,
            "consistency": self.consistency,
            "cross_check_pass_rate": self.cross_check_pass_rate,
            "reviewer_flags": self.reviewer_flags,
            "failed_agents": self.failed_agents,
            "total_tokens": self.total_tokens,
            "duration_s": self.duration_s,
            "notes_coverage": self.notes_coverage,
            "notes_coverage_available": self.notes_coverage_available,
        }


def aggregate_suite(scorecards: list[DocumentScorecard]) -> dict:
    """Aggregate per-document scorecards into a suite summary.

    Headline = mean per-document accuracy over documents that (a) did not fail
    and (b) have gold. Pooled figure is secondary. Worst document is surfaced.
    Taxonomy totals sum across contributing documents.
    """
    total = len(scorecards)
    graded = [
        s for s in scorecards
        if not s.failed and s.accuracy is not None and s.gold_cells > 0
    ]
    failed = [s for s in scorecards if s.failed]

    mean_accuracy = (
        sum(s.accuracy for s in graded) / len(graded) if graded else None
    )
    pooled_matched = sum(s.matched_cells for s in graded)
    pooled_gold = sum(s.gold_cells for s in graded)
    pooled_accuracy = (pooled_matched / pooled_gold) if pooled_gold > 0 else None

    worst = min(graded, key=lambda s: s.accuracy) if graded else None

    # Taxonomy totals across graded documents.
    taxonomy_totals: dict[str, int] = {}
    for s in graded:
        for k, v in (s.taxonomy or {}).items():
            taxonomy_totals[k] = taxonomy_totals.get(k, 0) + int(v or 0)

    # Consistency + coverage + cross-check means over documents that have them.
    # Failed docs are excluded to stay consistent with the accuracy headline
    # (a failed run's partial health signals shouldn't move the suite means).
    live = [s for s in scorecards if not s.failed]
    consistencies = [s.consistency for s in live if s.consistency is not None]
    coverages = [
        s.notes_coverage for s in live
        if s.notes_coverage is not None and s.notes_coverage_available
    ]
    ccprs = [
        s.cross_check_pass_rate for s in live
        if s.cross_check_pass_rate is not None
    ]

    return {
        "documents_total": total,
        "documents_graded": len(graded),
        "documents_failed": len(failed),
        "coverage_note": f"{len(graded)} of {total}",
        "mean_accuracy": mean_accuracy,
        "pooled_accuracy": pooled_accuracy,
        "pooled_matched": pooled_matched,
        "pooled_gold": pooled_gold,
        "worst_document": worst.to_dict() if worst else None,
        "taxonomy_totals": taxonomy_totals,
        "mean_consistency": (sum(consistencies) / len(consistencies)) if consistencies else None,
        "mean_notes_coverage": (sum(coverages) / len(coverages)) if coverages else None,
        "mean_cross_check_pass_rate": (sum(ccprs) / len(ccprs)) if ccprs else None,
    }


def _coverage_rate(rows: list[dict]) -> tuple[Optional[float], bool]:
    """Placement coverage = placed / (placed + missing + suspected_gap) over
    top-level notes, skips excluded. Returns (rate, available). A `note_num=-1`
    banner sentinel signals inventory availability (gotcha #27)."""
    tops = [r for r in rows if r.get("subnote_ref") in (None, "")]
    banner = next((r for r in tops if r.get("note_num") == -1), None)
    real = [r for r in tops if r.get("note_num") != -1]
    if banner is not None and banner.get("status") == "inventory_unavailable":
        return None, False
    if not real:
        return None, False
    placed = sum(1 for r in real if r.get("status") == "placed")
    missing = sum(1 for r in real if r.get("status") in _COVERAGE_MISSING)
    denom = placed + missing
    if denom == 0:
        return None, True
    return placed / denom, True


def build_document_scorecard(
    conn: sqlite3.Connection, run_id: int, *, label: Optional[str] = None
) -> Optional[DocumentScorecard]:
    """Assemble a run's scorecard from data already persisted per run."""
    from db import repository as repo

    run = repo.fetch_run(conn, run_id)
    if run is None:
        return None
    card = DocumentScorecard(
        run_id=run_id,
        label=label or run.pdf_filename or f"run {run_id}",
        status=run.status or "",
    )

    # Accuracy + taxonomy (when a benchmark was graded).
    score = repo.fetch_eval_score_for_run(conn, run_id)
    if score is not None:
        card.accuracy = score.get("score")
        card.gold_cells = int(score.get("gold_cells", 0) or 0)
        card.matched_cells = int(score.get("matched_cells", 0) or 0)
        card.taxonomy = score.get("taxonomy") or {}
        card.per_statement = score.get("per_statement") or {}

    # Consistency (repeat group).
    group_id = getattr(run, "repeat_group_id", None)
    if group_id is not None:
        group = repo.fetch_repeat_group(conn, group_id)
        cons = (group or {}).get("consistency") or {}
        if cons.get("available"):
            card.consistency = cons.get("consistency")

    # Health — cross-check pass rate (over hard passed/failed only).
    checks = repo.fetch_cross_checks(conn, run_id)
    hard = [c for c in checks if c.status in ("passed", "failed")]
    if hard:
        card.cross_check_pass_rate = sum(
            1 for c in hard if c.status == "passed"
        ) / len(hard)

    # Failed agents.
    agents = repo.fetch_run_agents(conn, run_id)
    card.failed_agents = sum(1 for a in agents if a.status == "failed")

    # Reviewer flags still needing attention — a resolved/dismissed flag is a
    # closed issue and must not drag the health signal forever. Exclude the two
    # terminal statuses (rather than allowlisting 'open'/'answered') so a future
    # non-terminal status still counts. No dedicated repo helper.
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM reviewer_flags WHERE run_id = ? "
            "AND status NOT IN ('resolved', 'dismissed')",
            (run_id,),
        ).fetchone()
        card.reviewer_flags = int(row[0]) if row else 0
    except sqlite3.OperationalError:
        card.reviewer_flags = 0

    # Tokens + duration (run-level, backfilled at completion).
    try:
        row = conn.execute(
            "SELECT total_tokens, started_at, ended_at FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is not None:
            card.total_tokens = int(row[0] or 0)
            card.duration_s = repo._parse_iso_duration(row[1] or "", row[2] or "")
    except sqlite3.OperationalError:
        pass

    # Notes placement coverage.
    try:
        rows = repo.fetch_notes_coverage(conn, run_id)
        card.notes_coverage, card.notes_coverage_available = _coverage_rate(rows)
    except sqlite3.OperationalError:
        pass

    return card
