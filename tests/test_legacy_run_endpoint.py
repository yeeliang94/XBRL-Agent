"""Pin the legacy POST /api/run/{session_id} contract after Phase B refactor.

Plan: docs/PLAN-persistent-draft-uploads.md — Phase E (step 23).

The persistent-draft work refactored `run_multi_agent_stream` to accept
an optional `existing_run_id`. The legacy upload-then-run flow (CLI +
Windows clients that haven't been updated to use the new run-id endpoint)
must continue creating a fresh `runs` row and reaching a terminal status
just like it did before. This test exercises that path end-to-end.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import server
from coordinator import CoordinatorResult
from db.schema import init_db
from workbook_merger import MergeResult


@pytest.fixture
def legacy_session(tmp_path, monkeypatch):
    """Set up a session directory + DB and skip the upload endpoint.

    The legacy flow points at an existing on-disk session that may NOT
    have a draft row in the audit DB (mirroring CLI / re-uploaded files).
    The test verifies that POST /api/run/{session_id} still creates the
    runs row at coordinator-launch time.
    """
    session_id = "legacy-session-xyz"
    out = tmp_path / "output"
    (out / session_id).mkdir(parents=True)
    (out / session_id / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    (out / session_id / "original_filename.txt").write_text("legacy.pdf", encoding="utf-8")

    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    db_path = out / "xbrl_agent.db"
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db_path)
    fake_env = tmp_path / ".env-test"
    fake_env.write_text("")
    monkeypatch.setattr(server, "ENV_FILE", fake_env)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    init_db(db_path)

    return TestClient(server.app), session_id, out, db_path


def test_legacy_post_run_session_creates_and_starts(legacy_session):
    """Hitting /api/run/{session_id} without a pre-existing draft must
    create a row + start running + reach a terminal status — identical
    behaviour to before the persistent-draft refactor."""
    client, session_id, out, db_path = legacy_session

    async def quiet_coord(config, infopack=None, event_queue=None, session_id=None, **_kw):
        if event_queue is not None:
            await event_queue.put(None)
        return CoordinatorResult(agent_results=[])

    body = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=quiet_coord), \
         patch("workbook_merger.merge", return_value=MergeResult(success=True, output_path=str(out / session_id / "filled.xlsx"), sheets_copied=0)), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=body)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM runs").fetchall()
    finally:
        conn.close()

    # Exactly one row, and it reached a terminal status — never stuck at
    # "draft" or "running". This is the regression pin: if the refactor
    # had accidentally tied row creation to existing_run_id-only, the
    # legacy path would silently stop persisting any History data.
    assert len(rows) == 1
    assert rows[0]["status"] in {"completed", "completed_with_errors"}
    assert rows[0]["session_id"] == session_id
    assert rows[0]["pdf_filename"] == "legacy.pdf"
    assert rows[0]["started_at"]  # non-empty
    assert rows[0]["ended_at"] is not None
