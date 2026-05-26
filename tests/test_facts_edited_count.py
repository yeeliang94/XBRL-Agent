"""Phase 2.3 — GET /api/runs/{id}/facts/edited_count.

The face-statement analogue of notes_cells/edited_count: it counts the
user_override facts touched after the run finished, so a re-run / correction
confirm dialog can warn "N edited values will be overwritten". The signal is
value_status='user_override' AND updated_at > run.ended_at — neither the
extraction writer nor the cascade ever stamps user_override.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    db_path = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    import importlib
    import server as server_module
    importlib.reload(server_module)
    server_module.AUDIT_DB_PATH = db_path
    from db.schema import init_db
    init_db(db_path)
    tc = TestClient(server_module.app)
    tc.db_path = db_path  # type: ignore[attr-defined]
    return tc


def _run(db_path, ended_at):
    conn = sqlite3.connect(str(db_path))
    try:
        rid = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
            "ended_at) VALUES (?,?,?,?,?)",
            ("2026-05-25T00:00:00Z", "x.pdf", "completed",
             "2026-05-25T00:00:00Z", ended_at),
        ).lastrowid
        conn.commit()
        return rid
    finally:
        conn.close()


def _fact(db_path, run_id, uuid, status, updated_at, source="pdf"):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, source, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, uuid, "CY", "Company", 1.0, status, source, updated_at),
        )
        conn.commit()
    finally:
        conn.close()


def test_counts_manual_edits_after_run_end(client):
    rid = _run(client.db_path, "2026-05-25T10:00:00Z")
    # Agent-observed + cascade facts (not manual edits) — not counted.
    _fact(client.db_path, rid, "obs-1", "observed", "2026-05-25T11:00:00Z",
          source="pdf")
    _fact(client.db_path, rid, "casc-1", "observed", "2026-05-25T11:00:00Z",
          source="cascade")
    # A typed override AFTER run end — counted.
    _fact(client.db_path, rid, "edit-1", "user_override", "2026-05-25T11:00:00Z",
          source="manual edit")
    # A CLEARED cell after run end (not_disclosed via manual edit) — also
    # counted (the bug this guards: keying on user_override alone missed it).
    _fact(client.db_path, rid, "edit-2", "not_disclosed", "2026-05-25T11:00:00Z",
          source="manual edit")
    # A manual edit stamped BEFORE run end — not counted.
    _fact(client.db_path, rid, "edit-0", "user_override", "2026-05-25T09:30:00Z",
          source="manual edit")

    r = client.get(f"/api/runs/{rid}/facts/edited_count")
    assert r.status_code == 200
    assert r.json()["count"] == 2


def test_running_run_reports_zero(client):
    rid = _run(client.db_path, None)
    _fact(client.db_path, rid, "edit-1", "user_override", "2026-05-25T11:00:00Z")
    r = client.get(f"/api/runs/{rid}/facts/edited_count")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_unknown_run_404(client):
    assert client.get("/api/runs/9999/facts/edited_count").status_code == 404
