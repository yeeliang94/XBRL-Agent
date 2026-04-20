"""Tests covering the four peer-review fixes for the notes pipeline:

1. LIST_OF_NOTES is hidden from the public CLI/API until Phase C lands.
2. Notes agents are pre-created in `run_agents` and their tool events are
   keyed through `persist_event` so History can show them.
3. A notes-coordinator exception (distinct from per-agent failure) is
   folded into overall_status / run_complete.success — no silent success.
4. Notes tasks are registered in `task_registry` under the shared
   session, so `/api/abort/{session_id}` cancels them alongside face.

These tests mock both coordinators to stay fast and deterministic.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from coordinator import CoordinatorResult
from notes.coordinator import NotesAgentResult, NotesCoordinatorResult
from notes_types import NotesTemplateType


# ---------------------------------------------------------------------------
# Helpers (mirror tests/test_server_notes_api.py so the patterns stay close)
# ---------------------------------------------------------------------------


def _session(tmp_path: Path) -> tuple[TestClient, str, Path]:
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    client = TestClient(server_module.app)
    session_id = str(uuid.uuid4())
    upload_dir = tmp_path / session_id
    upload_dir.mkdir(parents=True)
    (upload_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4\n%fake\n")
    return client, session_id, server_module.AUDIT_DB_PATH


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.strip().split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln]
        if not lines:
            continue
        evt: dict = {}
        for ln in lines:
            if ln.startswith("event:"):
                evt["event"] = ln.split(":", 1)[1].strip()
            elif ln.startswith("data:"):
                evt["data"] = json.loads(ln.split(":", 1)[1].strip())
        if "event" in evt:
            events.append(evt)
    return events


# ---------------------------------------------------------------------------
# Fix #3 superseded by Phase C: LIST_OF_NOTES is now a public choice.
# The surviving guard is that UNKNOWN notes keys still fail cleanly.
# ---------------------------------------------------------------------------


def test_cli_exposes_list_of_notes_after_phase_c():
    """Phase C wiring: `list_of_notes` is a real --notes choice."""
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(repo_root / "run.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "corporate_info" in result.stdout
    assert "list_of_notes" in result.stdout


@pytest.mark.asyncio
async def test_api_rejects_unknown_notes_key_with_clear_error(tmp_path: Path, monkeypatch):
    """POST /api/run with an unknown notes key should fail cleanly."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    client, session_id, _ = _session(tmp_path)

    with patch("server._create_proxy_model", return_value="fake"), \
         patch("coordinator.run_extraction", return_value=CoordinatorResult()), \
         patch("notes.coordinator.run_notes_extraction", return_value=NotesCoordinatorResult()):
        resp = client.post(f"/api/run/{session_id}", json={
            "statements": [],
            "notes_to_run": ["NOT_A_REAL_TEMPLATE"],
        })

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    errs = [e for e in events if e["event"] == "error"]
    assert any("NOT_A_REAL_TEMPLATE" in e["data"].get("message", "") for e in errs), (
        f"No error mentioning the unknown template found in: {errs}"
    )
    rc = next(e for e in events if e["event"] == "run_complete")
    assert rc["data"]["success"] is False


