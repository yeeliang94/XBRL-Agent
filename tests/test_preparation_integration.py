"""Upload preparation integration, using only scripted local workers/models."""
import asyncio
import json
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import server
import api.preparation as preparation
from api.preparation import ensure_prepared as real_ensure, start_preparation as real_start
from coordinator import AgentResult, CoordinatorResult
from db.schema import init_db
from db import repository as repo
from scout.infopack import Infopack, StatementPageRef
from statement_types import StatementType
from workbook_merger import MergeResult


@pytest.fixture
def document(tmp_path, monkeypatch):
    directory = tmp_path / "5a83128b-95ce-4ef7-8d5c-8755a035e9ba"
    directory.mkdir()
    (directory / "uploaded.pdf").write_bytes(b"%PDF-1.4 synthetic")
    db = tmp_path / "audit.db"
    init_db(db)
    conn = sqlite3.connect(db)
    run_id = repo.create_run(conn, "fixture.pdf", session_id=directory.name,
                             output_dir=str(directory), status="draft")
    conn.commit()
    conn.close()
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.setattr(server, "_reload_runtime_settings", lambda: None)
    monkeypatch.setattr(server, "_resolve_api_key", lambda: "synthetic-key")
    # Even accidental provider construction cannot reach a live endpoint.
    monkeypatch.setattr(server, "_create_proxy_model", lambda *a, **k: "scripted-model")
    return directory, db, run_id


@pytest.fixture
def pipeline(document, monkeypatch):
    import coordinator
    import notes.coordinator
    import notes.source_manifest
    import ingest.document_preparation
    import scout.runner
    import workbook_merger
    import cross_checks.framework
    directory, db, run_id = document
    pack = Infopack(toc_page=None, page_offset=0, statements={StatementType.SOFP: StatementPageRef(
        variant_suggestion="CuNonCu", face_page=1)})
    snapshot = {"attempt_id": "prepared", "status": "succeeded", "infopack": json.loads(pack.to_json()),
                "model_name": "scripted-model", "configuration_key": "cfg"}
    join = AsyncMock(return_value=snapshot)
    scout = AsyncMock(side_effect=AssertionError("Prepared extraction must not call Scout again"))
    received = {}
    async def extract(config, infopack=None, event_queue=None, **kwargs):
        received.update(pdf=config.pdf_path, infopack=infopack)
        result = AgentResult(statement_type=StatementType.SOFP, variant="CuNonCu", status="succeeded",
                             workbook_path=str(directory / "SOFP_filled.xlsx"))
        if event_queue is not None:
            await event_queue.put({"event": "complete", "data": {"success": True,
                "agent_id": "sofp", "agent_role": "SOFP", "workbook_path": result.workbook_path}})
            await event_queue.put(None)
        return CoordinatorResult(agent_results=[result])
    async def extract_notes(*args, event_queue=None, **kwargs):
        if event_queue is not None:
            await event_queue.put(None)
        return notes.coordinator.NotesCoordinatorResult(agent_results=[])
    monkeypatch.setattr(preparation, "ensure_prepared", join)
    monkeypatch.setattr(scout.runner, "run_scout_streaming", scout)
    monkeypatch.setattr(coordinator, "run_extraction", extract)
    monkeypatch.setattr(notes.coordinator, "run_notes_extraction", extract_notes)
    monkeypatch.setattr(ingest.document_preparation, "read_prepared_document", lambda *a, **k: SimpleNamespace(
        prepared_pdf_path=directory / "prepared.pdf", metadata_path=directory / "preparation.json"))
    monkeypatch.setattr(notes.source_manifest, "build_prepared_manifest", lambda *a, **k: (object(), None))
    monkeypatch.setattr(notes.source_manifest, "freeze_manifest", lambda *a, **k: 1)
    monkeypatch.setattr(workbook_merger, "merge", lambda *a, **k: MergeResult(
        success=True, output_path=str(directory / "filled.xlsx"), sheets_copied=1))
    monkeypatch.setattr(cross_checks.framework, "run_all", lambda *a, **k: [])
    monkeypatch.setattr(cross_checks.framework, "run_all_facts", lambda *a, **k: [])
    monkeypatch.setattr(server, "_run_notes_advisories", AsyncMock(return_value=[]))
    monkeypatch.setattr(server, "_notes_auto_review_enabled", lambda: False)
    monkeypatch.setattr(server, "_notes_coverage_enabled", lambda: False)
    return TestClient(server.app), join, scout, received


