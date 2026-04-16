"""End-to-end server test for notes_to_run plumbing.

Mocks both face + notes coordinators so we exercise the request-parsing,
event-multiplexing, and merger-wiring paths without calling real LLMs.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from coordinator import AgentResult, CoordinatorResult
from notes.coordinator import NotesAgentResult, NotesCoordinatorResult
from notes_types import NotesTemplateType
from statement_types import StatementType
from workbook_merger import MergeResult


def _session(tmp_path: Path) -> tuple[TestClient, str]:
    """Spin up a TestClient pointed at a temp OUTPUT_DIR with a seeded upload."""
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    client = TestClient(server_module.app)
    session_id = str(uuid.uuid4())
    upload_dir = tmp_path / session_id
    upload_dir.mkdir(parents=True)
    (upload_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4\n%fake\n")
    return client, session_id


def _parse_sse(text: str) -> list[dict]:
    events = []
    blocks = text.strip().split("\n\n")
    for blk in blocks:
        lines = [ln for ln in blk.splitlines() if ln]
        if not lines:
            continue
        evt = {}
        for ln in lines:
            if ln.startswith("event:"):
                evt["event"] = ln.split(":", 1)[1].strip()
            elif ln.startswith("data:"):
                evt["data"] = json.loads(ln.split(":", 1)[1].strip())
        if "event" in evt:
            events.append(evt)
    return events


@pytest.mark.asyncio
async def test_notes_to_run_is_accepted_and_passed_to_coordinator(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    client, session_id = _session(tmp_path)

    fake_face = CoordinatorResult(agent_results=[])
    fake_notes = NotesCoordinatorResult(agent_results=[
        NotesAgentResult(
            template_type=NotesTemplateType.CORP_INFO,
            status="succeeded",
            workbook_path=str(tmp_path / session_id / "NOTES_CORP_INFO_filled.xlsx"),
        ),
    ])

    received = {}

    async def mock_face(config, infopack=None, event_queue=None, session_id=None, **_kwargs):
        return fake_face

    async def mock_notes(config, infopack=None, event_queue=None, session_id=None, **_kwargs):
        received["notes_to_run"] = set(config.notes_to_run)
        received["filing_level"] = config.filing_level
        if event_queue is not None:
            await event_queue.put({
                "event": "complete",
                "data": {
                    "agent_id": "notes:CORP_INFO",
                    "agent_role": "CORP_INFO",
                    "success": True,
                    "workbook_path": fake_notes.agent_results[0].workbook_path,
                },
            })
        return fake_notes

    # Seed an existing notes workbook so the merger has something to pick up.
    notes_wb = tmp_path / session_id / "NOTES_CORP_INFO_filled.xlsx"
    # The merger opens it with openpyxl — write a minimal valid workbook.
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = "Notes-CI"
    wb.save(notes_wb)
    wb.close()

    with patch("server._create_proxy_model", return_value="fake"), \
         patch("coordinator.run_extraction", side_effect=mock_face), \
         patch("notes.coordinator.run_notes_extraction", side_effect=mock_notes), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json={
            "statements": [],
            "variants": {},
            "models": {},
            "filing_level": "company",
            "notes_to_run": ["CORP_INFO"],
        })

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert received["notes_to_run"] == {NotesTemplateType.CORP_INFO}
    assert received["filing_level"] == "company"

    # run_complete event should include notes_completed
    rc = next((e for e in events if e["event"] == "run_complete"), None)
    assert rc is not None
    assert rc["data"].get("notes_completed") == ["CORP_INFO"]
    assert rc["data"].get("notes_failed") == []


@pytest.mark.asyncio
async def test_unknown_notes_template_rejected_with_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    client, session_id = _session(tmp_path)

    with patch("server._create_proxy_model", return_value="fake"), \
         patch("coordinator.run_extraction", return_value=CoordinatorResult()), \
         patch("notes.coordinator.run_notes_extraction", return_value=NotesCoordinatorResult()):
        resp = client.post(f"/api/run/{session_id}", json={
            "statements": [],
            "notes_to_run": ["MADE_UP"],
        })

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    errs = [e for e in events if e["event"] == "error"]
    assert any("MADE_UP" in e["data"].get("message", "") for e in errs)
    # Run must terminate with run_complete success=False
    rc = next(e for e in events if e["event"] == "run_complete")
    assert rc["data"]["success"] is False
