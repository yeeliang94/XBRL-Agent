"""Grading core — compare a run's facts against a benchmark's gold facts.

This is the heart of the eval feature and is deliberately pure: it reads from
the DB but performs no writes and depends on nothing in the server, so it is
verifiable in isolation (see ``tests/test_eval_grader.py``).

The grading rule (docs/PRD-eval-benchmark.md §"Grading algorithm"):

* Scope to **gradeable gold cells** — gold facts whose concept
  ``kind IN ('LEAF','MATRIX_CELL')`` and whose concept's ``template_id`` is in
  the benchmark's explicit template set. COMPUTED totals are excluded so
  formula-derived values can't inflate the score (peer-review #3). Grading on
  ``concept_uuid`` means cross-sheet alias coords (which share one uuid) are
  naturally counted once (gotcha #21 / schema v11).
* For each gold cell, look up the run's fact for the same
  ``(concept_uuid, period, entity_scope)`` and classify:
  - **matched** — both present and numerically equal (``1`` == ``1.0``).
  - **scale_mismatch** — a kind of mismatch where ``run == gold * 10^k`` for
    ``k ∈ {±1, ±2, ±3, ±6}``. Counts wrong; also tallied separately.
  - **missing** — gold present, run empty/absent. Counts wrong.
  - **mismatch** — both present, unequal, non-scale. Counts wrong.
* **extra** — the run filled a gradeable leaf the gold left blank. Surfaced as
  a warning, NOT in the denominator (peer-review #4).
* ``not_disclosed`` gold is excluded from the denominator entirely; a run
  value at a ``not_disclosed`` gold cell is ignored (not counted extra).
  ``explicit_zero`` gold grades as numeric ``0``.

``score = matched / gold_cells`` where ``gold_cells = matched + missing +
mismatch`` (mismatch includes scale).
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

# Powers of ten the grader treats as a "scale mismatch" rather than a plain
# wrong value. ±3 covers the common thousands error (a run that forgot the
# "RM'000" header), ±6 the millions error, ±1/±2 the off-by-10/100 slips.
_SCALE_FACTORS = (1, 2, 3, 6, -1, -2, -3, -6)

# Concept kinds that carry a human-entered (gradeable) value. COMPUTED totals
# are Excel-formula-derived and excluded so they can't inflate the score.
_GRADEABLE_KINDS = ("LEAF", "MATRIX_CELL")

# Absolute tolerance for float-representation noise only — NOT an accounting
# tolerance band. Two values both stored as REAL that should be equal can
# differ in the last binary digit after a round-trip; 1e-6 absorbs that while
# staying far below a 1-RM difference (the PRD forbids fuzzy ±% tolerance).
_EPSILON = 1e-6


@dataclass
class ScoreCard:
    """Aggregate grading counts for one ``(run, benchmark)`` pair.

    ``gold_cells`` (the denominator) is ``matched + missing + mismatch``.
    ``extra`` and ``scale_mismatch`` are flags surfaced alongside the score,
    NOT folded into it.
    """
    gold_cells: int = 0
    matched: int = 0
    missing: int = 0
    mismatch: int = 0
    extra: int = 0
    scale_mismatch: int = 0

    @property
    def score(self) -> float:
        """Headline accuracy in [0, 1]. Zero gold cells → 0.0 (no division)."""
        if self.gold_cells <= 0:
            return 0.0
        return self.matched / self.gold_cells


def normalize(value) -> float | None:
    """Cast a stored cell value to float, or ``None`` if it isn't numeric.

    ``1`` and ``1.0`` normalise to the same float, which is the whole point —
    the match rule is numeric equality, not string equality.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _present_number(value, value_status: str) -> float | None:
    """Return the gradeable number a fact carries, or ``None`` if it carries
    none.

    A ``not_disclosed`` cell is treated as absent (the agent/human confirmed
    "no value here"). An ``explicit_zero`` cell is the number ``0``. Anything
    else uses the stored numeric value.
    """
    if value_status == "not_disclosed":
        return None
    if value_status == "explicit_zero":
        return 0.0
    return normalize(value)


