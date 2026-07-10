"""Reviewer lift — what the reviewer pass contributes (Evals workspace, E5).

Grades the pre-reviewer fact snapshot (run_fact_snapshots) against the final
facts. A fixture where the reviewer corrected two slots → lift = +2 matched.
"""
from __future__ import annotations

import sqlite3

from db.schema import init_db
from eval.grader import reviewer_lift

_TEMPLATE_ID = "mfrs-company-sofp-cunoncu-v1"
_CONCEPTS = ["c_cash", "c_recv", "c_ppe", "c_invs"]


def _seed(tmp_path):
    db = tmp_path / "lift.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path) VALUES (?, ?)",
        (_TEMPLATE_ID, "/tmp/t.xlsx"),
    )
    for i, uuid in enumerate(_CONCEPTS):
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, ?, 'LEAF', ?, 'SOFP', ?, 'B')",
            (uuid, _TEMPLATE_ID, uuid, 5 + i),
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
        "VALUES ('t', 'x.pdf', 'completed', ?)",
        (bench_id,),
    )
    run_id = int(cur.lastrowid)
    conn.commit()
    return conn, run_id, bench_id


def _gold(conn, bench_id, uuid, value):
    conn.execute(
        "INSERT INTO gold_concept_facts(benchmark_id, concept_uuid, period, "
        "entity_scope, value, value_status) VALUES (?, ?, 'CY', 'Company', ?, 'observed')",
        (bench_id, uuid, value),
    )


def _final(conn, run_id, uuid, value):
    conn.execute(
        "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status) VALUES (?, ?, 'CY', 'Company', ?, 'observed')",
        (run_id, uuid, value),
    )


def _snap(conn, run_id, uuid, value):
    conn.execute(
        "INSERT INTO run_fact_snapshots(run_id, concept_uuid, period, "
        "entity_scope, value, value_status) VALUES (?, ?, 'CY', 'Company', ?, 'observed')",
        (run_id, uuid, value),
    )


def test_reviewer_lift_counts_corrected_slots(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path)
    try:
        # Gold: all four right values.
        for uuid, v in zip(_CONCEPTS, [10.0, 20.0, 30.0, 40.0]):
            _gold(conn, bench_id, uuid, v)
        # Pre-reviewer snapshot: two wrong (c_ppe, c_invs).
        _snap(conn, run_id, "c_cash", 10.0)   # right
        _snap(conn, run_id, "c_recv", 20.0)   # right
        _snap(conn, run_id, "c_ppe", 999.0)   # wrong
        _snap(conn, run_id, "c_invs", 888.0)  # wrong
        # Final facts: reviewer fixed both.
        _final(conn, run_id, "c_cash", 10.0)
        _final(conn, run_id, "c_recv", 20.0)
        _final(conn, run_id, "c_ppe", 30.0)   # corrected
        _final(conn, run_id, "c_invs", 40.0)  # corrected
        conn.commit()

        lift = reviewer_lift(conn, run_id, bench_id)
        assert lift is not None
        assert lift["pre_matched"] == 2
        assert lift["final_matched"] == 4
        assert lift["lift_slots"] == 2
        assert lift["gold_cells"] == 4
    finally:
        conn.close()


def test_reviewer_lift_none_without_snapshot(tmp_path):
    conn, run_id, bench_id = _seed(tmp_path)
    try:
        _gold(conn, bench_id, "c_cash", 10.0)
        _final(conn, run_id, "c_cash", 10.0)
        conn.commit()
        # No run_fact_snapshots rows → no reviewer pass ran → None.
        assert reviewer_lift(conn, run_id, bench_id) is None
    finally:
        conn.close()
