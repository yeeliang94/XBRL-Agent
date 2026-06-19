"""Phase 3 / Step 6: the background conversion worker.

The Docling engine itself is proven in tests/test_docconvert.py. Here we stub
the converter so we can drive the worker fast and deterministically and assert
the orchestration contract: progress is recorded, every exit lands a terminal
status (never stuck 'running'), and a restart reconciles an orphaned job.
"""
from __future__ import annotations

from pathlib import Path

from db.schema import init_db
from db import repository as repo
from docconvert.converter import DocConvertError
from docconvert import worker


def _make_job(db) -> int:
    with repo.db_session(db) as conn:
        return repo.create_doc_conversion(
            conn, source_pdf_path="/tmp/x.pdf", original_filename="x.pdf"
        )


def test_happy_path_records_progress_and_writes_html(tmp_path, monkeypatch):
    db = tmp_path / "c.db"
    init_db(db)
    job_id = _make_job(db)
    out_html = tmp_path / "job" / "result.html"

    # Fake converter: drive 3 pages of progress, return real HTML.
    def _fake(pdf_path, *, model_dir=None, progress_cb=None):
        for i in range(1, 4):
            progress_cb(i, 3)
        return "<html><body><table><tr><td>3,141,738</td></tr></table></body></html>"

    monkeypatch.setattr(worker, "convert_pdf_to_html", _fake)

    worker.run_conversion_job(db, job_id, "/tmp/x.pdf", out_html)

    with repo.db_session(db) as conn:
        job = repo.fetch_doc_conversion(conn, job_id)
    assert job.status == "done"
    assert (job.current_page, job.total_pages) == (3, 3)
    assert job.result_html_path == str(out_html)
    assert out_html.exists()
    assert "3,141,738" in out_html.read_text(encoding="utf-8")


def test_user_error_lands_failed_with_message(tmp_path, monkeypatch):
    db = tmp_path / "c.db"
    init_db(db)
    job_id = _make_job(db)

    def _boom(pdf_path, *, model_dir=None, progress_cb=None):
        raise DocConvertError("This PDF is password protected.")

    monkeypatch.setattr(worker, "convert_pdf_to_html", _boom)
    worker.run_conversion_job(db, job_id, "/tmp/x.pdf", tmp_path / "r.html")

    with repo.db_session(db) as conn:
        job = repo.fetch_doc_conversion(conn, job_id)
    assert job.status == "failed"
    assert "password protected" in job.error


def test_unexpected_crash_lands_failed_not_running(tmp_path, monkeypatch):
    db = tmp_path / "c.db"
    init_db(db)
    job_id = _make_job(db)

    def _crash(pdf_path, *, model_dir=None, progress_cb=None):
        raise RuntimeError("segfault-ish")

    monkeypatch.setattr(worker, "convert_pdf_to_html", _crash)
    worker.run_conversion_job(db, job_id, "/tmp/x.pdf", tmp_path / "r.html")

    with repo.db_session(db) as conn:
        job = repo.fetch_doc_conversion(conn, job_id)
    # The raw exception text is NOT leaked to the user; status is terminal.
    assert job.status == "failed"
    assert "unexpectedly" in job.error
    assert "segfault" not in job.error


def test_simulated_restart_reconciles_orphaned_job(tmp_path):
    db = tmp_path / "c.db"
    init_db(db)
    job_id = _make_job(db)
    with repo.db_session(db) as conn:
        repo.update_doc_conversion_progress(conn, job_id, current_page=1, total_pages=4)
        # Job is now 'running'. Simulate the worker thread dying with the
        # process, then startup reconcile.
        fixed = repo.reconcile_stale_doc_conversions(conn)
        assert fixed == 1
        job = repo.fetch_doc_conversion(conn, job_id)
    assert job.status == "failed"
    assert "restarted" in job.error.lower()
