"""Peer-review HIGH (auto vs manual notes-review race).

The auto notes-reviewer pass now registers a durable `notes_review_tasks`
'running' row (server.run_multi_agent_stream) so the manual re-review
re-entrancy guard and the revert guard can both see an in-flight pass — the two
launch paths hold independent in-process locks, so the DB task state is the only
cross-launch interlock. These tests pin the two route-level guards:

  * POST /notes-review/re-review reports `already_running` when a pass is live,
  * POST /notes-review/revert-to-original returns 409 while a pass is live.
"""
from __future__ import annotations

import importlib
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

    from db.schema import init_db
    from db import repository as repo
    init_db(db)
    merged = tmp_path / "filled.xlsx"
    merged.write_bytes(b"PK\x03\x04")  # placeholder; guards fire before any read
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s",
                                 output_dir=str(tmp_path))
        repo.mark_run_merged(conn, run_id, str(merged))
    return TestClient(srv.app), db, run_id, srv


def _mark_running(db, run_id):
    from db import repository as repo
    with repo.db_session(db) as conn:
        repo.upsert_notes_review_task(conn, run_id, "running", model="m")


def test_re_review_reports_already_running_when_pass_live(client):
    tc, db, run_id, _ = client
    _mark_running(db, run_id)
    r = tc.post(f"/api/runs/{run_id}/notes-review/re-review", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "running"
    assert body.get("already_running") is True


def test_revert_blocked_while_pass_live(client):
    tc, db, run_id, _ = client
    _mark_running(db, run_id)
    r = tc.post(f"/api/runs/{run_id}/notes-review/revert-to-original")
    assert r.status_code == 409, r.text
    assert "running" in r.json()["detail"].lower()


def test_revert_allowed_once_pass_done(client):
    """A 'done' task does not block revert (only 'running' does). With no
    snapshot the revert is a clean 409-from-versioning (nothing to revert),
    proving the concurrency guard did NOT fire."""
    tc, db, run_id, _ = client
    from db import repository as repo
    with repo.db_session(db) as conn:
        repo.upsert_notes_review_task(conn, run_id, "done", model="m",
                                      outcome={"ok": True})
    r = tc.post(f"/api/runs/{run_id}/notes-review/revert-to-original")
    # 409 here is the "no snapshot to revert" path, with the no-version detail —
    # distinct from the concurrency guard's "currently running" detail.
    assert r.status_code == 409, r.text
    assert "running" not in r.json()["detail"].lower()
