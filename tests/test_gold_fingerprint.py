"""Gold-change guard (PLAN-evals-hardening Steps 7-8, schema v33).

Scores stay stamped-at-grade-time (the PRD design), but every stamp now
carries a content fingerprint of the gold it was graded against — so ANY
later change (edit, deletion, benchmark reassignment) is detected reliably,
including the cases the old timestamp-window heuristic missed. Plus the
one-click re-grade endpoint.
"""
from __future__ import annotations

import importlib
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from db.schema import init_db
from db import repository as repo
from eval.store import gold_fingerprint

_TEMPLATE_ID = "mfrs-company-sofp-cunoncu-v1"


def _seed(conn):
    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path) VALUES (?, '/t')",
        (_TEMPLATE_ID,),
    )
    for uuid in ("c1", "c2"):
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, ?, 'LEAF', ?, 'SOFP', 5, 'B')",
            (uuid, _TEMPLATE_ID, uuid),
        )
    for bid in (1, 2):
        conn.execute(
            "INSERT INTO eval_benchmarks(name, filing_standard, filing_level) "
            "VALUES (?, 'mfrs', 'company')", (f"B{bid}",),
        )
        conn.execute(
            "INSERT INTO eval_benchmark_templates(benchmark_id, template_id, "
            "statement_type) VALUES (?, ?, 'SOFP')", (bid, _TEMPLATE_ID),
        )
    for bid in (1, 2):
        for uuid, v in (("c1", 10.0), ("c2", 20.0)):
            conn.execute(
                "INSERT INTO gold_concept_facts(benchmark_id, concept_uuid, "
                "period, entity_scope, value, value_status, updated_at) "
                "VALUES (?, ?, 'CY', 'Company', ?, 'observed', '2026-01-01')",
                (bid, uuid, v),
            )
    conn.commit()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "fp.db"
    init_db(path)
    conn = sqlite3.connect(str(path))
    _seed(conn)
    yield conn, path
    conn.close()


class _Card:
    gold_cells = 2
    matched = 2
    missing = 0
    mismatch = 0
    extra = 0
    scale_mismatch = 0


def test_fingerprint_detects_edit_deletion_and_reassignment(db):
    conn, _ = db
    fp0 = gold_fingerprint(conn, 1)
    # Stable under no-op.
    assert gold_fingerprint(conn, 1) == fp0
    # Edit moves it.
    conn.execute(
        "UPDATE gold_concept_facts SET value = 11.0 "
        "WHERE benchmark_id = 1 AND concept_uuid = 'c1'"
    )
    fp_edit = gold_fingerprint(conn, 1)
    assert fp_edit != fp0
    # DELETION moves it — the case the timestamp heuristic could never see.
    conn.execute(
        "DELETE FROM gold_concept_facts "
        "WHERE benchmark_id = 1 AND concept_uuid = 'c2'"
    )
    assert gold_fingerprint(conn, 1) != fp_edit
    # Two benchmarks with IDENTICAL gold content still fingerprint apart, so
    # reassigning a doc to a same-looking benchmark reads as a change.
    assert gold_fingerprint(conn, 2) != fp0


def test_save_eval_score_stamps_fingerprint_and_scorecard_flags_stale(db):
    conn, _ = db
    conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
        "benchmark_id) VALUES ('t', 'x.pdf', 'completed', 's', 1)"
    )
    run_id = conn.execute("SELECT id FROM runs").fetchone()[0]
    repo.save_eval_score(conn, run_id, 1, _Card())
    conn.commit()

    score = repo.fetch_eval_score(conn, run_id, 1)
    assert score["gold_fingerprint"] == gold_fingerprint(conn, 1)

    from eval.scorecards import build_document_scorecard

    card = build_document_scorecard(conn, run_id)
    assert card.gold_stale is False

    # Delete one gold row (no timestamp in any window) → stale.
    conn.execute(
        "DELETE FROM gold_concept_facts "
        "WHERE benchmark_id = 1 AND concept_uuid = 'c2'"
    )
    conn.commit()
    card = build_document_scorecard(conn, run_id)
    assert card.gold_stale is True
    assert card.to_dict()["gold_stale"] is True


def test_compare_flags_gold_changed_between_two_stamped_runs(db):
    conn, _ = db
    conn.execute("INSERT INTO eval_suites(name, created_at) VALUES ('S','t')")
    conn.execute(
        "INSERT INTO eval_suite_docs(suite_id, label, benchmark_id, "
        "filing_standard, filing_level) VALUES (1, 'd', 1, 'mfrs', 'company')"
    )
    for created in ("2026-01-01", "2026-02-01"):
        conn.execute(
            "INSERT INTO eval_suite_runs(suite_id, config_json, status, "
            "created_at) VALUES (1, '{}', 'complete', ?)", (created,),
        )

    def _graded_child(sr_id):
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
            "suite_run_id, benchmark_id) "
            "VALUES ('t', 'x.pdf', 'completed', ?, ?, 1)",
            (f"suite-{sr_id}-doc-1", sr_id),
        )
        repo.save_eval_score(conn, int(cur.lastrowid), 1, _Card())

    _graded_child(1)
    # Gold edited BETWEEN the two suite runs' gradings — but leave updated_at
    # untouched, which blinded the old timestamp heuristic.
    conn.execute(
        "UPDATE gold_concept_facts SET value = 99.0 "
        "WHERE benchmark_id = 1 AND concept_uuid = 'c1'"
    )
    _graded_child(2)
    conn.commit()

    from eval.compare import compare_suite_runs

    cmp = compare_suite_runs(conn, 1, 2)
    doc = next(r for r in cmp["documents"] if r["doc_id"] == 1)
    assert doc["gold_changed"] is True
    assert cmp["gold_changed_any"] is True


def test_re_grade_endpoint_updates_score_and_fingerprint(tmp_path, monkeypatch):
    db_path = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    import server as srv

    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db_path
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    _seed(conn)
    conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
        "benchmark_id) VALUES ('t', 'x.pdf', 'completed', 's', 1)"
    )
    run_id = conn.execute("SELECT id FROM runs").fetchone()[0]
    # The run matched both gold cells at grade time.
    for uuid, v in (("c1", 10.0), ("c2", 20.0)):
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, updated_at) "
            "VALUES (?, ?, 'CY', 'Company', ?, 'observed', '')",
            (run_id, uuid, v),
        )
    repo.save_eval_score(conn, run_id, 1, _Card())
    # Later, a human corrects one gold answer → the stored 100% is stale.
    conn.execute(
        "UPDATE gold_concept_facts SET value = 99.0 "
        "WHERE benchmark_id = 1 AND concept_uuid = 'c1'"
    )
    conn.commit()
    conn.close()

    tc = TestClient(srv.app)
    resp = tc.post(f"/api/runs/{run_id}/re-grade")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["old_score"] == pytest.approx(1.0)
    assert body["new_score"] == pytest.approx(0.5)

    conn = sqlite3.connect(str(db_path))
    try:
        score = repo.fetch_eval_score(conn, run_id, 1)
        assert score["score"] == pytest.approx(0.5)
        assert score["gold_fingerprint"] == gold_fingerprint(conn, 1)
    finally:
        conn.close()

    # A run with no benchmark is a clear 422, not a crash.
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, session_id) "
        "VALUES ('t', 'y.pdf', 'completed', 's2')"
    )
    bare_run = int(cur.lastrowid)
    conn.commit()
    conn.close()
    assert tc.post(f"/api/runs/{bare_run}/re-grade").status_code == 422