def test_prepared_run_reuses_inventory_and_derived_pages_without_second_scout(document, pipeline):
    directory, _, _ = document
    client, join, scout, received = pipeline
    response = client.post(f"/api/run/{directory.name}", json={
        "statements": ["SOFP"], "variants": {"SOFP": "CuNonCu"}, "use_scout": True,
        "denomination": "thousands",
    })
    assert response.status_code == 200
    assert "run_complete" in response.text, response.text[-1500:]
    join.assert_awaited_once()
    scout.assert_not_awaited()
    assert received["pdf"] == str(directory / "prepared.pdf")
    assert received["infopack"].statements[StatementType.SOFP].face_page == 1


@pytest.mark.asyncio
async def test_prepared_repeats_preserve_request_and_use_preparation_each_time(document, pipeline):
    directory, db, _ = document
    _, join, scout, _ = pipeline
    config = server.RunConfigRequest(statements=["SOFP"], variants={"SOFP": "CuNonCu"},
                                    use_scout=True, repeats=2, denomination="thousands")
    original = config.model_dump()
    events = [event async for event in server.run_repeat_group_stream(
        session_id=directory.name, session_dir=directory, run_config=config,
        api_key="synthetic-key", proxy_url="", model_name="scripted-model",
    )]
    assert sum(event["event"] == "run_complete" for event in events) == 2
    assert config.model_dump() == original
    assert [call.args[0] for call in join.await_args_list] == [directory, directory / "repeat_1"]
    scout.assert_not_awaited()
    with sqlite3.connect(db) as conn:
        configs = conn.execute("SELECT DISTINCT run_config_json FROM runs WHERE repeat_group_id IS NOT NULL").fetchall()
    assert len(configs) == 1


@pytest.mark.parametrize("assessment_fails", [False, True])
def test_prepared_notes_cannot_finish_clean_when_integrity_assessment_raises(document, pipeline, monkeypatch, assessment_fails):
    directory, db, _ = document
    client, _, _, _ = pipeline
    assess = Mock(side_effect=RuntimeError("synthetic integrity failure")) if assessment_fails else Mock(return_value={"tips_status": False, "requires_review": False, "missing_block_ids": []})
    monkeypatch.setattr(server, "_run_notes_integrity_check", assess)
    response = client.post(f"/api/run/{directory.name}", json={
        "statements": ["SOFP"], "variants": {"SOFP": "CuNonCu"}, "use_scout": True,
        "denomination": "thousands",
        "notes_to_run": ["CORP_INFO"],
    })
    assert response.status_code == 200
    assert assess.called, response.text[-2000:]
    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    assert status == ("completed_with_errors" if assessment_fails else "completed"), response.text[-2000:]


@pytest.mark.asyncio
async def test_configuration_changed_while_waiting_revalidates_before_return(document, monkeypatch):
    import ingest.document_preparation as capture
    directory, _, run_id = document
    old = {"attempt_id": "old", "status": "working", "configuration_key": "old"}
    done = {**old, "status": "succeeded"}
    fresh = {"attempt_id": "new", "status": "succeeded", "configuration_key": "new"}
    states = iter([old, done, fresh])
    monkeypatch.setattr(preparation, "snapshot", lambda _: next(states, fresh))
    monkeypatch.setattr(preparation, "_configuration", lambda: ("model", "scout", "new"))
    checked = Mock(side_effect=[None, object()])
    monkeypatch.setattr(capture, "read_prepared_document", checked)
    restart = Mock(return_value=fresh)
    monkeypatch.setattr(preparation, "start_preparation", restart)
    result = await real_ensure(directory, run_id)
    assert result["attempt_id"] == "new"
    restart.assert_called_once_with(directory, run_id, retry=True)
    assert checked.call_args.kwargs["configuration_key"] == "new"


