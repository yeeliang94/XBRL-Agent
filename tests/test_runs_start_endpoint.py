"""POST /api/runs/{id}/start — flip a draft into a running extraction.

Plan: docs/PLAN-persistent-draft-uploads.md — Phase B (steps 7-8).

This is the second half of the persistent-draft contract: after the
frontend has PATCHed config onto the draft, the user clicks "Start" and
this endpoint flips status `draft` → `running` and streams the same SSE
the legacy `POST /api/run/{session_id}` would have. The legacy path is
preserved (Step 23 regression pin) so CLI and Windows clients keep
working unchanged.
"""
from __future__ import annotations

import io
import json
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
def draft_with_config(tmp_path, monkeypatch):
    """Upload + PATCH a minimal SOFP draft, return everything the test
    needs to invoke the /start endpoint with realistic state."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    db_path = output_dir / "xbrl_agent.db"
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db_path)
    # Test-only env so the resolve-key path inside the start handler
    # does not raise on a missing key.
    fake_env = tmp_path / ".env-test"
    fake_env.write_text("")
    monkeypatch.setattr(server, "ENV_FILE", fake_env)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-xyz")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    init_db(db_path)

    client = TestClient(server.app)
    upload = client.post(
        "/api/upload",
        files={"file": ("S.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    payload = upload.json()
    run_id = payload["run_id"]
    session_id = payload["session_id"]

    # Patch a minimal valid config so /start can hand it to the coordinator.
    client.patch(
        f"/api/runs/{run_id}",
        json={
            "statements": ["SOFP"],
            "variants": {"SOFP": "CuNonCu"},
        },
    )
    return client, run_id, session_id, output_dir, db_path


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def test_start_flips_status_and_streams(draft_with_config):
    """Happy path: POST /api/runs/{id}/start runs the SSE stream and
    leaves the row in a terminal status with a real started_at."""
    client, run_id, session_id, output_dir, db_path = draft_with_config

    async def quiet_coordinator(config, infopack=None, event_queue=None, session_id=None, **_kwargs):
        if event_queue is not None:
            await event_queue.put(None)
        return CoordinatorResult(agent_results=[])

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=quiet_coordinator), \
         patch("workbook_merger.merge", return_value=MergeResult(success=True, output_path=str(output_dir / session_id / "filled.xlsx"), sheets_copied=0)), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/runs/{run_id}/start")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    # After the stream drains, the row must be in a terminal state and
    # have its started_at populated (the draft had started_at='').
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] in {"completed", "completed_with_errors"}
    assert row["started_at"], "started_at must be stamped when draft → running"
    assert row["ended_at"] is not None


def test_start_rejected_on_non_draft(draft_with_config):
    """A run that has already started cannot be re-started via /start."""
    client, run_id, session_id, output_dir, db_path = draft_with_config

    # Force the row out of draft.
    conn = _open_db(db_path)
    try:
        conn.execute(
            "UPDATE runs SET status = 'running' WHERE id = ?", (run_id,)
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.post(f"/api/runs/{run_id}/start")
    assert resp.status_code == 409


def test_start_rejected_with_no_config(tmp_path, monkeypatch):
    """A draft with no PATCHed config (statements list empty) must 422.

    Required-field validation lives at start time, not at upload time —
    the upload-creates-draft contract is intentionally permissive so the
    user can refresh-survive even before they've made any choices.
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    db_path = output_dir / "xbrl_agent.db"
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db_path)
    fake_env = tmp_path / ".env-test"
    fake_env.write_text("")
    monkeypatch.setattr(server, "ENV_FILE", fake_env)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-xyz")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    init_db(db_path)
    client = TestClient(server.app)

    upload = client.post(
        "/api/upload",
        files={"file": ("E.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    run_id = upload.json()["run_id"]

    # No PATCH — the draft's run_config_json is still NULL.
    resp = client.post(f"/api/runs/{run_id}/start")
    assert resp.status_code == 422
    body = resp.json()
    detail = json.dumps(body).lower()
    assert "statements" in detail


def test_infopack_survives_patch_and_reaches_coordinator(draft_with_config):
    """Peer-review HIGH #1 regression: scout output must NOT be silently
    dropped when starting a draft. The frontend PATCHes the infopack
    onto the draft after scout completes; /start reads it back from the
    DB and threads it to coordinator.run_extraction.
    """
    client, run_id, session_id, output_dir, db_path = draft_with_config

    # Simulate scout: build a real Infopack via the public API so the
    # round-trip through Infopack.from_json on the server side accepts it.
    from scout.infopack import Infopack, StatementPageRef
    from statement_types import StatementType
    infopack_obj = Infopack(
        toc_page=2,
        page_offset=0,
        statements={
            StatementType.SOFP: StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=10,
                note_pages=[15, 16],
                confidence="HIGH",
            ),
        },
        detected_standard="mfrs",
    )
    infopack_payload = json.loads(infopack_obj.to_json())
    resp = client.patch(f"/api/runs/{run_id}", json={"infopack": infopack_payload})
    assert resp.status_code == 200, resp.text

    captured = {}

    async def capturing_coord(config, infopack=None, event_queue=None, session_id=None, **_kw):
        captured["infopack"] = infopack
        if event_queue is not None:
            await event_queue.put(None)
        return CoordinatorResult(agent_results=[])

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=capturing_coord), \
         patch("workbook_merger.merge", return_value=MergeResult(success=True, output_path=str(output_dir / session_id / "filled.xlsx"), sheets_copied=0)), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/runs/{run_id}/start")

    assert resp.status_code == 200
    assert captured.get("infopack") is not None, (
        "infopack must reach coordinator — peer-review HIGH #1 regression"
    )
    # Confirm the round-trip preserved the page hint.
    from scout.infopack import Infopack
    assert isinstance(captured["infopack"], Infopack)


def test_start_does_not_create_a_second_row_on_race(draft_with_config):
    """Peer-review HIGH #3 regression: when the draft → running flip
    fails (because another request already flipped the row), /start
    must NOT silently create a brand-new row. That would break shareable
    URL semantics: /run/{42} would belong to a stale draft while a
    fresh row at id 43 carries the actual extraction.

    We simulate the race by patching repo.fetch_run to lie (return
    status='draft' even though the underlying row is 'running'). The
    pre-stream status check passes on the lie; the atomic flip catches
    the truth.
    """
    client, run_id, session_id, output_dir, db_path = draft_with_config

    # Flip status under the hood so the actual UPDATE must reject it.
    conn = _open_db(db_path)
    try:
        conn.execute(
            "UPDATE runs SET status='running' WHERE id=?", (run_id,)
        )
        conn.commit()
    finally:
        conn.close()

    # Lie to the upfront status check so it doesn't short-circuit. The
    # only thing left protecting us is the atomic flip in the endpoint.
    import db.repository as repo
    real_fetch = repo.fetch_run

    def lying_fetch(conn, rid):
        row = real_fetch(conn, rid)
        if row is not None and rid == run_id:
            from dataclasses import replace
            return replace(row, status="draft")
        return row

    async def quiet_coord(config, infopack=None, event_queue=None, session_id=None, **_kw):
        if event_queue is not None:
            await event_queue.put(None)
        return CoordinatorResult(agent_results=[])

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=quiet_coord), \
         patch("workbook_merger.merge", return_value=MergeResult(success=True, output_path=str(output_dir / session_id / "filled.xlsx"), sheets_copied=0)), \
         patch("cross_checks.framework.run_all", return_value=[]), \
         patch.object(repo, "fetch_run", side_effect=lying_fetch):
        resp = client.post(f"/api/runs/{run_id}/start")

    # Must be a clean 409 — NOT 200 with a new row created behind the scenes.
    assert resp.status_code == 409, (
        f"failed flip must be 409 (peer-review HIGH #3); got {resp.status_code} {resp.text}"
    )

    # Exactly one row in the table — never duplicated.
    conn = _open_db(db_path)
    try:
        rows = conn.execute("SELECT id, status FROM runs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, (
        f"expected one runs row; got {[(r['id'], r['status']) for r in rows]}"
    )
    assert rows[0]["id"] == run_id


def test_start_404_on_missing_run(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    db_path = output_dir / "xbrl_agent.db"
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db_path)
    init_db(db_path)
    client = TestClient(server.app)

    resp = client.post("/api/runs/9999/start")
    assert resp.status_code == 404
