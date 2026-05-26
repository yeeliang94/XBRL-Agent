"""M1 Step 1-2 — source-PDF serving for side-by-side review.

Pins the two read-only endpoints the Review Workspace adds:

  GET /api/runs/{id}/pdf/info          → {"pages": N}
  GET /api/runs/{id}/pdf/page/{n}.png  → rendered PNG of page n

The PDF is resolved from the run's session dir (OUTPUT_DIR/{session_id}/
uploaded.pdf). Out-of-range pages, unknown runs, and runs with no stored PDF
all 404 so the frontend can degrade to an empty pane instead of erroring.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import fitz  # PyMuPDF — used to synthesise a real PDF the renderer can open
import pytest
from fastapi.testclient import TestClient


def _make_pdf(path: Path, pages: int = 3) -> None:
    """Write a minimal multi-page PDF so the render path exercises real bytes."""
    doc = fitz.open()
    try:
        for i in range(pages):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {i + 1}")
        doc.save(str(path))
    finally:
        doc.close()


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    # NB: do NOT importlib.reload(server). Reloading rebinds the module's
    # stateful globals (notably `active_runs`), so tests that imported server
    # earlier end up mutating a stale object while the live endpoints read the
    # new one — that silently broke test_sse_rejects_concurrent_run (returned
    # 200 instead of 409). monkeypatch.setattr on the existing module is enough
    # here and auto-restores after the test.
    import server as srv
    db_path = tmp_path / "xbrl.db"
    monkeypatch.setattr(srv, "AUDIT_DB_PATH", db_path)
    # OUTPUT_DIR is a module global (not env-driven); point it at tmp_path so
    # session dirs live under it exactly as they do in production. The PDF
    # path-resolution hardening constrains candidates to OUTPUT_DIR.
    monkeypatch.setattr(srv, "OUTPUT_DIR", tmp_path)
    from db.schema import init_db
    init_db(db_path)

    # A run whose session dir holds a 3-page uploaded.pdf.
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _make_pdf(session_dir / "uploaded.pdf", pages=3)
    merged = session_dir / "filled.xlsx"
    merged.write_bytes(b"not-a-real-xlsx")

    conn = sqlite3.connect(str(db_path))
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
            "ended_at, session_id, merged_workbook_path) "
            "VALUES (?,?,?,?,?,?,?)",
            ("2026-05-26T00:00:00Z", "x.pdf", "completed",
             "2026-05-26T00:00:00Z", "2026-05-26T01:00:00Z", "session",
             str(merged)),
        ).lastrowid
        # A second run with NO uploaded.pdf in its session dir.
        no_pdf_dir = tmp_path / "no_pdf"
        no_pdf_dir.mkdir()
        no_pdf_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
            "session_id, merged_workbook_path) VALUES (?,?,?,?,?,?)",
            ("2026-05-26T00:00:00Z", "y.pdf", "completed",
             "2026-05-26T00:00:00Z", "no_pdf",
             str(no_pdf_dir / "filled.xlsx")),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    tc = TestClient(srv.app)
    tc.run_id = run_id  # type: ignore[attr-defined]
    tc.no_pdf_id = no_pdf_id  # type: ignore[attr-defined]
    return tc


def test_info_returns_page_count(client: TestClient):
    r = client.get(f"/api/runs/{client.run_id}/pdf/info")
    assert r.status_code == 200, r.text
    assert r.json() == {"run_id": client.run_id, "pages": 3}


def test_page_returns_png(client: TestClient):
    r = client.get(f"/api/runs/{client.run_id}/pdf/page/2.png")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-pdf-page-count"] == "3"
    # PNG magic number.
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_dpi_is_clamped(client: TestClient):
    # An absurd DPI must not error — it clamps and still returns a PNG.
    r = client.get(f"/api/runs/{client.run_id}/pdf/page/1.png?dpi=99999")
    assert r.status_code == 200, r.text
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("page", [0, 4, 999])
def test_out_of_range_page_404(client: TestClient, page: int):
    assert client.get(f"/api/runs/{client.run_id}/pdf/page/{page}.png").status_code == 404


def test_unknown_run_404(client: TestClient):
    assert client.get("/api/runs/9999/pdf/info").status_code == 404
    assert client.get("/api/runs/9999/pdf/page/1.png").status_code == 404


def test_run_without_pdf_404(client: TestClient):
    assert client.get(f"/api/runs/{client.no_pdf_id}/pdf/info").status_code == 404
    assert client.get(f"/api/runs/{client.no_pdf_id}/pdf/page/1.png").status_code == 404


def test_traversing_session_id_cannot_escape_output_dir(client: TestClient, tmp_path: Path):
    """Defense-in-depth: a `..`-laden session_id must not resolve to a PDF
    outside OUTPUT_DIR, even when such a file exists. OUTPUT_DIR is tmp_path
    (set by the fixture), so we plant an uploaded.pdf in a sibling dir and
    point a run's session_id at it via traversal — the endpoint must 404."""
    import sqlite3 as _sq
    import server as srv

    escape_dir = tmp_path.parent / f"escape_{tmp_path.name}"
    escape_dir.mkdir(exist_ok=True)
    try:
        _make_pdf(escape_dir / "uploaded.pdf", pages=1)
        conn = _sq.connect(str(srv.AUDIT_DB_PATH))
        try:
            rid = conn.execute(
                "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
                "session_id) VALUES (?,?,?,?,?)",
                ("2026-05-26T00:00:00Z", "evil.pdf", "completed",
                 "2026-05-26T00:00:00Z", f"../{escape_dir.name}"),
            ).lastrowid
            conn.commit()
        finally:
            conn.close()
        # The file exists, but it's outside OUTPUT_DIR → blocked.
        assert client.get(f"/api/runs/{rid}/pdf/info").status_code == 404
        assert client.get(f"/api/runs/{rid}/pdf/page/1.png").status_code == 404
    finally:
        import shutil
        shutil.rmtree(escape_dir, ignore_errors=True)
