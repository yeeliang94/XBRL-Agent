"""Grader failure taxonomy + per-statement breakdown (Step B1).

The taxonomy partitions every WRONG gold slot into a diagnosed failure mode
without changing the headline score. Two load-bearing invariants:

  * headline byte-identity — adding the taxonomy must not move card.score /
    matched / missing / mismatch on the existing fixtures;
  * partition — sum(taxonomy.values()) == missing + mismatch.
"""
from __future__ import annotations

import sqlite3

from db import repository as repo
from db.schema import init_db
from eval.grader import (
    ScoreCard,
    classify_failures,
    empty_taxonomy,
    grade_run,
    is_sign_flip,
)

_TEMPLATE_ID = "mfrs-company-sofp-cunoncu-v1"


def _seed(tmp_path, level="company"):
    db = tmp_path / "tax.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path) VALUES (?, ?)",
        (_TEMPLATE_ID, "/tmp/t.xlsx"),
    )
    cur = conn.execute(
        "INSERT INTO eval_benchmarks(name, filing_standard, filing_level) "
        "VALUES ('B', 'mfrs', ?)",
        (level,),
    )
    bench_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO eval_benchmark_templates(benchmark_id, template_id, "
        "statement_type) VALUES (?, ?, 'SOFP')",
        (bench_id, _TEMPLATE_ID),
    )
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, benchmark_id) "
        "VALUES ('t', 'x.pdf', 'completed', ?)",
        (bench_id,),
    )
    run_id = int(cur.lastrowid)
    return conn, run_id, bench_id


def _concept(conn, uuid, kind="LEAF", sheet="SOFP", row=5):
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) "
        "VALUES (?, ?, ?, ?, ?, ?, 'B')",
        (uuid, _TEMPLATE_ID, kind, uuid, sheet, row),
    )


def _gold(conn, bench_id, uuid, value, status="observed", period="CY", scope="Company"):
    conn.execute(
        "INSERT INTO gold_concept_facts(benchmark_id, concept_uuid, period, "
        "entity_scope, value, value_status) VALUES (?, ?, ?, ?, ?, ?)",
        (bench_id, uuid, period, scope, value, status),
    )


def _run(conn, run_id, uuid, value, status="observed", period="CY", scope="Company"):
    conn.execute(
        "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, uuid, period, scope, value, status),
    )


# --- pure helpers ----------------------------------------------------------

def test_is_sign_flip():
    assert is_sign_flip(-5.0, 5.0)
    assert is_sign_flip(5.0, -5.0)
    assert not is_sign_flip(5.0, 5.0)
    assert not is_sign_flip(0.0, 0.0)


def test_empty_taxonomy_has_all_keys():
    tax = empty_taxonomy()
    assert set(tax) == {
        "period_swap", "scope_swap", "sign_flip", "scale", "plain_wrong",
        "false_not_disclosed", "misplaced", "unaddressed",
    }
    assert all(v == 0 for v in tax.values())


# --- each diagnosis ---------------------------------------------------------

def test_sign_flip_diagnosis(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path)
    _concept(conn, "a")
    _gold(conn, bench_id, "a", 100.0)
    _run(conn, run_id, "a", -100.0)
    conn.commit()
    card = grade_run(conn, run_id, bench_id)
    assert card.mismatch == 1
    assert card.taxonomy["sign_flip"] == 1
    assert sum(card.taxonomy.values()) == card.missing + card.mismatch


def test_scale_diagnosis(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path)
    _concept(conn, "a")
    _gold(conn, bench_id, "a", 200.0)
    _run(conn, run_id, "a", 200000.0)
    conn.commit()
    card = grade_run(conn, run_id, bench_id)
    assert card.taxonomy["scale"] == 1


def test_period_swap_diagnosis(tmp_path):
    """CY/PY transposed for one line item → BOTH slots are period_swap, not two
    plain mismatches."""
    conn, run_id, bench_id = _seed(tmp_path)
    _concept(conn, "a")
    _gold(conn, bench_id, "a", 100.0, period="CY")
    _gold(conn, bench_id, "a", 90.0, period="PY")
    _run(conn, run_id, "a", 90.0, period="CY")   # holds gold PY
    _run(conn, run_id, "a", 100.0, period="PY")  # holds gold CY
    conn.commit()
    card = grade_run(conn, run_id, bench_id)
    assert card.mismatch == 2
    assert card.taxonomy["period_swap"] == 2
    assert card.taxonomy["plain_wrong"] == 0


