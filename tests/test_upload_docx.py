"""POST /api/upload accepts Word (.docx) — PLAN-word-input Phase 1 Step 3.

The converter is mocked (no LibreOffice/Word in CI); the .docx bytes are a real
minimal docx so the mammoth source.html sidecar path runs for real.
"""
import io

import pytest
import server
from fastapi.testclient import TestClient
from server import app

from ingest import word_convert
from ingest.word_convert import WordConversionError
from tests._docx_fixture import build_minimal_docx

client = TestClient(app)

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx_bytes(tmp_path) -> bytes:
    return build_minimal_docx(tmp_path / "fs.docx").read_bytes()


def test_upload_docx_converts_and_keeps_both_files(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    # Mock the actual conversion — write a fake PDF where the real converter would.
    def _fake_run(src, dest):
        dest.write_bytes(b"%PDF-1.7 converted")

    monkeypatch.setattr(word_convert, "_run_conversion", _fake_run)

    resp = client.post(
        "/api/upload",
        files={"file": ("Accounts.docx", io.BytesIO(_docx_bytes(tmp_path)), _DOCX_MIME)},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["filename"] == "Accounts.docx"  # History shows the Word name

    session_dir = output_dir / data["session_id"]
    assert (session_dir / "uploaded.docx").exists()  # original kept
    assert (session_dir / "uploaded.pdf").exists()  # canonical for the pipeline
    # mammoth ran on the real docx → source.html sidecar for the notes channel.
    assert (session_dir / "source.html").exists()
    assert "1,595" in (session_dir / "source.html").read_text(encoding="utf-8")


def test_upload_docx_conversion_failure_returns_422(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    def _boom(src, dest):
        raise WordConversionError("no converter", user_message="Save As PDF and retry.")

    monkeypatch.setattr(word_convert, "_run_conversion", _boom)

    resp = client.post(
        "/api/upload",
        files={"file": ("bad.docx", io.BytesIO(_docx_bytes(tmp_path)), _DOCX_MIME)},
    )
    assert resp.status_code == 422
    assert "Save As PDF" in resp.json()["detail"]
    # The whole session dir is torn down — no half-usable run left behind.
    assert not any(output_dir.iterdir())


def test_upload_docx_unexpected_error_also_422_and_torn_down(tmp_path, monkeypatch):
    # Gotcha #29: a conversion failure is a 422, NEVER a crash. An UNEXPECTED
    # exception (not WordConversionError) must still become a 422 and tear down
    # the session dir — not escape as a 500 that orphans uploaded.docx.
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    def _kaboom(src, dest):
        raise RuntimeError("converter bug, not a WordConversionError")

    monkeypatch.setattr(word_convert, "_run_conversion", _kaboom)

    resp = client.post(
        "/api/upload",
        files={"file": ("x.docx", io.BytesIO(_docx_bytes(tmp_path)), _DOCX_MIME)},
    )
    assert resp.status_code == 422
    assert "PDF" in resp.json()["detail"]
    assert not any(output_dir.iterdir())  # no orphaned session dir


def test_upload_still_rejects_non_pdf_non_docx(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    resp = client.post(
        "/api/upload",
        files={"file": ("book.xlsx", io.BytesIO(b"PK\x03\x04"), "application/octet-stream")},
    )
    assert resp.status_code == 400


def test_upload_pdf_unaffected(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    resp = client.post(
        "/api/upload",
        files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert resp.status_code == 200
    session_dir = output_dir / resp.json()["session_id"]
    assert (session_dir / "uploaded.pdf").exists()
    assert not (session_dir / "source.html").exists()  # no sidecar for PDF uploads
