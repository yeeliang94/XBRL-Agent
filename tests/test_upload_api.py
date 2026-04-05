"""Cycle 5: Upload API — POST /api/upload."""
import io

import server
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)


def test_upload_pdf_creates_session(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    pdf_content = b"%PDF-1.4 fake pdf content"
    resp = client.post(
        "/api/upload",
        files={"file": ("test.pdf", io.BytesIO(pdf_content), "application/pdf")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data["filename"] == "test.pdf"

    # Verify file was saved on disk
    session_dir = output_dir / data["session_id"]
    assert (session_dir / "uploaded.pdf").exists()


def test_upload_rejects_non_pdf(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    resp = client.post(
        "/api/upload",
        files={"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert resp.status_code == 400


def test_upload_rejects_oversized_file(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "MAX_UPLOAD_SIZE", 1024)  # 1KB for test

    large_content = b"x" * (2 * 1024)
    resp = client.post(
        "/api/upload",
        files={"file": ("big.pdf", io.BytesIO(large_content), "application/pdf")},
    )
    assert resp.status_code == 413