# ---------------------------------------------------------------------------
# Fix #1: notes agents land in run_agents + events persist via agent_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_agents_persisted_to_run_agents_and_events(
    tmp_path: Path, monkeypatch,
):
    """A notes run should leave a run_agents row + tool events in the DB."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    client, session_id, audit_db = _session(tmp_path)

    wb_path = tmp_path / session_id / "NOTES_CORP_INFO_filled.xlsx"
    fake_notes = NotesCoordinatorResult(agent_results=[
        NotesAgentResult(
            template_type=NotesTemplateType.CORP_INFO,
            status="succeeded",
            workbook_path=str(wb_path),
        ),
    ])

    async def mock_face(config, infopack=None, event_queue=None, session_id=None, **_kw):
        return CoordinatorResult(agent_results=[])

    async def mock_notes(config, infopack=None, event_queue=None, session_id=None, **_kw):
        # Simulate the real coordinator's agent_id emissions so persist_event
        # has something to route. `tool_call` exercises the notes:CORP_INFO
        # → run_agent_id lookup inside persist_event.
        if event_queue is not None:
            await event_queue.put({
                "event": "tool_call",
                "data": {
                    "agent_id": "notes:CORP_INFO",
                    "agent_role": "CORP_INFO",
                    "tool_name": "read_template",
                    "tool_call_id": "t1",
                    "args": {},
                },
            })
            await event_queue.put({
                "event": "complete",
                "data": {
                    "agent_id": "notes:CORP_INFO",
                    "agent_role": "CORP_INFO",
                    "success": True,
                    "workbook_path": str(wb_path),
                },
            })
        return fake_notes

    import openpyxl
    wb = openpyxl.Workbook(); wb.active.title = "Notes-CI"
    wb.save(wb_path); wb.close()

    with patch("server._create_proxy_model", return_value="fake"), \
         patch("coordinator.run_extraction", side_effect=mock_face), \
         patch("notes.coordinator.run_notes_extraction", side_effect=mock_notes), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json={
            "statements": [],
            "notes_to_run": ["CORP_INFO"],
        })

    assert resp.status_code == 200

    # Inspect the audit DB directly — the run_agents row for the notes
    # template must exist, and the tool_call event must be persisted.
    conn = sqlite3.connect(audit_db)
    conn.row_factory = sqlite3.Row
    try:
        agents = conn.execute(
            "SELECT statement_type, status, workbook_path FROM run_agents "
            "WHERE statement_type LIKE 'NOTES_%'"
        ).fetchall()
        assert len(agents) == 1, f"expected one notes agent row, got {list(map(dict, agents))}"
        row = dict(agents[0])
        assert row["statement_type"] == "NOTES_CORP_INFO"
        assert row["status"] == "succeeded"
        assert row["workbook_path"] == str(wb_path)

        events = conn.execute(
            "SELECT ae.event_type FROM agent_events ae "
            "JOIN run_agents ra ON ae.run_agent_id = ra.id "
            "WHERE ra.statement_type = 'NOTES_CORP_INFO'"
        ).fetchall()
        event_types = {r["event_type"] for r in events}
        # tool_call and complete both went through persist_event's mapping.
        assert "tool_call" in event_types
        assert "complete" in event_types
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fix #2: notes coordinator exception flips run_complete.success to False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_coordinator_exception_fails_overall_run(
    tmp_path: Path, monkeypatch,
):
    """If the notes coordinator raises, the run must not report success."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    client, session_id, audit_db = _session(tmp_path)

    async def mock_face(config, infopack=None, event_queue=None, session_id=None, **_kw):
        # Successful (empty) face run — the only way the whole thing could
        # still report success is if the notes crash were silently dropped.
        return CoordinatorResult(agent_results=[])

    async def exploding_notes(config, infopack=None, event_queue=None, session_id=None, **_kw):
        raise RuntimeError("notes boom")

    with patch("server._create_proxy_model", return_value="fake"), \
         patch("coordinator.run_extraction", side_effect=mock_face), \
         patch("notes.coordinator.run_notes_extraction", side_effect=exploding_notes), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json={
            "statements": [],
            "notes_to_run": ["CORP_INFO"],
        })

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    rc = next(e for e in events if e["event"] == "run_complete")
    assert rc["data"]["success"] is False, (
        "Notes coordinator crashed — run_complete must not claim success"
    )
    assert "CORP_INFO" in rc["data"].get("notes_failed", []), (
        f"Expected CORP_INFO in notes_failed: {rc['data']}"
    )

    # Audit DB: the notes agent row must be marked failed.
    conn = sqlite3.connect(audit_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status FROM run_agents WHERE statement_type = 'NOTES_CORP_INFO'"
        ).fetchone()
        assert row is not None, "notes run_agents row was never created"
        assert row["status"] == "failed"
        run_row = conn.execute("SELECT status FROM runs").fetchone()
        assert run_row["status"] == "failed"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fix #4: notes tasks register in task_registry so abort can cancel them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_tasks_registered_in_task_registry():
    """run_notes_extraction(session_id=...) should register each task."""
    import task_registry
    from notes.coordinator import NotesRunConfig, run_notes_extraction

    session_id = f"test-{uuid.uuid4()}"

    # Monkey-patch the per-agent runner so we never touch LLMs or PDFs.
    # Hold it open until we've checked the registry, then return a result.
    release = asyncio.Event()

    async def stub_agent(*, template_type, **_kw):
        await release.wait()
        return NotesAgentResult(template_type=template_type, status="succeeded")

    with patch("notes.coordinator._run_single_notes_agent", side_effect=stub_agent):
        coord_task = asyncio.create_task(run_notes_extraction(
            NotesRunConfig(
                pdf_path="/tmp/fake.pdf",
                output_dir="/tmp",
                model="fake",
                notes_to_run={NotesTemplateType.CORP_INFO, NotesTemplateType.ACC_POLICIES},
            ),
            session_id=session_id,
        ))
        # Yield so the coordinator can launch per-template tasks.
        for _ in range(5):
            await asyncio.sleep(0)

        try:
            assert task_registry.get_task(session_id, "notes:CORP_INFO") is not None
            assert task_registry.get_task(session_id, "notes:ACC_POLICIES") is not None
        finally:
            release.set()
            await coord_task
            # The outer orchestrator normally cleans up — emulate that here.
            task_registry.remove_session(session_id)