def test_retry_during_terminal_worker_cleanup_never_replaces_its_task(document, monkeypatch):
    directory, _, run_id = document
    terminal = threading.Event()
    release = threading.Event()
    def worker(path, db, rid, attempt):
        preparation._update(path, attempt, status="failed", message="Retry")
        terminal.set()
        release.wait(3)
    monkeypatch.setattr(preparation, "_worker", worker)
    first = real_start(directory, run_id)
    thread = preparation._workers[preparation._key(directory)]
    try:
        assert terminal.wait(1)
        second = real_start(directory, run_id, retry=True)
        assert second["attempt_id"] == first["attempt_id"]
        assert preparation._workers[preparation._key(directory)] is thread
    finally:
        release.set()
        thread.join(3)
        preparation._workers.pop(preparation._key(directory), None)


@pytest.mark.asyncio
async def test_late_cancellation_cannot_activate_ready_snapshot(document, monkeypatch):
    directory, _, _ = document
    state = {"attempt_id": "a", "status": "working", "stage": "scouting"}
    preparation._write(directory, state)
    monkeypatch.setattr(preparation, "snapshot", lambda path: preparation._read(path))
    cancel = Mock(return_value=True)
    monkeypatch.setattr(preparation.task_registry, "cancel_agent", cancel)
    await preparation.preparation_cancel(directory.name)
    with pytest.raises(asyncio.CancelledError):
        preparation._update(directory, "a", status="succeeded", infopack={})
    cancel.assert_called_once_with(directory.name, "source-preparation")
    assert preparation._read(directory)["status"] != "succeeded"


def test_preparation_endpoints_require_auth_without_dispatching(document, monkeypatch):
    monkeypatch.delenv("AUTH_MODE", raising=False)
    dispatch = Mock(side_effect=AssertionError("Unauthenticated request dispatched paid work"))
    monkeypatch.setattr(preparation, "start_preparation", dispatch)
    client = TestClient(server.app)
    directory, _, _ = document
    for method, suffix in (("GET", ""), ("POST", ""), ("POST", "/cancel")):
        response = client.request(method, f"/api/preparation/{directory.name}{suffix}")
        assert response.status_code == 401
    dispatch.assert_not_called()


def test_empty_ui_inventory_overrides_do_not_repeat_document_mapping(document, pipeline, monkeypatch):
    import scout.prepared_map
    directory, db, _ = document
    client, _, _, _ = pipeline
    mapper = AsyncMock(side_effect=AssertionError("Empty UI overrides must reuse upload map"))
    monkeypatch.setattr(scout.prepared_map, "build_prepared_document_map", mapper)
    monkeypatch.setattr(server, "_run_notes_integrity_check", Mock(return_value={
        "tips_status": False, "requires_review": False, "missing_block_ids": [],
    }))
    response = client.post(f"/api/run/{directory.name}", json={
        "statements": ["SOFP"], "variants": {"SOFP": "CuNonCu"}, "use_scout": True,
        "denomination": "thousands",
        "notes_to_run": ["CORP_INFO"],
        "notes_inventory_overrides": {"added": [], "removed_note_nums": []},
    })
    assert response.status_code == 200
    mapper.assert_not_awaited()
    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    assert status == "completed", response.text[-2000:]


@pytest.mark.asyncio
@pytest.mark.parametrize("user_stop", [True, False])
async def test_inventory_remap_cancellation_distinguishes_stop_from_failure(document, pipeline, monkeypatch, user_stop):
    import observability.incidents
    import task_registry
    directory, db, _ = document
    reason = task_registry.USER_ABORT_REASON if user_stop else "provider_interrupted"
    remap = AsyncMock(side_effect=asyncio.CancelledError(reason))
    monkeypatch.setattr(preparation, "remap_prepared_inventory", remap)
    incident = Mock()
    monkeypatch.setattr(observability.incidents, "capture_run_incident", incident)
    config = server.RunConfigRequest(
        statements=["SOFP"], variants={"SOFP": "CuNonCu"}, use_scout=True,
        denomination="thousands",
        notes_to_run=["CORP_INFO"],
        notes_inventory_overrides={"added": [{"note_num": 1, "title": "Corporate information", "page_range": [1, 1]}]},
    )
    events = server.run_multi_agent_stream(
        directory.name, directory, config, "synthetic-key", "", "scripted-model",
        require_preparation=True,
    )
    with pytest.raises(asyncio.CancelledError):
        async for _ in events:
            pass
    remap.assert_awaited_once()
    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    assert status == ("aborted" if user_stop else "failed")
    if user_stop:
        incident.assert_not_called()
    else:
        incident.assert_called_once()


