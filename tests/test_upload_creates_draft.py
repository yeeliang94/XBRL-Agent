"""POST /api/upload must immediately persist a draft `runs` row.

Plan: docs/PLAN-persistent-draft-uploads.md — Phase A.

These tests pin the contract that every upload becomes a shareable run on
disk-write success: the response includes a `run_id`, a row exists in the
audit DB with `status='draft'`, and `GET /api/runs/{run_id}` rehydrates
it without choking on the null/empty pre-run config shape.
"""
from __future__ import annotations

import io
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import server
from db.schema import init_db


# Helper — same shape as the existing test_upload_api fixture pattern.
def _wire_paths(tmp_path: Path, monkeypatch) -> Path:
    """Repoint OUTPUT_DIR + AUDIT_DB_PATH at a fresh temp location.

    A fresh AUDIT_DB_PATH per test is essential — the server's audit DB is
    a process-wide handle, and we don't want test ordering to leak rows
    across cases.
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    db_path = output_dir / "xbrl_agent.db"
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db_path)
    # Initialise so the upload endpoint can write a row immediately.
    init_db(db_path)
    return output_dir


def _open_db(output_dir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(output_dir / "xbrl_agent.db"))
    conn.row_factory = sqlite3.Row
    return conn


def test_upload_returns_run_id_and_persists_draft_row(tmp_path, monkeypatch):
    """The upload response carries `run_id`, and a `runs` row exists with
    status='draft' and the user-visible filename."""
    output_dir = _wire_paths(tmp_path, monkeypatch)
    client = TestClient(server.app)

    pdf_content = b"%PDF-1.4 fake pdf"
    resp = client.post(
        "/api/upload",
        files={"file": ("Annual-Report.pdf", io.BytesIO(pdf_content), "application/pdf")},
    )

    assert resp.status_code == 200
    data = resp.json()
    # Back-compat: existing clients still consume session_id + filename.
    assert "session_id" in data
    assert data["filename"] == "Annual-Report.pdf"
    # New: the response carries a numeric run_id pointing at a draft row.
    assert "run_id" in data
    assert isinstance(data["run_id"], int)

    conn = _open_db(output_dir)
    try:
        row = conn.execute(
            "SELECT id, status, pdf_filename, session_id, "
            "run_config_json, started_at, ended_at "
            "FROM runs WHERE id = ?",
            (data["run_id"],),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "upload should have inserted a runs row"
    assert row["status"] == "draft"
    assert row["pdf_filename"] == "Annual-Report.pdf"
    assert row["session_id"] == data["session_id"]
    # Draft has no config yet — the user hasn't picked statements/level/standard.
    assert row["run_config_json"] is None
    # Draft has not started; ended_at is null until the run actually finishes.
    assert row["started_at"] == ""
    assert row["ended_at"] is None


def test_draft_run_is_fetchable(tmp_path, monkeypatch):
    """`GET /api/runs/{id}` returns a sane payload for a draft (config={},
    no agents, no cross_checks) — exercises the post-upload refresh path."""
    output_dir = _wire_paths(tmp_path, monkeypatch)
    client = TestClient(server.app)

    upload = client.post(
        "/api/upload",
        files={"file": ("Sample.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    run_id = upload.json()["run_id"]

    resp = client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200
    detail = resp.json()

    assert detail["id"] == run_id
    assert detail["status"] == "draft"
    assert detail["pdf_filename"] == "Sample.pdf"
    assert detail["session_id"] == upload.json()["session_id"]
    # Empty/draft-shaped config — the rehydration path on the frontend
    # treats this as "no choices made yet" and shows defaults.
    assert detail["config"] in ({}, None)
    assert detail["agents"] == []
    assert detail["cross_checks"] == []
    # Defaults still apply for the front-page filing-level/standard fields.
    assert detail["filing_level"] == "company"
    assert detail["filing_standard"] == "mfrs"
