"""Suite-run comparison + trends (Evals workspace, Step F2).

Compares two suite runs of one suite: per-document accuracy deltas, aggregate
delta over common documents, union handling for differing document sets, the
gold-changed-between warning, and the slot-level diff drill-down.
"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_db
from eval.compare import compare_suite_runs, slot_level_diff, suite_run_aggregate

_TEMPLATE_ID = "mfrs-company-sofp-cunoncu-v1"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "cmp.db"
    init_db(path)
    conn = sqlite3.connect(str(path))
    # One template + a couple of LEAF concepts for the slot diff.
    conn.execute("INSERT INTO concept_templates(template_id, source_path) VALUES (?, '/t')", (_TEMPLATE_ID,))
    for uuid in ("c1", "c2"):
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, ?, 'LEAF', ?, 'SOFP', 5, 'B')",
            (uuid, _TEMPLATE_ID, uuid),
        )
    # A benchmark for doc 1.
    conn.execute("INSERT INTO eval_benchmarks(name, filing_standard, filing_level) VALUES ('B','mfrs','company')")
    conn.execute("INSERT INTO eval_benchmark_templates(benchmark_id, template_id, statement_type) VALUES (1, ?, 'SOFP')", (_TEMPLATE_ID,))
    # Gold: c1=10, c2=20.
    for uuid, v in (("c1", 10.0), ("c2", 20.0)):
        conn.execute(
            "INSERT INTO gold_concept_facts(benchmark_id, concept_uuid, period, "
            "entity_scope, value, value_status, updated_at) VALUES (1, ?, 'CY', 'Company', ?, 'observed', '2026-01-01')",
            (uuid, v),
        )
    # Suite + docs.
    conn.execute("INSERT INTO eval_suites(name, created_at) VALUES ('S','2026-01-01')")
    conn.execute("INSERT INTO eval_suite_docs(suite_id, label, benchmark_id, filing_standard, filing_level) VALUES (1, 'doc1', 1, 'mfrs', 'company')")  # id 1
    conn.execute("INSERT INTO eval_suite_docs(suite_id, label, filing_standard, filing_level) VALUES (1, 'doc2', 'mfrs', 'company')")  # id 2, no gold
    conn.commit()
    return conn


def _suite_run(conn, created_at):
    cur = conn.execute(
        "INSERT INTO eval_suite_runs(suite_id, config_json, status, created_at) "
        "VALUES (1, '{}', 'complete', ?)", (created_at,)
    )
    return int(cur.lastrowid)


def _child(conn, suite_run_id, doc_id, *, accuracy_num, benchmark_id=None):
    """A completed child run with a persisted eval_scores row giving an
    accuracy of accuracy_num/2 (2 gold cells)."""
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, session_id, suite_run_id, benchmark_id) "
        "VALUES ('t', 'x.pdf', 'completed', ?, ?, ?)",
        (f"suite-{suite_run_id}-doc-{doc_id}", suite_run_id, benchmark_id),
    )
    run_id = int(cur.lastrowid)
    if benchmark_id is not None:
        conn.execute(
            "INSERT INTO eval_scores(run_id, benchmark_id, gold_cells, matched_cells, "
            "missing_cells, mismatch_cells, extra_cells, scale_mismatch) "
            "VALUES (?, ?, 2, ?, 0, 0, 0, 0)",
            (run_id, benchmark_id, accuracy_num),
        )
    return run_id


def test_compare_reports_per_document_and_aggregate_delta(db):
    conn = db
    a = _suite_run(conn, "2026-01-10")
    b = _suite_run(conn, "2026-02-10")
    # doc1 has gold: A matched 1/2 (0.5), B matched 2/2 (1.0) → +0.5.
    _child(conn, a, 1, accuracy_num=1, benchmark_id=1)
    _child(conn, b, 1, accuracy_num=2, benchmark_id=1)
    # doc2 has no gold in either → excluded from aggregate.
    _child(conn, a, 2, accuracy_num=0)
    _child(conn, b, 2, accuracy_num=0)
    conn.commit()

    cmp = compare_suite_runs(conn, a, b)
    doc1 = next(r for r in cmp["documents"] if r["doc_id"] == 1)
    assert doc1["accuracy_a"] == 0.5
    assert doc1["accuracy_b"] == 1.0
    assert abs(doc1["delta"] - 0.5) < 1e-9
    assert abs(cmp["aggregate_delta"] - 0.5) < 1e-9
    assert cmp["common_documents"] == 1  # only doc1 is graded in both


def test_compare_union_handles_differing_document_sets(db):
    conn = db
    a = _suite_run(conn, "2026-01-10")
    b = _suite_run(conn, "2026-02-10")
    _child(conn, a, 1, accuracy_num=2, benchmark_id=1)
    _child(conn, a, 2, accuracy_num=0)
    # Suite run B only ran doc1.
    _child(conn, b, 1, accuracy_num=2, benchmark_id=1)
    conn.commit()

    cmp = compare_suite_runs(conn, a, b)
    doc2 = next(r for r in cmp["documents"] if r["doc_id"] == 2)
    assert doc2["in_both"] is False
    assert doc2["delta"] is None
    assert cmp["only_in_one"] == 1


def test_gold_changed_between_warns(db):
    conn = db
    a = _suite_run(conn, "2026-01-05")
    b = _suite_run(conn, "2026-03-05")
    _child(conn, a, 1, accuracy_num=2, benchmark_id=1)
    _child(conn, b, 1, accuracy_num=2, benchmark_id=1)
    # Edit gold between the two runs.
    conn.execute("UPDATE gold_concept_facts SET updated_at='2026-02-01' WHERE concept_uuid='c1'")
    conn.commit()

    cmp = compare_suite_runs(conn, a, b)
    assert cmp["gold_changed_any"] is True


def test_slot_level_diff_finds_regressions_and_fixes(db):
    conn = db
    a = _suite_run(conn, "2026-01-10")
    b = _suite_run(conn, "2026-02-10")
    ra = _child(conn, a, 1, accuracy_num=2, benchmark_id=1)
    rb = _child(conn, b, 1, accuracy_num=2, benchmark_id=1)
    # Run A: c1 right (10), c2 wrong (99). Run B: c1 wrong (99), c2 right (20).
    for run_id, c1v, c2v in ((ra, 10.0, 99.0), (rb, 99.0, 20.0)):
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, entity_scope, value, value_status) "
            "VALUES (?, 'c1', 'CY', 'Company', ?, 'observed')", (run_id, c1v))
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, entity_scope, value, value_status) "
            "VALUES (?, 'c2', 'CY', 'Company', ?, 'observed')", (run_id, c2v))
    conn.commit()

    diff = slot_level_diff(conn, ra, rb, 1)
    reg_keys = {tuple(r["key"]) for r in diff["regressions"]}
    fix_keys = {tuple(r["key"]) for r in diff["fixes"]}
    assert ("c1", "CY", "Company") in reg_keys  # right in A, wrong in B
    assert ("c2", "CY", "Company") in fix_keys  # wrong in A, right in B


def test_suite_run_aggregate_keys_documents(db):
    conn = db
    a = _suite_run(conn, "2026-01-10")
    _child(conn, a, 1, accuracy_num=1, benchmark_id=1)
    conn.commit()
    agg = suite_run_aggregate(conn, a)
    assert "1" in agg["documents"]
    assert agg["aggregate"]["mean_accuracy"] == 0.5
