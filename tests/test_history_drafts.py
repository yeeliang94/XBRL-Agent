"""History list (GET /api/runs) surfaces drafts alongside terminated runs.

Plan: docs/PLAN-persistent-draft-uploads.md — Phase B (steps 9-10).

Drafts are runs the user uploaded but never started. They live in History
forever (per the brainstorm) so the user can come back to a partially-
configured upload, or share the URL. The list query must accept
status='draft' as a filter value, and the unfiltered list must include
drafts in the same response shape as completed/failed runs.
"""
from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from db import repository as repo
from db.schema import init_db


@pytest.fixture
def history_with_mixed_runs(tmp_path, monkeypatch):
    """Seed three runs: one completed, one failed, one draft (via upload).

    Returns (client, draft_run_id).
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    db_path = output_dir / "xbrl_agent.db"
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db_path)
    init_db(db_path)

    # Two terminal-status runs created directly via the repo. Bypassing the
    # upload endpoint here keeps the test focused on the list/filter
    # contract rather than re-exercising Phase A.
    conn = sqlite3.connect(str(db_path))
    try:
        repo.create_run(
            conn,
            pdf_filename="completed.pdf",
            session_id="sess-completed",
            output_dir=str(output_dir / "sess-completed"),
            config={"statements": ["SOFP"]},
        )
        repo.create_run(
            conn,
            pdf_filename="failed.pdf",
            session_id="sess-failed",
            output_dir=str(output_dir / "sess-failed"),
            config={"statements": ["SOPL"]},
        )
        # Mark them terminal so the list distinguishes them from drafts.
        conn.execute("UPDATE runs SET status='completed' WHERE pdf_filename='completed.pdf'")
        conn.execute("UPDATE runs SET status='failed' WHERE pdf_filename='failed.pdf'")
        conn.commit()
    finally:
        conn.close()

    client = TestClient(server.app)
    upload = client.post(
        "/api/upload",
        files={"file": ("draft.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    return client, upload.json()["run_id"]


def test_list_includes_drafts(history_with_mixed_runs):
    """An unfiltered GET /api/runs returns drafts alongside terminal runs."""
    client, draft_run_id = history_with_mixed_runs
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    statuses = sorted(r["status"] for r in body["runs"])
    assert statuses == ["completed", "draft", "failed"]


def test_status_filter_draft_only(history_with_mixed_runs):
    """`?status=draft` returns only the draft row — verifies the SQL
    filter accepts the new value with no hidden allow-list."""
    client, draft_run_id = history_with_mixed_runs
    resp = client.get("/api/runs?status=draft")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["runs"]) == 1
    only = body["runs"][0]
    assert only["status"] == "draft"
    assert only["id"] == draft_run_id
    assert only["pdf_filename"] == "draft.pdf"
