"""Force-abort escape hatch for wedged `running` runs (UX-QA #2).

The live Stop-All only reaches a run whose SSE stream is still in memory. A run
left `running` by a dead process is a History dead-end. POST
/api/runs/{id}/force-abort flips such a dead row to `aborted`; a genuinely-live
run is cancelled through task_registry instead of having its row clobbered.
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    db = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db
    srv.active_runs.clear()

    from db.schema import init_db
    init_db(db)
    return TestClient(srv.app), db, srv


def _insert_run(db: Path, *, status: str, session_id: str = "sess-1") -> int:
    conn = sqlite3.connect(str(db))
    try:
        rid = int(conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
            "started_at) VALUES ('2026-05-28T00:00:00Z', 'uploaded.pdf', ?, ?, "
            "'2026-05-28T00:00:00Z')",
            (status, session_id),
        ).lastrowid)
        conn.commit()
        return rid
    finally:
        conn.close()


def test_dead_running_row_is_reaped(client):
    tc, db, srv = client
    rid = _insert_run(db, status="running")

    resp = tc.post(f"/api/runs/{rid}/force-abort")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "reaped_dead"

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT status, ended_at FROM runs WHERE id = ?", (rid,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "aborted"
    assert row[1]  # ended_at stamped


def test_live_run_is_cancelled_not_clobbered(client, monkeypatch):
    tc, db, srv = client
    rid = _insert_run(db, status="running", session_id="live-sess")
    srv.active_runs.add("live-sess")

    calls = {}
    import task_registry
    monkeypatch.setattr(task_registry, "cancel_all",
                        lambda sid: calls.setdefault("sid", sid) or 3)

    resp = tc.post(f"/api/runs/{rid}/force-abort")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "cancelled_live"
    assert calls["sid"] == "live-sess"

    # DB row is left for the live stream's finally block to finalize.
    conn = sqlite3.connect(str(db))
    try:
        status = conn.execute(
            "SELECT status FROM runs WHERE id = ?", (rid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "running"


def test_non_running_run_returns_409(client):
    tc, db, srv = client
    rid = _insert_run(db, status="completed")
    resp = tc.post(f"/api/runs/{rid}/force-abort")
    assert resp.status_code == 409


def test_missing_run_returns_404(client):
    tc, db, srv = client
    resp = tc.post("/api/runs/999999/force-abort")
    assert resp.status_code == 404
