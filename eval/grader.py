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
from collections import Counter
from dataclasses import dataclass, field

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


# The failure taxonomy (docs/PLAN-evals-workspace.md, PRD Scoring Design). Every
# WRONG gold slot (missing OR mismatch) is assigned exactly ONE of these, so the
# counts partition the wrong set: ``sum(taxonomy.values()) == missing + mismatch``.
# The taxonomy NEVER changes the headline score — it only diagnoses failures for
# drill-down and trends. `scale` here is the residual-after-priority (a scale
# error that is also a period-swap classifies as period_swap), so it can be ≤ the
# independent `scale_mismatch` flag; that's intentional.
_TAXONOMY_KEYS = (
    # mismatch refinements (both values present, unequal)
    "period_swap",   # CY/PY transposed for the same line item
    "scope_swap",    # Group/Company transposed for the same line item
    "sign_flip",     # run == -gold
    "scale",         # run == gold * 10^k
    "plain_wrong",   # residual mismatch
    # missing refinements (gold present, run absent/blank)
    "false_not_disclosed",  # run explicitly asserted "not in PDF"
    "misplaced",            # the number landed on a different (gold-blank) row
    "unaddressed",          # the run never dealt with this slot
)


def empty_taxonomy() -> dict[str, int]:
    return {k: 0 for k in _TAXONOMY_KEYS}


@dataclass
class ScoreCard:
    """Aggregate grading counts for one ``(run, benchmark)`` pair.

    ``gold_cells`` (the denominator) is ``matched + missing + mismatch``.
    ``extra`` and ``scale_mismatch`` are flags surfaced alongside the score,
    NOT folded into it.

    ``taxonomy`` (v30) partitions the wrong slots into diagnosed failure modes;
    ``per_statement`` breaks ``gold_cells``/``matched`` down by statement
    (SOFP/SOPL/…). Both default empty so a bare ``ScoreCard()`` and every legacy
    caller keep working, and the headline ``score`` is unaffected by either.
    """
    gold_cells: int = 0
    matched: int = 0
    missing: int = 0
    mismatch: int = 0
    extra: int = 0
    scale_mismatch: int = 0
    taxonomy: dict[str, int] = field(default_factory=dict)
    per_statement: dict[str, dict[str, int]] = field(default_factory=dict)

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


def is_sign_flip(run: float, gold: float) -> bool:
    """True when ``run`` is the exact negation of a non-zero ``gold``."""
    if gold == 0:
        return False
    return math.isclose(run, -gold, rel_tol=1e-6, abs_tol=_EPSILON)


def _present_map(
    facts: dict[tuple[str, str, str], tuple]
) -> dict[tuple[str, str, str], float]:
    """{key: present number}, skipping not_disclosed / blank cells."""
    out: dict[tuple[str, str, str], float] = {}
    for key, (value, status) in facts.items():
        num = _present_number(value, status)
        if num is not None:
            out[key] = num
    return out


def _is_axis_swap(
    key: tuple[str, str, str],
    other_key: tuple[str, str, str],
    gold_present: dict[tuple[str, str, str], float],
    run_present: dict[tuple[str, str, str], float],
) -> bool:
    """True when ``key`` and ``other_key`` have their gold values transposed in
    the run (run[key] == gold[other] AND run[other] == gold[key]), with the two
    gold values distinct so the swap is observable."""
    if other_key not in gold_present:
        return False
    if key not in run_present or other_key not in run_present:
        return False
    g_this = gold_present[key]
    g_other = gold_present[other_key]
    if _values_equal(g_this, g_other):
        return False
    return _values_equal(run_present[key], g_other) and _values_equal(
        run_present[other_key], g_this
    )