def test_scope_swap_diagnosis(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path, level="group")
    _concept(conn, "a")
    _gold(conn, bench_id, "a", 500.0, scope="Group")
    _gold(conn, bench_id, "a", 400.0, scope="Company")
    _run(conn, run_id, "a", 400.0, scope="Group")    # holds gold Company
    _run(conn, run_id, "a", 500.0, scope="Company")  # holds gold Group
    conn.commit()
    card = grade_run(conn, run_id, bench_id)
    assert card.taxonomy["scope_swap"] == 2


def test_misplaced_diagnosis(tmp_path):
    """Gold value 777 is unique+nonzero, missing at 'a', but appears exactly at
    gold-blank slot 'b' → 'a' is diagnosed misplaced (right number, wrong row)."""
    conn, run_id, bench_id = _seed(tmp_path)
    _concept(conn, "a")
    _concept(conn, "b")
    _gold(conn, bench_id, "a", 777.0)   # missing in run
    _run(conn, run_id, "b", 777.0)      # extra — the number went here
    conn.commit()
    card = grade_run(conn, run_id, bench_id)
    assert card.missing == 1
    assert card.extra == 1
    assert card.taxonomy["misplaced"] == 1
    assert card.taxonomy["unaddressed"] == 0


def test_false_not_disclosed_vs_unaddressed(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path)
    _concept(conn, "a")
    _concept(conn, "b")
    _gold(conn, bench_id, "a", 10.0)
    _gold(conn, bench_id, "b", 20.0)
    _run(conn, run_id, "a", None, status="not_disclosed")  # run asserted absence
    # b: no run fact at all → unaddressed
    conn.commit()
    card = grade_run(conn, run_id, bench_id)
    assert card.missing == 2
    assert card.taxonomy["false_not_disclosed"] == 1
    assert card.taxonomy["unaddressed"] == 1


def test_per_statement_breakdown(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path)
    _concept(conn, "a")
    _concept(conn, "b")
    _gold(conn, bench_id, "a", 10.0)
    _gold(conn, bench_id, "b", 20.0)
    _run(conn, run_id, "a", 10.0)   # matched
    _run(conn, run_id, "b", 99.0)   # wrong
    conn.commit()
    card = grade_run(conn, run_id, bench_id)
    assert card.per_statement == {"SOFP": {"gold_cells": 2, "matched": 1}}


def test_headline_unchanged_and_partition_holds(tmp_path):
    """A mixed case: score/counts are the classic grader's, and the taxonomy
    partitions the wrong set exactly."""
    conn, run_id, bench_id = _seed(tmp_path)
    for name in ("a", "b", "c", "d"):
        _concept(conn, name)
    _gold(conn, bench_id, "a", 100.0)   # matched
    _gold(conn, bench_id, "b", 50.0)    # sign flip
    _gold(conn, bench_id, "c", 200.0)   # scale
    _gold(conn, bench_id, "d", 5.0)     # unaddressed (missing)
    _run(conn, run_id, "a", 100.0)
    _run(conn, run_id, "b", -50.0)
    _run(conn, run_id, "c", 200000.0)
    conn.commit()
    card = grade_run(conn, run_id, bench_id)
    assert card.gold_cells == 4
    assert card.matched == 1
    assert card.mismatch == 2
    assert card.missing == 1
    assert abs(card.score - 0.25) < 1e-9
    assert sum(card.taxonomy.values()) == card.missing + card.mismatch == 3
    assert card.taxonomy["sign_flip"] == 1
    assert card.taxonomy["scale"] == 1
    assert card.taxonomy["unaddressed"] == 1


def test_persist_and_read_back_taxonomy(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path)
    _concept(conn, "a")
    _gold(conn, bench_id, "a", 100.0)
    _run(conn, run_id, "a", -100.0)
    conn.commit()
    card = grade_run(conn, run_id, bench_id)
    repo.save_eval_score(conn, run_id, bench_id, card)
    conn.commit()
    read = repo.fetch_eval_score(conn, run_id, bench_id)
    assert read["taxonomy"]["sign_flip"] == 1
    assert read["per_statement"] == {"SOFP": {"gold_cells": 1, "matched": 0}}


def test_legacy_scorecard_persists_null_taxonomy(tmp_path):
    """A ScoreCard with no taxonomy (older path) stores NULL, reads back None."""
    conn, run_id, bench_id = _seed(tmp_path)
    conn.commit()
    repo.save_eval_score(
        conn, run_id, bench_id, ScoreCard(gold_cells=2, matched=2)
    )
    conn.commit()
    read = repo.fetch_eval_score(conn, run_id, bench_id)
    assert read["taxonomy"] is None
    assert read["per_statement"] is None
