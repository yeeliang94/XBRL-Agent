"""Phase 3 / Step 5: the /api/doc-convert routes.

Drives the route layer with the worker stubbed (the real Docling path is
covered by tests/test_docconvert.py). conftest defaults AUTH_MODE=dev so these
/api/* routes don't 401 (gotcha #24).
"""
from __future__ import annotations

import importlib
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db.schema import init_db
from db import repository as repo


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    db = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db
    init_db(db)
    return TestClient(srv.app), db


def _fake_worker_done(db_path, job_id, pdf_path, result_html_path, *, models_dir=None):
    """Stand-in worker: write a tiny HTML and mark the job done immediately."""
    out = Path(result_html_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "<html><body><table><tr><td>3,141,738</td></tr></table></body></html>",
        encoding="utf-8",
    )
    conn = sqlite3.connect(str(db_path))
    try:
        repo.update_doc_conversion_progress(conn, job_id, current_page=1, total_pages=1)
        repo.mark_doc_conversion_finished(
            conn, job_id, status="done", result_html_path=str(out)
        )
        conn.commit()
    finally:
        conn.close()


def _poll_until_done(c, job_id, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = c.get(f"/api/doc-convert/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.03)
    raise AssertionError("conversion did not finish in time")


def test_upload_convert_poll_and_view(client, monkeypatch):
    c, db = client
    import docconvert.routes as routes
    monkeypatch.setattr(routes, "run_conversion_job", _fake_worker_done)

    resp = c.post(
        "/api/doc-convert",
        files={"file": ("statement.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    job_id = body["job_id"]

    done = _poll_until_done(c, job_id)
    assert done["status"] == "done"

    view = c.get(f"/api/doc-convert/{job_id}/view")
    assert view.status_code == 200
    assert "3,141,738" in view.text


def test_rejects_non_pdf(client):
    c, _ = client
    resp = c.post(
        "/api/doc-convert",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


def test_serialises_to_one_conversion(client):
    c, db = client
    # Seed an in-flight job directly, then a new upload must be refused.
    with repo.db_session(db) as conn:
        jid = repo.create_doc_conversion(
            conn, source_pdf_path="/tmp/a.pdf", original_filename="a.pdf"
        )
        repo.update_doc_conversion_progress(conn, jid, current_page=0, total_pages=0)
    resp = c.post(
        "/api/doc-convert",
        files={"file": ("b.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 409


def test_409_upload_leaves_no_orphan_file(client, tmp_path):
    """A rejected (409) upload must NOT leave a source.pdf on disk.

    Regression for the peer-review finding: the atomic serialise check now runs
    BEFORE the bytes are written.
    """
    c, db = client
    out_root = tmp_path / "doc_conversions"
    with repo.db_session(db) as conn:
        repo.create_doc_conversion(
            conn, source_pdf_path="/tmp/a.pdf", original_filename="a.pdf"
        )  # active job → next upload is refused
    before = list(out_root.rglob("source.pdf")) if out_root.exists() else []
    resp = c.post(
        "/api/doc-convert",
        files={"file": ("b.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 409
    after = list(out_root.rglob("source.pdf")) if out_root.exists() else []
    assert before == after  # no new file written for the rejected upload


def test_view_sets_restrictive_csp(client):
    c, db = client
    import pathlib
    import server as srv

    # The result file must live under the output dir (path-confinement check).
    out = pathlib.Path(srv.OUTPUT_DIR) / "doc_conversions" / "vjob" / "result.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("<html><body><table></table></body></html>", encoding="utf-8")
    with repo.db_session(db) as conn:
        jid = repo.create_doc_conversion(
            conn, source_pdf_path="/tmp/a.pdf", original_filename="a.pdf"
        )
        repo.mark_doc_conversion_finished(
            conn, jid, status="done", result_html_path=str(out)
        )
    resp = c.get(f"/api/doc-convert/{jid}/view")
    assert resp.status_code == 200
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_unknown_job_is_404(client):
    c, _ = client
    assert c.get("/api/doc-convert/9999").status_code == 404
    assert c.get("/api/doc-convert/9999/view").status_code == 404


def test_view_409_before_done(client):
    c, db = client
    with repo.db_session(db) as conn:
        jid = repo.create_doc_conversion(
            conn, source_pdf_path="/tmp/a.pdf", original_filename="a.pdf"
        )
    assert c.get(f"/api/doc-convert/{jid}/view").status_code == 409


def test_sse_emits_complete_for_terminal_job(client):
    c, db = client
    with repo.db_session(db) as conn:
        jid = repo.create_doc_conversion(
            conn, source_pdf_path="/tmp/a.pdf", original_filename="a.pdf"
        )
        repo.mark_doc_conversion_finished(
            conn, jid, status="done", result_html_path="/tmp/a.html"
        )
    with c.stream("GET", f"/api/doc-convert/{jid}/events") as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "doc_convert_complete" in text
    assert '"status": "done"' in text