def classify_failures(
    gold: dict[tuple[str, str, str], tuple],
    run: dict[tuple[str, str, str], tuple],
) -> dict[str, int]:
    """Diagnose every WRONG gold slot into the failure taxonomy.

    Pure: takes the two fact maps (as produced by :func:`_gradeable_facts`) and
    returns a count per taxonomy key. Guarantees
    ``sum(result.values()) == missing + mismatch`` — a partition of the wrong
    slots. Priority for a mismatch: period-swap → scope-swap → sign → scale →
    plain-wrong (most structural / most actionable first).
    """
    gold_present = _present_map(gold)
    run_present = _present_map(run)

    # For "misplaced": a unique, non-zero gold number that shows up as an EXTRA
    # run value (a run value on a slot the gold left blank) is "right number,
    # wrong row". Precompute the extra-value set and gold multiplicities.
    extra_values = {
        num for key, num in run_present.items() if key not in gold_present
    }
    gold_counts = Counter(gold_present.values())

    tax = empty_taxonomy()
    for key, g in gold_present.items():
        uuid, period, scope = key
        run_fact = run.get(key)
        r = _present_number(run_fact[0], run_fact[1]) if run_fact else None

        if r is None:
            # --- missing bucket ---
            if run_fact is not None and run_fact[1] == "not_disclosed":
                tax["false_not_disclosed"] += 1
            elif g != 0 and gold_counts[g] == 1 and g in extra_values:
                tax["misplaced"] += 1
            else:
                tax["unaddressed"] += 1
            continue

        if _values_equal(r, g):
            continue  # matched — not a failure

        # --- mismatch bucket (priority order) ---
        other_period = "PY" if period == "CY" else "CY"
        other_scope = "Group" if scope == "Company" else "Company"
        if _is_axis_swap(key, (uuid, other_period, scope), gold_present, run_present):
            tax["period_swap"] += 1
        elif _is_axis_swap(key, (uuid, period, other_scope), gold_present, run_present):
            tax["scope_swap"] += 1
        elif is_sign_flip(r, g):
            tax["sign_flip"] += 1
        elif is_scale_mismatch(r, g):
            tax["scale"] += 1
        else:
            tax["plain_wrong"] += 1
    return tax


def _statement_by_concept(
    conn: sqlite3.Connection, benchmark_id: int, template_ids: list[str]
) -> dict[str, str]:
    """Map each gradeable concept_uuid to its statement type (SOFP/SOPL/…).

    Statement lives on ``eval_benchmark_templates`` per template_id; concepts
    carry their template_id, so this joins the two. Concepts whose template
    isn't in the benchmark set are simply absent (grading already excludes
    them)."""
    if not template_ids:
        return {}
    placeholders = ",".join("?" for _ in template_ids)
    sql = (
        "SELECT n.concept_uuid, t.statement_type "
        "FROM concept_nodes n "
        "JOIN eval_benchmark_templates t ON t.template_id = n.template_id "
        f"WHERE t.benchmark_id = ? AND n.template_id IN ({placeholders})"
    )
    return {
        r[0]: r[1]
        for r in conn.execute(sql, (benchmark_id, *template_ids)).fetchall()
    }


def per_statement_breakdown(
    conn: sqlite3.Connection,
    benchmark_id: int,
    template_ids: list[str],
    gold: dict[tuple[str, str, str], tuple],
    run: dict[tuple[str, str, str], tuple],
) -> dict[str, dict[str, int]]:
    """Break gold_cells / matched down by statement, so a change can be traced
    to one statement (e.g. "SOCF went 84% → 93%, nothing else moved")."""
    stmt_by_concept = _statement_by_concept(conn, benchmark_id, template_ids)
    gold_present = _present_map(gold)
    out: dict[str, dict[str, int]] = {}
    for key, g in gold_present.items():
        stmt = stmt_by_concept.get(key[0], "OTHER")
        bucket = out.setdefault(stmt, {"gold_cells": 0, "matched": 0})
        bucket["gold_cells"] += 1
        run_fact = run.get(key)
        r = _present_number(run_fact[0], run_fact[1]) if run_fact else None
        if r is not None and _values_equal(r, g):
            bucket["matched"] += 1
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

    # v30: diagnose the wrong slots and break the score down by statement. Both
    # are pure additions layered on the same maps — the headline is untouched.
    card.taxonomy = classify_failures(gold, run)
    card.per_statement = per_statement_breakdown(
        conn, benchmark_id, template_ids, gold, run
    )
    return card
