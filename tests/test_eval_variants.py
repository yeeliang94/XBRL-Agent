"""Shared benchmark→variant recovery (PLAN-evals-hardening Step 3).

The reverse map (benchmark template_id → statement variant) moved from the
regression CLI into eval/variants.py so the suite runner applies the same
recovery. The CLI's own behaviour stays pinned by test_eval_regression.py;
these tests cover the shared module + the suite-side wiring.
"""
from __future__ import annotations

import sqlite3

import pytest

from eval.variants import (
    benchmark_variants,
    benchmark_variants_for,
    variant_conflicts,
)


# --- variant_conflicts (pure) ----------------------------------------------

def test_no_conflict_when_no_overlap():
    assert variant_conflicts({"SOPL": "Function"}, {"SOFP": "CuNonCu"}) == []


def test_no_conflict_when_same_variant():
    assert variant_conflicts({"SOFP": "CuNonCu"}, {"SOFP": "CuNonCu"}) == []


def test_conflict_when_requested_contradicts_gold():
    assert variant_conflicts(
        {"SOFP": "OrderOfLiquidity"}, {"SOFP": "CuNonCu"}
    ) == ["SOFP"]


# --- benchmark_variants_for (DB read) ---------------------------------------

def _real_template_id(statement: str, variant: str) -> str:
    from concept_model.parser import _derive_template_id
    from statement_types import StatementType, template_path

    return _derive_template_id(
        template_path(StatementType(statement), variant, "company", "mfrs")
    )


@pytest.fixture
def db(tmp_path):
    from db.schema import init_db

    path = tmp_path / "t.db"
    init_db(path)
    conn = sqlite3.connect(str(path))
    yield conn
    conn.close()


def test_benchmark_variants_for_reads_template_set(db):
    tid = _real_template_id("SOFP", "CuNonCu")
    db.execute(
        "INSERT INTO eval_benchmarks(name, filing_standard, filing_level) "
        "VALUES ('B', 'mfrs', 'company')"
    )
    bid = db.execute("SELECT id FROM eval_benchmarks").fetchone()[0]
    db.execute(
        "INSERT OR IGNORE INTO concept_templates(template_id, source_path) "
        "VALUES (?, '/tmp/x')",
        (tid,),
    )
    db.execute(
        "INSERT INTO eval_benchmark_templates(benchmark_id, template_id, "
        "statement_type) VALUES (?, ?, 'SOFP')",
        (bid, tid),
    )
    db.commit()
    assert benchmark_variants_for(db, bid, "mfrs", "company") == {"SOFP": "CuNonCu"}


def test_benchmark_variants_for_none_benchmark_is_empty(db):
    assert benchmark_variants_for(db, None, "mfrs", "company") == {}


def test_benchmark_variants_ignores_unknown_ids():
    assert benchmark_variants("mfrs", "company", ["bogus-v1"]) == {}
