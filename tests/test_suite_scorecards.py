"""Suite scorecard aggregation (Evals workspace, Step E4).

The pure aggregation math over hand-built document scorecards: mean-of-documents
headline, pooled secondary, worst-document surfaced, failed document excluded +
labelled, taxonomy totals.
"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_db
from eval.scorecards import (
    DocumentScorecard, aggregate_suite, _coverage_rate, build_document_scorecard,
)


def _doc(run_id, *, accuracy=None, gold=0, matched=0, status="completed",
         taxonomy=None, consistency=None, ccpr=None, coverage=None, cov_avail=False):
    return DocumentScorecard(
        run_id=run_id, label=f"doc{run_id}", status=status,
        accuracy=accuracy, gold_cells=gold, matched_cells=matched,
        taxonomy=taxonomy or {}, consistency=consistency,
        cross_check_pass_rate=ccpr, notes_coverage=coverage,
        notes_coverage_available=cov_avail,
    )


def test_mean_is_per_document_not_pooled():
    # Two docs: one small perfect, one large mediocre. Mean weights them
    # equally (0.75); pooled weights by size (worse).
    docs = [
        _doc(1, accuracy=1.0, gold=2, matched=2),
        _doc(2, accuracy=0.5, gold=100, matched=50),
    ]
    agg = aggregate_suite(docs)
    assert abs(agg["mean_accuracy"] - 0.75) < 1e-9
    assert abs(agg["pooled_accuracy"] - (52 / 102)) < 1e-9
    assert agg["pooled_matched"] == 52
    assert agg["pooled_gold"] == 102


def test_worst_document_surfaced():
    docs = [
        _doc(1, accuracy=0.9, gold=10, matched=9),
        _doc(2, accuracy=0.4, gold=10, matched=4),
        _doc(3, accuracy=0.7, gold=10, matched=7),
    ]
    agg = aggregate_suite(docs)
    assert agg["worst_document"]["run_id"] == 2


def test_failed_document_excluded_and_labelled():
    docs = [
        _doc(1, accuracy=0.8, gold=10, matched=8),
        _doc(2, status="failed"),  # no gold, failed
        _doc(3, accuracy=0.6, gold=10, matched=6),
    ]
    agg = aggregate_suite(docs)
    # Failed doc is out of the mean (only docs 1 & 3 count).
    assert abs(agg["mean_accuracy"] - 0.7) < 1e-9
    assert agg["documents_total"] == 3
    assert agg["documents_graded"] == 2
    assert agg["documents_failed"] == 1
    assert agg["coverage_note"] == "2 of 3"


def test_coverage_note_over_frozen_corpus_not_scorecard_count():
    """Peer-review Step 3: a document that failed to stage produces no scorecard,
    so "N of M" must count the FROZEN corpus (corpus_size), never collapse to
    "0 of 0" when every document failed."""
    # Two of three corpus documents never produced a scorecard.
    agg = aggregate_suite([_doc(1, accuracy=0.9, gold=10, matched=9)], corpus_size=3)
    assert agg["coverage_note"] == "1 of 3"
    # All three failed to stage → zero scorecards, but M stays the corpus size.
    empty = aggregate_suite([], corpus_size=3)
    assert empty["coverage_note"] == "0 of 3"


def test_pooled_accuracy_uses_exact_repeat_matched_not_rounded():
    """Peer-review Step 7: a repeat mean of 0.5 matched must not be rounded to 0
    before pooling. Two single-cell docs, each a 0%/100% repeat mean (0.5
    matched, gold 1): mean accuracy 50% AND pooled accuracy 50% — not 0%."""
    d1 = _doc(1, accuracy=0.5, gold=1, matched=0)
    d1.matched_cells_exact = 0.5
    d2 = _doc(2, accuracy=0.5, gold=1, matched=0)
    d2.matched_cells_exact = 0.5
    agg = aggregate_suite([d1, d2])
    assert agg["mean_accuracy"] == pytest.approx(0.5)
    assert agg["pooled_accuracy"] == pytest.approx(0.5)  # 1.0 / 2, not 0/2


def test_taxonomy_totals_sum_across_documents():
    docs = [
        _doc(1, accuracy=0.8, gold=10, matched=8, taxonomy={"sign_flip": 1, "scale": 2}),
        _doc(2, accuracy=0.6, gold=10, matched=6, taxonomy={"sign_flip": 3}),
    ]
    agg = aggregate_suite(docs)
    assert agg["taxonomy_totals"] == {"sign_flip": 4, "scale": 2}


def test_no_graded_documents_gives_null_headline():
    docs = [_doc(1, status="failed"), _doc(2, accuracy=None)]
    agg = aggregate_suite(docs)
    assert agg["mean_accuracy"] is None
    assert agg["pooled_accuracy"] is None
    assert agg["worst_document"] is None


def test_means_over_consistency_coverage_ccpr():
    docs = [
        _doc(1, accuracy=1.0, gold=1, matched=1, consistency=0.8, ccpr=1.0, coverage=0.9, cov_avail=True),
        _doc(2, accuracy=1.0, gold=1, matched=1, consistency=0.6, ccpr=0.5, coverage=0.7, cov_avail=True),
    ]
    agg = aggregate_suite(docs)
    assert abs(agg["mean_consistency"] - 0.7) < 1e-9
    assert abs(agg["mean_cross_check_pass_rate"] - 0.75) < 1e-9
    assert abs(agg["mean_notes_coverage"] - 0.8) < 1e-9


def test_coverage_rate_excludes_skips_and_honours_unavailable():
    # placed=2, missing=1, skipped=1 (excluded) → 2/3.
    rows = [
        {"note_num": 1, "subnote_ref": None, "status": "placed"},
        {"note_num": 2, "subnote_ref": None, "status": "placed"},
        {"note_num": 3, "subnote_ref": None, "status": "missing"},
        {"note_num": 4, "subnote_ref": None, "status": "skipped"},
    ]
    rate, avail = _coverage_rate(rows)
    assert avail is True
    assert abs(rate - 2 / 3) < 1e-9

    # Inventory-unavailable banner → not green, unavailable.
    banner = [{"note_num": -1, "subnote_ref": None, "status": "inventory_unavailable"}]
    rate2, avail2 = _coverage_rate(banner)
    assert rate2 is None and avail2 is False


def test_document_scorecard_counts_only_active_reviewer_flags(tmp_path):
    """Code-review MEDIUM: resolved/dismissed reviewer flags are closed issues
    and must not keep dragging the health signal."""
    db = tmp_path / "sc.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'x.pdf', 'completed')"
        )
        for status in ("open", "answered", "resolved", "dismissed"):
            conn.execute(
                "INSERT INTO reviewer_flags(run_id, category, status) "
                "VALUES (1, 'stuck', ?)",
                (status,),
            )
        conn.commit()
        card = build_document_scorecard(conn, 1)
        # open + answered count; resolved + dismissed excluded.
        assert card.reviewer_flags == 2
    finally:
        conn.close()


def test_non_terminal_runs_do_not_feed_health_means():
    """PLAN-evals-hardening Step 6: aborted / draft / running rows are
    labelled but must not move the suite's health means or graded counts."""
    from eval.scorecards import DocumentScorecard, aggregate_suite

    good = DocumentScorecard(
        run_id=1, status="completed", accuracy=0.9, gold_cells=10,
        matched_cells=9, cross_check_pass_rate=1.0, notes_coverage=1.0,
        notes_coverage_available=True, consistency=1.0,
    )
    aborted = DocumentScorecard(
        run_id=2, status="aborted", accuracy=0.1, gold_cells=10,
        matched_cells=1, cross_check_pass_rate=0.0, notes_coverage=0.0,
        notes_coverage_available=True, consistency=0.0,
    )
    running = DocumentScorecard(run_id=3, status="running",
                                cross_check_pass_rate=0.0)
    draft = DocumentScorecard(run_id=4, status="draft")

    agg = aggregate_suite([good, aborted, running, draft])
    assert agg["documents_graded"] == 1
    assert agg["mean_accuracy"] == 0.9
    assert agg["mean_cross_check_pass_rate"] == 1.0
    assert agg["mean_consistency"] == 1.0
    assert agg["mean_notes_coverage"] == 1.0
    # Labelled, not hidden: they still count toward the corpus total.
    assert agg["documents_total"] == 4
    assert aborted.contributes is False and good.contributes is True
    assert aborted.to_dict()["contributes"] is False
