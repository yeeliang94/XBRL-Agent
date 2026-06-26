"""Notes-reviewer API endpoints (docs/PLAN.md Step 10).

GET /notes-review, POST /notes-flags/{id}/answer, async POST /notes-review/
re-review + /status, POST /notes-review/revert-to-original. Modeled on
tests/test_reviewer_routes.py.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

_S12 = "Notes-Listofnotes"
_PREFIX = "mfrs-company-"


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    db = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    import importlib
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db

    from db.schema import init_db
    from db import repository as repo
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir=str(tmp_path))
        # A real Sheet-12 collision (notes 4 + 20 on one row) + an empty LEAF.
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=_S12, row=49,
                               label="Disclosure of fair value information",
                               html="<p>fair value</p>")
        conn.execute(
            "INSERT INTO notes_nodes(node_uuid, template_id, sheet, row, label, kind) "
            "VALUES (?, ?, ?, ?, ?, 'LEAF')",
            ("n80", f"{_PREFIX}notes-listofnotes-v1", _S12, 80,
             "Disclosure of financial instruments"),
        )
        repo.upsert_notes_provenance(
            conn, run_id=run_id, sheet=_S12, row=49,
            row_label="Disclosure of fair value information",
            source_note_refs=["4.1", "20.7"], content_preview="fv",
        )
    # Mock PDF rendering so the reviewer's grounding works without a real file.
    import notes.detectors as det  # _render_single_page (PDF render) lives here now
    import notes.reviewer_agent as ra
    monkeypatch.setattr(ra, "count_pdf_pages", lambda _p: 60)
    monkeypatch.setattr(det, "render_pages_to_png_bytes",
                        lambda pdf_path, start, end, dpi=200: [b"png"])
    (tmp_path / "uploaded.pdf").write_bytes(b"%PDF-1.4")
    return TestClient(srv.app), db, run_id, srv


def _await(tc, run_id, timeout_s=10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        s = tc.get(f"/api/runs/{run_id}/notes-review/status")
        assert s.status_code == 200, s.text
        body = s.json()
        if body.get("status") == "done":
            return body
        time.sleep(0.05)
    raise AssertionError("notes re-review did not finish")


def _move_then_flag(messages, info):
    """Scripted reviewer: view, move the collision, flag, finish."""
    n = len([m for m in messages if m.kind == "response"])
    if n == 0:
        return ModelResponse(parts=[ToolCallPart(
            tool_name="view_pdf_pages", args={"pages": [36]})])
    if n == 1:
        return ModelResponse(parts=[ToolCallPart(
            tool_name="move_note_cell", args={
                "from_sheet": _S12, "from_row": 49, "to_sheet": _S12,
                "to_row": 80, "source_pages": [36]})])
    if n == 2:
        return ModelResponse(parts=[ToolCallPart(
            tool_name="raise_flag", args={
                "kind": "needs_human", "reason": "double-check split"})])
    return ModelResponse(parts=[TextPart("done")])


def test_get_notes_review_empty_before_pass(client):
    tc, db, run_id, srv = client
    r = tc.get(f"/api/runs/{run_id}/notes-review")
    assert r.status_code == 200
    body = r.json()
    assert body["has_reviewer_version"] is False
    assert body["diff"] == [] and body["flags"] == []


def test_async_re_review_fixes_and_flags(client, monkeypatch):
    tc, db, run_id, srv = client
    monkeypatch.setattr(srv, "_create_proxy_model",
                        lambda *a, **k: FunctionModel(_move_then_flag))
    r = tc.post(f"/api/runs/{run_id}/notes-review/re-review", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"

    done = _await(tc, run_id)
    assert done["ok"] is True
    assert done["writes_performed"] == 1
    assert done["flags_raised"] == 1

    # The review surface now shows the diff + the flag.
    review = tc.get(f"/api/runs/{run_id}/notes-review").json()
    assert review["has_reviewer_version"] is True
    assert any(d["change"] in ("authored", "cleared") for d in review["diff"])
    assert review["flags"] and review["flags"][0]["kind"] == "needs_human"

    # Answer the flag → open → answered.
    fid = review["flags"][0]["id"]
    a = tc.post(f"/api/runs/{run_id}/notes-flags/{fid}/answer",
                json={"answer": "looks fine"})
    assert a.status_code == 200 and a.json()["status"] == "answered"


def test_revert_restores_original_prose(client, monkeypatch):
    tc, db, run_id, srv = client
    monkeypatch.setattr(srv, "_create_proxy_model",
                        lambda *a, **k: FunctionModel(_move_then_flag))
    tc.post(f"/api/runs/{run_id}/notes-review/re-review", json={})
    _await(tc, run_id)

    # Row 49 was moved to 80; revert puts it back.
    rev = tc.post(f"/api/runs/{run_id}/notes-review/revert-to-original")
    assert rev.status_code == 200 and rev.json()["reverted"] is True
    from db import repository as repo
    with repo.db_session(db) as conn:
        rows = {c.row for c in repo.list_notes_cells_for_run(conn, run_id)}
    assert rows == {49}  # original restored, authored move-target removed


def test_re_review_preserves_prior_answered_flags(client, monkeypatch):
    """Peer-review HIGH: a fresh pass must NOT erase a human-answered flag. The
    success path supersedes only prior OPEN flags; answered guidance survives."""
    tc, db, run_id, srv = client
    from db import repository as repo
    # Seed a prior flag and answer it (human guidance).
    with repo.db_session(db) as conn:
        fid = repo.insert_notes_review_flag(
            conn, run_id=run_id, kind="disputes_prior", reason="old finding")
        repo.answer_notes_review_flag(
            conn, flag_id=fid, run_id=run_id, answer="keep as is")

    monkeypatch.setattr(srv, "_create_proxy_model",
                        lambda *a, **k: FunctionModel(_move_then_flag))
    tc.post(f"/api/runs/{run_id}/notes-review/re-review", json={})
    _await(tc, run_id)

    flags = tc.get(f"/api/runs/{run_id}/notes-review").json()["flags"]
    answered = [f for f in flags if f["id"] == fid]
    assert answered and answered[0]["status"] == "answered"
    assert answered[0]["answer"] == "keep as is", "human answer was erased"
    # The new pass's flag is also present (open).
    assert any(f["kind"] == "needs_human" and f["status"] == "open" for f in flags)


def test_revert_without_version_409(client):
    tc, db, run_id, srv = client
    r = tc.post(f"/api/runs/{run_id}/notes-review/revert-to-original")
    assert r.status_code == 409


def test_stale_running_task_reconciled_at_startup(client):
    tc, db, run_id, srv = client
    from db import repository as repo
    with repo.db_session(db) as conn:
        repo.upsert_notes_review_task(conn, run_id, "running", model="m")
    # Simulate a restart's reconcile.
    with repo.db_session(db) as conn:
        n = repo.reconcile_stale_notes_review_tasks(conn)
    assert n == 1
    s = tc.get(f"/api/runs/{run_id}/notes-review/status").json()
    assert s["status"] == "done" and s["ok"] is False
