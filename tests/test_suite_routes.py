"""Suite CRUD + document routes (Evals workspace, Step E2)."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    import server as srv

    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db
    srv.OUTPUT_DIR = tmp_path
    from db.schema import init_db

    init_db(db)
    return TestClient(srv.app), tmp_path


def test_create_list_and_get_suite(client):
    tc, _ = client
    r = tc.post("/api/suites", json={"name": "MFRS Company regression"})
    assert r.status_code == 200, r.text
    suite = r.json()
    sid = suite["id"]
    assert suite["name"] == "MFRS Company regression"
    assert suite["docs"] == []

    lst = tc.get("/api/suites").json()["suites"]
    assert any(s["id"] == sid and s["doc_count"] == 0 for s in lst)

    got = tc.get(f"/api/suites/{sid}")
    assert got.status_code == 200
    assert got.json()["name"] == "MFRS Company regression"


def test_create_requires_name(client):
    tc, _ = client
    assert tc.post("/api/suites", json={"name": "  "}).status_code == 422


def test_rename_and_delete_suite(client):
    tc, _ = client
    sid = tc.post("/api/suites", json={"name": "old"}).json()["id"]
    renamed = tc.patch(f"/api/suites/{sid}", json={"name": "new"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "new"

    assert tc.delete(f"/api/suites/{sid}").status_code == 200
    assert tc.get(f"/api/suites/{sid}").status_code == 404


def test_add_and_remove_document(client):
    tc, tmp = client
    sid = tc.post("/api/suites", json={"name": "s"}).json()["id"]

    # Add a PDF document to the suite.
    resp = tc.post(
        f"/api/suites/{sid}/docs",
        data={"label": "FINCO 2021", "filing_standard": "mfrs", "filing_level": "company"},
        files={"file": ("FINCO.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    doc_id = body["doc_id"]
    assert len(body["suite"]["docs"]) == 1
    assert body["suite"]["docs"][0]["source_filename"] == "FINCO.pdf"

    # The managed copy exists on disk.
    managed = tmp / "_suite_docs" / str(sid)
    assert any(managed.iterdir())

    # Reject a non-PDF/.docx file.
    bad = tc.post(
        f"/api/suites/{sid}/docs",
        data={"filing_standard": "mfrs", "filing_level": "company"},
        files={"file": ("notes.txt", b"hi", "text/plain")},
    )
    assert bad.status_code == 400

    # Remove the document.
    assert tc.delete(f"/api/suites/{sid}/docs/{doc_id}").status_code == 200
    assert tc.get(f"/api/suites/{sid}").json()["docs"] == []


def test_add_doc_to_missing_suite_404(client):
    tc, _ = client
    resp = tc.post(
        "/api/suites/999/docs",
        data={"filing_standard": "mfrs", "filing_level": "company"},
        files={"file": ("x.pdf", b"%PDF", "application/pdf")},
    )
    assert resp.status_code == 404


def test_add_document_with_denomination(client):
    """Step 3 (PLAN-evals-hardening): denomination is per DOCUMENT — a mixed
    corpus extracts each filing at its declared scale, never a silent
    suite-wide 'thousands'."""
    tc, _ = client
    sid = tc.post("/api/suites", json={"name": "s"}).json()["id"]

    resp = tc.post(
        f"/api/suites/{sid}/docs",
        data={"label": "mil", "denomination": "millions"},
        files={"file": ("a.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["suite"]["docs"][0]["denomination"] == "millions"
    # Omitted → the common Malaysian default.
    resp2 = tc.post(
        f"/api/suites/{sid}/docs",
        files={"file": ("b.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp2.json()["suite"]["docs"][1]["denomination"] == "thousands"
    # Junk is rejected before anything is stored.
    bad = tc.post(
        f"/api/suites/{sid}/docs",
        data={"denomination": "lakhs"},
        files={"file": ("c.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert bad.status_code == 400
