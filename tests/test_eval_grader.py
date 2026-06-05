"""Unit tests for the pure grading core (eval/grader.py).

Hand-built fixtures cover every branch of the grading algorithm so the
scorecard maths is pinned independently of any wiring: exact match, ``1`` vs
``1.0``, a 1000x scale mismatch, a missing cell, an extra cell, a
``not_disclosed`` gold cell, an ``explicit_zero`` match, and a COMPUTED gold
fact that must be excluded.
"""
from __future__ import annotations

import sqlite3

from db.schema import init_db
from db import repository as repo
from eval.grader import (
    ScoreCard,
    grade_run,
    is_scale_mismatch,
    normalize,
)


# A single template with a handful of concepts of each kind. Rows/cols are
# arbitrary — the grader keys on concept_uuid, not geometry.
_TEMPLATE_ID = "mfrs-company-sofp-cunoncu-v1"
_CONCEPTS = [
    # (uuid, kind)
    ("c_cash", "LEAF"),
    ("c_recv", "LEAF"),
    ("c_ppe", "LEAF"),
    ("c_invs", "LEAF"),
    ("c_zero", "LEAF"),
    ("c_nd", "LEAF"),
    ("c_extra", "LEAF"),
    ("c_total", "COMPUTED"),   # must be excluded from grading
    ("c_matrix", "MATRIX_CELL"),
]


def _seed(tmp_path):
    """Build a fresh DB with one benchmark + template and return (conn, ids)."""
    db = tmp_path / "grader.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path) VALUES (?, ?)",
        (_TEMPLATE_ID, "/tmp/t.xlsx"),
    )
    for i, (uuid, kind) in enumerate(_CONCEPTS):
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, ?, ?, ?, 'SOFP', ?, 'B')",
            (uuid, _TEMPLATE_ID, kind, uuid, 5 + i),
        )

    cur = conn.execute(
        "INSERT INTO eval_benchmarks(name, filing_standard, filing_level) "
        "VALUES ('B', 'mfrs', 'company')"
    )
    bench_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO eval_benchmark_templates(benchmark_id, template_id, "
        "statement_type) VALUES (?, ?, 'SOFP')",
        (bench_id, _TEMPLATE_ID),
    )

    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, benchmark_id) "
        "VALUES ('2026-06-04T00:00:00Z', 'x.pdf', 'completed', ?)",
        (bench_id,),
    )
    run_id = int(cur.lastrowid)
    conn.commit()
    return conn, run_id, bench_id


def _gold(conn, bench_id, uuid, value, status="observed", period="CY",
          scope="Company"):
    conn.execute(
        "INSERT INTO gold_concept_facts(benchmark_id, concept_uuid, period, "
        "entity_scope, value, value_status) VALUES (?, ?, ?, ?, ?, ?)",
        (bench_id, uuid, period, scope, value, status),
    )


def _run_fact(conn, run_id, uuid, value, status="observed", period="CY",
              scope="Company"):
    conn.execute(
        "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, uuid, period, scope, value, status),
    )


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def test_normalize_treats_int_and_float_equal():
    assert normalize(1) == normalize(1.0) == 1.0
    assert normalize(None) is None
    assert normalize("not a number") is None


def test_is_scale_mismatch_covers_thousands_and_millions():
    assert is_scale_mismatch(1000.0, 1.0)        # 10^3
    assert is_scale_mismatch(1.0, 1000.0)        # 10^-3
    assert is_scale_mismatch(1_000_000.0, 1.0)   # 10^6
    assert is_scale_mismatch(10.0, 1.0)          # 10^1
    assert not is_scale_mismatch(7.0, 1.0)       # not a power of ten
    assert not is_scale_mismatch(5.0, 0.0)       # zero gold can't scale


def test_score_property_zero_gold_is_zero():
    assert ScoreCard().score == 0.0


# --------------------------------------------------------------------------
# Full grading
# --------------------------------------------------------------------------

