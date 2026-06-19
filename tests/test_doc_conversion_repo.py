"""Phase 2 / Step 4: doc_conversions repository CRUD + stale reconcile.

Round-trips a conversion job through every state and proves the startup
reconcile turns an orphaned 'running'/'queued' row into a terminal 'failed'.
"""
from __future__ import annotations

from db.schema import init_db
from db import repository as repo


def test_conversion_round_trips_through_every_state(tmp_path):
    db = tmp_path / "conv.db"
    init_db(db)
    with repo.db_session(db) as conn:
        job_id = repo.create_doc_conversion(
            conn, source_pdf_path="/tmp/a.pdf", original_filename="a.pdf"
        )
        job = repo.fetch_doc_conversion(conn, job_id)
        assert job is not None
        assert job.status == "queued"
        assert job.original_filename == "a.pdf"
        assert job.result_html_path is None

        # While running, a conversion blocks a second one (serialise to one).
        assert repo.is_doc_conversion_running(conn) is True

        repo.update_doc_conversion_progress(
            conn, job_id, current_page=2, total_pages=5
        )
        job = repo.fetch_doc_conversion(conn, job_id)
        assert job.status == "running"
        assert (job.current_page, job.total_pages) == (2, 5)

        repo.mark_doc_conversion_finished(
            conn, job_id, status="done", result_html_path="/tmp/a.html"
        )
        job = repo.fetch_doc_conversion(conn, job_id)
        assert job.status == "done"
        assert job.result_html_path == "/tmp/a.html"
        assert job.error is None
        # A done conversion no longer blocks a new one.
        assert repo.is_doc_conversion_running(conn) is False


def test_mark_failed_records_error(tmp_path):
    db = tmp_path / "conv.db"
    init_db(db)
    with repo.db_session(db) as conn:
        job_id = repo.create_doc_conversion(
            conn, source_pdf_path="/tmp/bad.pdf", original_filename="bad.pdf"
        )
        repo.mark_doc_conversion_finished(
            conn, job_id, status="failed", error="This PDF is password protected."
        )
        job = repo.fetch_doc_conversion(conn, job_id)
        assert job.status == "failed"
        assert "password protected" in job.error


def test_create_if_idle_is_atomic_and_serialised(tmp_path):
    """create_doc_conversion_if_idle creates only when no job is active."""
    db = tmp_path / "conv.db"
    init_db(db)

    # First call creates a job.
    jid = repo.create_doc_conversion_if_idle(
        db, source_pdf_path="/tmp/a.pdf", original_filename="a.pdf"
    )
    assert jid is not None

    # While it's active (queued), a second call is refused (returns None) —
    # no duplicate job.
    assert (
        repo.create_doc_conversion_if_idle(
            db, source_pdf_path="/tmp/b.pdf", original_filename="b.pdf"
        )
        is None
    )

    # Finish it; now a new job can be created again.
    with repo.db_session(db) as conn:
        repo.mark_doc_conversion_finished(
            conn, jid, status="done", result_html_path="/tmp/a.html"
        )
    jid2 = repo.create_doc_conversion_if_idle(
        db, source_pdf_path="/tmp/c.pdf", original_filename="c.pdf"
    )
    assert jid2 is not None and jid2 != jid


def test_reconcile_stale_conversions_fails_orphaned_jobs(tmp_path):
    db = tmp_path / "conv.db"
    init_db(db)
    with repo.db_session(db) as conn:
        running = repo.create_doc_conversion(
            conn, source_pdf_path="/tmp/r.pdf", original_filename="r.pdf"
        )
        repo.update_doc_conversion_progress(
            conn, running, current_page=1, total_pages=3
        )
        done = repo.create_doc_conversion(
            conn, source_pdf_path="/tmp/d.pdf", original_filename="d.pdf"
        )
        repo.mark_doc_conversion_finished(
            conn, done, status="done", result_html_path="/tmp/d.html"
        )

        # Two orphaned rows: the 'running' one above + a still-'queued' one.
        repo.create_doc_conversion(
            conn, source_pdf_path="/tmp/q.pdf", original_filename="q.pdf"
        )

        fixed = repo.reconcile_stale_doc_conversions(conn)
        assert fixed == 2  # the running + the queued; the done is untouched

        job = repo.fetch_doc_conversion(conn, running)
        assert job.status == "failed"
        assert "restarted" in job.error.lower()
        # The already-finished job is left alone.
        assert repo.fetch_doc_conversion(conn, done).status == "done"