@pytest.mark.asyncio
async def test_notes_tasks_cancellable_via_task_registry():
    """task_registry.cancel_all(session_id) should cancel notes tasks.

    This is the invariant the `/api/abort/{session_id}` endpoint relies on —
    it calls task_registry.cancel_all, so as long as notes tasks appear in
    the registry under the shared session_id, abort works for notes too.
    The HTTP endpoint itself is a one-liner over that API and is already
    covered by existing tests.
    """
    import task_registry
    from notes.coordinator import NotesRunConfig, run_notes_extraction

    session_id = f"abort-{uuid.uuid4()}"
    release = asyncio.Event()

    async def stub_agent(*, template_type, **_kw):
        try:
            await release.wait()
        except asyncio.CancelledError:
            return NotesAgentResult(
                template_type=template_type,
                status="cancelled",
                error="Cancelled by user",
            )
        return NotesAgentResult(template_type=template_type, status="succeeded")

    with patch("notes.coordinator._run_single_notes_agent", side_effect=stub_agent):
        coord_task = asyncio.create_task(run_notes_extraction(
            NotesRunConfig(
                pdf_path="/tmp/fake.pdf",
                output_dir="/tmp",
                model="fake",
                notes_to_run={NotesTemplateType.CORP_INFO},
            ),
            session_id=session_id,
        ))

        # Yield until the per-template task is registered.
        for _ in range(10):
            await asyncio.sleep(0)
            if task_registry.get_task(session_id, "notes:CORP_INFO") is not None:
                break
        else:
            release.set()
            await coord_task
            pytest.fail("notes task never appeared in task_registry")

        cancelled = task_registry.cancel_all(session_id)
        assert cancelled >= 1, "cancel_all did not cancel any notes tasks"

        # Free the stub and wait for coordinator to finish collecting results.
        release.set()
        result = await coord_task

        # The coordinator should report the cancelled status on the result.
        statuses = {r.status for r in result.agent_results}
        assert "cancelled" in statuses, (
            f"Expected a cancelled notes result, got {statuses}"
        )
        task_registry.remove_session(session_id)