def test_grade_run_covers_every_branch(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path)
    try:
        # Gold cells (the truth set).
        _gold(conn, bench_id, "c_cash", 1595.0)      # exact match
        _gold(conn, bench_id, "c_recv", 1.0)         # 1 vs 1.0 → match
        _gold(conn, bench_id, "c_ppe", 200.0)        # run is 200000 → scale
        _gold(conn, bench_id, "c_invs", 50.0)        # run absent → missing
        _gold(conn, bench_id, "c_zero", 0.0,
              status="explicit_zero")                # explicit_zero match
        _gold(conn, bench_id, "c_nd", None,
              status="not_disclosed")                # excluded from denom
        _gold(conn, bench_id, "c_total", 9999.0)     # COMPUTED → excluded
        conn.commit()

        # Run facts.
        _run_fact(conn, run_id, "c_cash", 1595.0)    # match
        _run_fact(conn, run_id, "c_recv", 1)         # int → normalises to 1.0
        _run_fact(conn, run_id, "c_ppe", 200000.0)   # 1000x → scale mismatch
        # c_invs intentionally absent → missing
        _run_fact(conn, run_id, "c_zero", 0.0,
                  status="explicit_zero")            # match
        _run_fact(conn, run_id, "c_nd", 42.0)        # run value at ND gold → ignored
        _run_fact(conn, run_id, "c_extra", 7.0)      # no gold here → extra
        _run_fact(conn, run_id, "c_total", 9999.0)   # COMPUTED → not counted
        conn.commit()

        card = grade_run(conn, run_id, bench_id)

        # Denominator excludes not_disclosed gold + COMPUTED gold.
        # Gradeable gold: cash, recv, ppe, invs, zero = 5.
        assert card.gold_cells == 5
        assert card.matched == 3          # cash, recv, zero
        assert card.mismatch == 1         # ppe (scale counts as mismatch)
        assert card.scale_mismatch == 1   # ppe tagged
        assert card.missing == 1          # invs
        assert card.extra == 1            # c_extra only (c_nd ignored, c_total excluded)
        # score = matched / gold_cells
        assert abs(card.score - 3 / 5) < 1e-9
    finally:
        conn.close()


def test_run_value_at_not_disclosed_gold_is_not_extra(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path)
    try:
        _gold(conn, bench_id, "c_nd", None, status="not_disclosed")
        _run_fact(conn, run_id, "c_nd", 123.0)
        conn.commit()
        card = grade_run(conn, run_id, bench_id)
        assert card.gold_cells == 0
        assert card.extra == 0   # ignored, not an extra
    finally:
        conn.close()


def test_computed_gold_excluded_even_if_run_matches(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path)
    try:
        _gold(conn, bench_id, "c_total", 9999.0)   # COMPUTED
        _run_fact(conn, run_id, "c_total", 9999.0)
        conn.commit()
        card = grade_run(conn, run_id, bench_id)
        assert card.gold_cells == 0   # COMPUTED never graded
        assert card.matched == 0
        assert card.extra == 0
    finally:
        conn.close()


def test_save_and_fetch_eval_score_upserts(tmp_path):
    """save_eval_score / fetch_eval_score round-trip + UNIQUE upsert."""
    conn, run_id, bench_id = _seed(tmp_path)
    try:
        card = ScoreCard(gold_cells=10, matched=8, missing=1, mismatch=1,
                         extra=2, scale_mismatch=1)
        repo.save_eval_score(conn, run_id, bench_id, card)
        conn.commit()

        got = repo.fetch_eval_score(conn, run_id, bench_id)
        assert got["gold_cells"] == 10
        assert got["matched_cells"] == 8
        assert got["extra_cells"] == 2
        assert got["scale_mismatch"] == 1
        assert abs(got["score"] - 0.8) < 1e-9

        # Re-grade overwrites (UNIQUE(run_id, benchmark_id)).
        card2 = ScoreCard(gold_cells=10, matched=9, missing=1, mismatch=0,
                          extra=0, scale_mismatch=0)
        repo.save_eval_score(conn, run_id, bench_id, card2)
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM eval_scores WHERE run_id = ? AND benchmark_id = ?",
            (run_id, bench_id),
        ).fetchone()[0] == 1
        again = repo.fetch_eval_score(conn, run_id, bench_id)
        assert again["matched_cells"] == 9
        assert abs(again["score"] - 0.9) < 1e-9

        # Benchmark-agnostic convenience fetch finds it too.
        by_run = repo.fetch_eval_score_for_run(conn, run_id)
        assert by_run["matched_cells"] == 9
    finally:
        conn.close()


def test_fetch_eval_score_missing_returns_none(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path)
    try:
        assert repo.fetch_eval_score(conn, run_id, bench_id) is None
        assert repo.fetch_eval_score_for_run(conn, run_id) is None
    finally:
        conn.close()


def test_template_scoping_excludes_other_template_facts(tmp_path):
    """Gold/run facts on a concept outside the benchmark's template set are
    never graded (gotcha #21 — uuids differ per variant)."""
    conn, run_id, bench_id = _seed(tmp_path)
    try:
        # A second template NOT in the benchmark set.
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path) "
            "VALUES ('other-tpl', '/tmp/o.xlsx')"
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES ('o_cash', 'other-tpl', 'LEAF', 'Cash', 'SOFP', 5, 'B')"
        )
        _gold(conn, bench_id, "o_cash", 100.0)
        _run_fact(conn, run_id, "o_cash", 999.0)
        conn.commit()
        card = grade_run(conn, run_id, bench_id)
        assert card.gold_cells == 0   # out-of-set concept ignored
    finally:
        conn.close()