@pytest.mark.asyncio
async def test_unresolved_source_does_not_repeat_full_review(
    document, pipeline, monkeypatch,
):
    """One review leaves remaining gaps visible without another paid pass."""
    import openpyxl
    import notes.coordinator
    import task_registry
    from notes_types import NotesTemplateType

    directory, db, _ = document
    for filename in ("SOFP_filled.xlsx", "NOTES_CORP_INFO_filled.xlsx", "filled.xlsx"):
        workbook = openpyxl.Workbook()
        workbook.save(directory / filename)
        workbook.close()

    async def extract_notes(*args, **kwargs):
        return notes.coordinator.NotesCoordinatorResult(agent_results=[
            notes.coordinator.NotesAgentResult(
                template_type=NotesTemplateType.CORP_INFO, status="succeeded",
                workbook_path=str(directory / "NOTES_CORP_INFO_filled.xlsx"),
            ),
        ])

    monkeypatch.setattr(notes.coordinator, "run_notes_extraction", extract_notes)
    monkeypatch.setattr(server, "_should_auto_format_pdf_notes", lambda *a, **k: False)
    monkeypatch.setattr(server, "_refresh_merged_notes_workbook", Mock())
    monkeypatch.setattr(server, "_run_notes_integrity_check", Mock(side_effect=[
        {"tips_status": True, "missing_block_ids": ["unplaced-block"]},
        {"tips_status": False, "missing_block_ids": []},
    ]))
    monkeypatch.setattr(server, "_retry_missing_source_blocks", lambda rid, gen, mode, outcome, report: outcome)
    outcomes = []

    async def review(**kwargs):
        attempt = len(outcomes) + 1
        outcome = {
            "error": None, "writes_performed": 0,
            "total_tokens": 150 * attempt, "total_cost": 0.01 * attempt,
            "prompt_tokens": 100 * attempt, "completion_tokens": 50 * attempt,
            "turns_used": 1, "tool_call_count": 1,
            "coverage": {"unresolved": 0},
            "turn_records": [{
                "turn_index": 1, "node_kind": "call_tools", "tool_names": "read_source_blocks",
                "total_tokens": 150 * attempt, "prompt_tokens": 100 * attempt,
                "completion_tokens": 50 * attempt,
            }],
        }
        outcomes.append(outcome)
        with repo.db_session(db) as conn:
            task = repo.fetch_notes_review_task(conn, kwargs["run_id"])
            assert task["status"] == "running"
            rows = conn.execute(
                "SELECT status, total_tokens FROM run_agents WHERE run_id=? AND statement_type=? ORDER BY id",
                (kwargs["run_id"], server.NOTES_VALIDATOR_AGENT_ID),
            ).fetchall()
            assert len(rows) == attempt
            assert rows[-1][0] == "running"
        await kwargs["finalize_gate"].wait()
        return outcome

    monkeypatch.setattr(server, "_run_notes_reviewer_pass", review)
    events = server.run_multi_agent_stream(
        directory.name, directory,
        server.RunConfigRequest(statements=["SOFP"], variants={"SOFP": "CuNonCu"},
                                notes_to_run=["CORP_INFO"], use_scout=True,
                                denomination="thousands"),
        "synthetic-key", "", "scripted-model", require_preparation=True,
    )
    async for _ in events:
        pass
    assert len(outcomes) == 1
    with repo.db_session(db) as conn:
        run_id, run_status = conn.execute("SELECT id, status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        rows = conn.execute(
            "SELECT status, total_tokens FROM run_agents WHERE run_id=? AND statement_type=?",
            (run_id, server.NOTES_VALIDATOR_AGENT_ID),
        ).fetchall()
        assert [tuple(row) for row in rows] == [("completed", 150)]
        assert repo.fetch_notes_review_task(conn, run_id)["status"] == "done"
        assert run_status == "completed_with_errors"
    assert task_registry.get_task(directory.name, server.NOTES_VALIDATOR_AGENT_ID) is None
