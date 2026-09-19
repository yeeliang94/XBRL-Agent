"""Unified document-map orchestration with no live provider calls."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import server
import api.preparation as preparation
from api.preparation import ensure_prepared as real_ensure
from db import repository as repo
from db.schema import init_db
from scout.infopack import Infopack


@pytest.fixture
def mapped_upload(tmp_path, monkeypatch):
    import ingest.document_preparation as capture
    import scout.prepared_map as mapping
    import scout.runner
    import notes.source_manifest as manifest
    directory = tmp_path / "9aab3a86-a5ce-4c44-a2b5-92b260d9536e"
    directory.mkdir()
    (directory / "uploaded.pdf").write_bytes(b"%PDF synthetic local test")
    db = tmp_path / "audit.db"
    init_db(db)
    conn = sqlite3.connect(db)
    run_id = repo.create_run(conn, "fixture.pdf", session_id=directory.name,
                             output_dir=str(directory), status="draft")
    conn.commit()
    conn.close()
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(server, "_reload_runtime_settings", lambda: None)
    monkeypatch.setattr(server, "_resolve_api_key", lambda: "synthetic-key")
    monkeypatch.setattr(server, "_create_proxy_model", lambda *a, **k: object())
    monkeypatch.setattr(preparation, "_configuration", lambda: ("capture-model", "map-model", "cfg"))
    prepared = SimpleNamespace(metadata_path=directory / "preparation.json",
        prepared_pdf_path=directory / "prepared.pdf", revision="revision")
    pack = Infopack(toc_page=None, page_offset=0, statements={}, notes_inventory=[])
    assignments = [{"block_id": "p1b0", "owner_kind": "metadata"}]
    steps = []
    async def read(*args, **kwargs):
        steps.append("capture")
        kwargs["on_progress"]({"stage": "verifying", "message": "Checking source content",
                                "total": 30, "captured": 30, "checked": 30, "verified": 29})
        return prepared
    async def map_document(source, model, **kwargs):
        assert source is prepared
        steps.append("map")
        if kwargs.get("usage_out") is not None:
            kwargs["usage_out"].update(prompt_tokens=70, completion_tokens=30, total_tokens=100)
        return pack, assignments
    async def apply(source, **kwargs):
        steps.append("apply")
        assert source is prepared
        assert kwargs["assignments"] == assignments
        return prepared
    def validate(*args, **kwargs):
        steps.append("validate")
        return object(), None
    mapper = AsyncMock(side_effect=map_document)
    apply_map = AsyncMock(side_effect=apply)
    legacy_scout = AsyncMock(side_effect=AssertionError("Standalone Scout must not run after capture"))
    monkeypatch.setattr(capture, "prepare_document", read)
    monkeypatch.setattr(mapping, "build_prepared_document_map", mapper)
    monkeypatch.setattr(capture, "reconcile_prepared_inventory", apply_map)
    monkeypatch.setattr(capture, "read_prepared_document", lambda *a, **k: prepared)
    monkeypatch.setattr(scout.runner, "run_scout_streaming", legacy_scout)
    monkeypatch.setattr(manifest, "build_prepared_manifest", validate)
    preparation._write(directory, {"attempt_id": "a", "status": "queued", "run_id": run_id})
    return SimpleNamespace(directory=directory, db=db, run_id=run_id, prepared=prepared, pack=pack,
        mapper=mapper, apply_map=apply_map, legacy_scout=legacy_scout, steps=steps)


@pytest.mark.asyncio
async def test_capture_then_one_unified_map_supplies_inventory_and_ownership(mapped_upload):
    env = mapped_upload
    await preparation._prepare(env.directory, env.db, env.run_id, "a")
    state = preparation._read(env.directory)
    assert state["status"] == "succeeded", state
    assert env.steps == ["capture", "map", "apply", "validate"]
    env.mapper.assert_awaited_once()
    env.legacy_scout.assert_not_awaited()
    assert state["infopack"] == json.loads(env.pack.to_json())
    assert (state["captured"], state["checked"], state["verified"]) == (30, 30, 29)
    conn = sqlite3.connect(env.db)
    rows = conn.execute("SELECT statement_type, status FROM run_agents ORDER BY id").fetchall()
    conn.close()
    assert rows == [("SOURCE_PREPARATION", "succeeded"), ("SCOUT", "succeeded")]


@pytest.mark.asyncio
async def test_ready_extraction_reuses_unified_map_without_another_request(mapped_upload, monkeypatch):
    env = mapped_upload
    await preparation._prepare(env.directory, env.db, env.run_id, "a")
    restart = Mock(side_effect=AssertionError("Valid preparation must be reused"))
    monkeypatch.setattr(preparation, "start_preparation", restart)
    first = await real_ensure(env.directory, env.run_id)
    second = await real_ensure(env.directory, env.run_id)
    assert first["infopack"] == second["infopack"] == json.loads(env.pack.to_json())
    env.mapper.assert_awaited_once()
    restart.assert_not_called()
    env.legacy_scout.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_during_unified_map_preserves_capture_and_does_not_publish_ready(mapped_upload):
    env = mapped_upload
    entered = asyncio.Event()
    async def waiting_map(*args, **kwargs):
        entered.set()
        await asyncio.Future()
    env.mapper.side_effect = waiting_map
    worker = asyncio.create_task(preparation._prepare(env.directory, env.db, env.run_id, "a"))
    await asyncio.wait_for(entered.wait(), 2)
    assert preparation.task_registry.cancel_agent(env.directory.name, "source-preparation")
    await asyncio.wait_for(worker, 2)
    state = preparation._read(env.directory)
    assert state["status"] == state["scout_status"] == "cancelled"
    assert state["prepared"] is True
    assert "infopack" not in state
    env.apply_map.assert_not_awaited()
    assert preparation.task_registry.get_task(env.directory.name, "source-preparation") is None
    conn = sqlite3.connect(env.db)
    statuses = conn.execute("SELECT statement_type,status FROM run_agents ORDER BY id").fetchall()
    conn.close()
    assert statuses == [("SOURCE_PREPARATION", "succeeded"), ("SCOUT", "cancelled")]


@pytest.mark.asyncio
async def test_unified_map_failure_does_not_fall_back_to_second_paid_scout(mapped_upload):
    from ingest.document_preparation import PreparationError
    env = mapped_upload
    env.mapper.side_effect = PreparationError("Document map failed. Retry preparation.")
    await preparation._prepare(env.directory, env.db, env.run_id, "a")
    state = preparation._read(env.directory)
    assert state["status"] == "failed"
    assert state["prepared"] is True
    assert "infopack" not in state
    env.legacy_scout.assert_not_awaited()
    env.apply_map.assert_not_awaited()


@pytest.mark.asyncio
async def test_unified_map_usage_is_recorded_once_on_map_audit(mapped_upload):
    env = mapped_upload
    await preparation._prepare(env.directory, env.db, env.run_id, "a")
    conn = sqlite3.connect(env.db)
    metrics = dict(conn.execute("SELECT statement_type,total_tokens FROM run_agents"))
    conn.close()
    assert metrics == {"SOURCE_PREPARATION": 0, "SCOUT": 100}
    env.mapper.assert_awaited_once()


@pytest.mark.asyncio
async def test_cached_preparation_retry_retains_prior_paid_map_usage(mapped_upload):
    env = mapped_upload
    await preparation._prepare(env.directory, env.db, env.run_id, "a")
    env.mapper.side_effect = None
    env.mapper.return_value = (env.pack, [{"block_id": "p1b0", "owner_kind": "metadata"}])
    preparation._write(env.directory, {"attempt_id": "b", "status": "queued", "run_id": env.run_id})
    await preparation._prepare(env.directory, env.db, env.run_id, "b")
    conn = sqlite3.connect(env.db)
    rows = conn.execute("SELECT status,total_tokens,prompt_tokens,completion_tokens FROM run_agents WHERE statement_type='SCOUT'").fetchall()
    conn.close()
    assert rows == [("succeeded", 100, 70, 30)]