def is_scale_mismatch(run: float, gold: float) -> bool:
    """True when ``run`` equals ``gold`` scaled by a power of ten in the set.

    A zero gold value can't produce a non-zero scaled value, so a zero-vs-
    nonzero pair is a plain mismatch, never a scale mismatch.
    """
    if gold == 0:
        return False
    for k in _SCALE_FACTORS:
        if math.isclose(run, gold * (10 ** k), rel_tol=1e-6, abs_tol=_EPSILON):
            return True
    return False


def _values_equal(a: float, b: float) -> bool:
    """Numeric equality with float-repr noise absorbed (not a tolerance band)."""
    return math.isclose(a, b, rel_tol=0.0, abs_tol=_EPSILON)


def _benchmark_template_ids(conn: sqlite3.Connection, benchmark_id: int) -> list[str]:
    """The benchmark's explicit template set (gotcha #21 — variant-precise)."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT template_id FROM eval_benchmark_templates WHERE benchmark_id = ?",
            (benchmark_id,),
        ).fetchall()
    ]


def _gradeable_facts(
    conn: sqlite3.Connection,
    table: str,
    id_col: str,
    id_value: int,
    template_ids: list[str],
) -> dict[tuple[str, str, str], tuple]:
    """Load gradeable facts from ``table`` keyed by
    ``(concept_uuid, period, entity_scope)``.

    Scoped to LEAF/MATRIX_CELL concepts whose ``template_id`` is in the
    benchmark's set. Returns ``{key: (value, value_status)}``.
    """
    if not template_ids:
        return {}
    placeholders = ",".join("?" for _ in template_ids)
    sql = (
        f"SELECT f.concept_uuid, f.period, f.entity_scope, f.value, "
        f"       f.value_status "
        f"FROM {table} f "
        f"JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
        f"WHERE f.{id_col} = ? "
        f"  AND n.kind IN ('LEAF','MATRIX_CELL') "
        f"  AND n.template_id IN ({placeholders})"
    )
    out: dict[tuple[str, str, str], tuple] = {}
    for r in conn.execute(sql, (id_value, *template_ids)).fetchall():
        key = (r[0], r[1], r[2])
        out[key] = (r[3], r[4])
    return out


def grade_run(
    conn: sqlite3.Connection, run_id: int, benchmark_id: int
) -> ScoreCard:
    """Grade a run against a benchmark and return the aggregate scorecard.

    Pure read: takes a connection, performs no writes. The caller persists the
    result via :func:`db.repository.save_eval_score`.
    """
    template_ids = _benchmark_template_ids(conn, benchmark_id)
    gold = _gradeable_facts(
        conn, "gold_concept_facts", "benchmark_id", benchmark_id, template_ids
    )
    run = _gradeable_facts(
        conn, "run_concept_facts", "run_id", run_id, template_ids
    )

    card = ScoreCard()

    # Walk every gold cell. A gold cell with no gradeable number
    # (not_disclosed, or blank) is excluded from the denominator entirely.
    for key, (g_val, g_status) in gold.items():
        g = _present_number(g_val, g_status)
        if g is None:
            continue  # not_disclosed / blank gold — out of denominator
        card.gold_cells += 1

        run_fact = run.get(key)
        r = _present_number(run_fact[0], run_fact[1]) if run_fact else None
        if r is None:
            card.missing += 1
        elif _values_equal(r, g):
            card.matched += 1
        else:
            card.mismatch += 1
            if is_scale_mismatch(r, g):
                card.scale_mismatch += 1

    # Extras — a run value on a gradeable leaf the gold left blank. A
    # not_disclosed gold cell is NOT an extra (the human said "nothing here";
    # a run value there is simply ignored).
    for key, (r_val, r_status) in run.items():
        if _present_number(r_val, r_status) is None:
            continue
        g_fact = gold.get(key)
        if g_fact is None:
            card.extra += 1
            continue
        g_val, g_status = g_fact
        if g_status == "not_disclosed":
            continue  # ignored, not extra
        if _present_number(g_val, g_status) is None:
            # gold present but blank/non-gradeable → the run filled a cell the
            # gold left empty → extra.
            card.extra += 1

    return card
