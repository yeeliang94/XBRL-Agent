"""The face and notes reviewers overlap without racing the merged workbook."""
from __future__ import annotations

import inspect
import sqlite3
from unittest.mock import patch

import openpyxl
import server
from fastapi.testclient import TestClient

from coordinator import AgentResult, CoordinatorResult
from cross_checks.framework import CrossCheckResult
from db import repository as repo
from notes.coordinator import NotesAgentResult, NotesCoordinatorResult
from notes_types import NotesTemplateType
from statement_types import StatementType
from workbook_merger import MergeResult


def test_notes_reviewer_launches_before_face_reviewer_is_awaited():
    source = inspect.getsource(server.run_multi_agent_stream)

    notes_start = source.find(
        "validator_task = asyncio.create_task(_run_notes_reviewer_pass("
    )
    face_start = source.find(
        "correction_task = asyncio.create_task(\n                    _run_reviewer_pass("
    )
    face_wait = source.find("correction_outcome = await correction_task")

    assert notes_start != -1
    assert face_start != -1
    assert face_wait != -1
    assert notes_start < face_start < face_wait


def test_parallel_notes_reviewer_defers_merged_workbook_refresh():
    source = inspect.getsource(server.run_multi_agent_stream)
    notes_start = source.find(
        "validator_task = asyncio.create_task(_run_notes_reviewer_pass("
    )
    notes_end = source.find("task_registry.register(", notes_start)
    launch = source[notes_start:notes_end]

    assert "merged_workbook_path=None" in launch
    assert "_refresh_merged_notes_workbook(" in source[notes_end:]


def test_live_pipeline_reviewers_are_in_flight_together(tmp_path, monkeypatch):
    """Drive the real run orchestrator and fail if either reviewer waits."""
    out = tmp_path / "output"
    session_id = "parallel-reviewers"
    session_dir = out / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", out / "xbrl_agent.db")
    monkeypatch.setattr(server, "ENV_FILE", tmp_path / ".env-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    monkeypatch.setenv("XBRL_AUTO_REVIEW", "true")
    monkeypatch.setenv("XBRL_NOTES_AUTO_REVIEW", "true")

    face_path = session_dir / "SOFP_filled.xlsx"
    notes_path = session_dir / "NOTES_CORP_INFO_filled.xlsx"
    merged_path = session_dir / "filled.xlsx"
    for path, title in (
        (face_path, "SOFP"),
        (notes_path, "Notes-CI"),
        (merged_path, "SOFP"),
    ):
        wb = openpyxl.Workbook()
        wb.active.title = title
        wb.save(path)
        wb.close()

    async def fake_face(*_args, event_queue=None, **_kwargs):
        if event_queue is not None:
            await event_queue.put(None)
        return CoordinatorResult(agent_results=[AgentResult(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            status="succeeded",
            workbook_path=str(face_path),
        )])

    async def fake_notes(*_args, **_kwargs):
        return NotesCoordinatorResult(agent_results=[NotesAgentResult(
            template_type=NotesTemplateType.CORP_INFO,
            status="succeeded",
            workbook_path=str(notes_path),
        )])

    started = {
        "face": False,
        "face_done": False,
        "notes": False,
        "notes_finalized": False,
        "overlap": False,
    }

    async def wait_for_peer(peer: str):
        for _ in range(100):
            if started[peer]:
                started["overlap"] = True
                return
            await __import__("asyncio").sleep(0.005)
        raise AssertionError(f"{peer} reviewer did not start concurrently")

    async def fake_face_review(**_kwargs):
        started["face"] = True
        await wait_for_peer("notes")
        started["face_done"] = True
        return {"writes_performed": 1, "error": None}

    async def fake_notes_review(**kwargs):
        assert kwargs["merged_workbook_path"] is None
        finalize_gate = kwargs["finalize_gate"]
        assert not finalize_gate.is_set()
        started["notes"] = True
        await wait_for_peer("face")
        await finalize_gate.wait()
        assert started["face_done"] is True
        # Reproduce the live run-97 failure at the real SQLite seam. A face
        # review that changes facts re-marks the merged workbook on the
        # lifecycle connection. If that write is not committed before the
        # finalization gate opens, both notes-review replacements hit the same
        # ``database is locked`` error shown in the operator's traceback.
        conn = sqlite3.connect(str(server.AUDIT_DB_PATH), timeout=0.05)
        conn.execute("PRAGMA busy_timeout = 50")
        try:
            repo.insert_notes_review_flag(
                conn,
                run_id=kwargs["run_id"],
                kind="needs_human",
                reason="confirm note placement",
                sheet="Notes-CI",
                row=4,
            )
            repo.replace_notes_coverage_for_run(conn, kwargs["run_id"], [{
                "note_num": -1,
                "subnote_ref": None,
                "status": "reviewed",
            }])
            conn.commit()
        finally:
            conn.close()
        started["notes_finalized"] = True
        return {"writes_performed": 0, "error": None}

    failing = [CrossCheckResult(
        name="balance", status="failed", expected=1.0, actual=0.0,
        diff=1.0, tolerance=0.5, message="failed",
    )]
    client = TestClient(server.app)
    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=fake_face), \
         patch("notes.coordinator.run_notes_extraction", side_effect=fake_notes), \
         patch("workbook_merger.merge", return_value=MergeResult(
             success=True, output_path=str(merged_path), sheets_copied=2,
         )), \
         patch("cross_checks.framework.run_all", return_value=failing), \
         patch("cross_checks.framework.run_all_facts", return_value=failing), \
         patch("correction.reviewer_agent.load_open_conflicts", return_value=[]), \
         patch("server._run_reviewer_pass", side_effect=fake_face_review), \
         patch("server._run_notes_reviewer_pass", side_effect=fake_notes_review), \
         patch("server._export_canonical_workbooks", return_value=None), \
         patch("tools.recalc.recalc_workbook", return_value=None):
        response = client.post(f"/api/run/{session_id}", json={
            "statements": ["SOFP"],
            "variants": {"SOFP": "CuNonCu"},
            "notes_to_run": ["CORP_INFO"],
            "use_scout": False,
        })

    assert response.status_code == 200
    assert started == {
        "face": True,
        "face_done": True,
        "notes": True,
        "notes_finalized": True,
        "overlap": True,
    }
    conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM notes_review_flags"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM notes_coverage_rows"
        ).fetchone()[0] == 1
    finally:
        conn.close()
