"""Cycle 7: Download API — GET /api/result/{session_id}/{file}."""
import server
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)


def test_download_filled_excel(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    session_dir = output_dir / "sess1"
    session_dir.mkdir(parents=True)
    (session_dir / "filled.xlsx").write_bytes(b"fake excel content")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    resp = client.get("/api/result/sess1/filled.xlsx")
    assert resp.status_code == 200
    assert resp.content == b"fake excel content"


def test_download_result_json(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    session_dir = output_dir / "sess2"
    session_dir.mkdir(parents=True)
    (session_dir / "result.json").write_text('{"fields": []}')
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    resp = client.get("/api/result/sess2/result.json")
    assert resp.status_code == 200
    assert resp.json() == {"fields": []}


def test_download_missing_file_returns_404(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    resp = client.get("/api/result/nope/filled.xlsx")
    assert resp.status_code == 404
