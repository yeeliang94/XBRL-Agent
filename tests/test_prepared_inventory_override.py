"""Run-owned inventory edits preserve the upload ledger and paid audit totals."""
import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

import server
import api.preparation as preparation
from db import repository as repo
from db.schema import init_db
from ingest.document_preparation import PreparedDocument


def test_metadata_edits_do_not_require_paid_remapping():
    original = [{"note_num": 1, "title": "Old", "page_range": [1, 3], "subnotes": []}]
    current = [{**original[0], "title": "Corrected", "subnotes": [{"subnote_ref": "a"}]}]
    assert not preparation.inventory_requires_remap(original, current)
    assert preparation.inventory_requires_remap(original, [{**current[0], "page_range": [2, 4]}])
    assert preparation.inventory_requires_remap(original, [])


@pytest.fixture
def override_env(tmp_path, monkeypatch):
    import scout.prepared_map as mapper
    import ingest.document_preparation as capture
    db = tmp_path / "audit.db"
    init_db(db)
    conn = sqlite3.connect(db)
    run_id = repo.create_run(conn, "fixture.pdf", session_id="session", output_dir=str(tmp_path))
    conn.commit()
    conn.close()
    metadata = tmp_path / "preparation.json"
    metadata.write_text('{"revision":"r1","owner":"original"}')
    source = PreparedDocument(tmp_path / "source.html", metadata, tmp_path / "prepared.pdf", "r1", 1, {}, [], [])
    monkeypatch.setattr("pricing.estimate_cost", lambda *args, **kwargs: 0.25 if args[0] else 0.0)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.setattr(server, "_reload_runtime_settings", lambda: None)
    monkeypatch.setattr(server, "_resolve_api_key", lambda: "synthetic-key")
    monkeypatch.setattr(preparation, "_configuration", lambda: ("capture-model", "configured-scout", "cfg"))
    model = object()
    def create_model(name, *args):
        assert name == "configured-scout"
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT status FROM run_agents WHERE statement_type='SCOUT'").fetchone()
        conn.close()
        assert row == ("running",), "audit must precede model construction"
        return model
    monkeypatch.setattr(server, "_create_proxy_model", create_model)
    async def map_document(prepared, used_model, **kwargs):
        assert used_model is model
        kwargs["usage_out"].update(prompt_tokens=10, completion_tokens=5, total_tokens=15, turn_count=1)
        return None, [{"block_id": "b1"}]
    async def reconcile(prepared, **kwargs):
        assert prepared.metadata_path.name == f"preparation-run-{run_id}.json"
        assert prepared.source_html_path is source.source_html_path
        prepared.metadata_path.write_text('{"revision":"r1","owner":"edited"}')
        return prepared
    map_mock = AsyncMock(side_effect=map_document)
    monkeypatch.setattr(mapper, "build_prepared_document_map", map_mock)
    monkeypatch.setattr(capture, "reconcile_prepared_inventory", reconcile)
    return source, db, run_id, map_mock


@pytest.mark.asyncio
async def test_override_isolates_ledger_and_preserves_configured_scout_audit(override_env):
    source, db, run_id, mapper = override_env
    before = source.metadata_path.read_bytes()
    result = await preparation.remap_prepared_inventory(source, {"notes_inventory": []}, run_id=run_id, session_id="session")
    assert source.metadata_path.read_bytes() == before
    assert json.loads(result.metadata_path.read_text())["owner"] == "edited"
    mapper.assert_awaited_once()
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status,model,total_tokens FROM run_agents WHERE statement_type='SCOUT'").fetchone()
    conn.close()
    assert row == ("succeeded", "configured-scout", 15)
    assert preparation.task_registry.get_task("session", "scout") is None


@pytest.mark.asyncio
async def test_override_cancel_is_registered_and_audit_finishes(override_env):
    source, db, run_id, mapper = override_env
    entered = asyncio.Event()
    async def waiting(*args, **kwargs):
        kwargs["usage_out"].update(prompt_tokens=5, total_tokens=5)
        entered.set()
        await asyncio.Future()
    mapper.side_effect = waiting
    worker = asyncio.create_task(preparation.remap_prepared_inventory(source, {}, run_id=run_id, session_id="session"))
    await asyncio.wait_for(entered.wait(), 2)
    assert preparation.task_registry.cancel_agent("session", "scout")
    with pytest.raises(asyncio.CancelledError):
        await worker
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status,total_tokens FROM run_agents WHERE statement_type='SCOUT'").fetchone()
    conn.close()
    assert row == ("cancelled", 5)
    assert json.loads(source.metadata_path.read_text())["owner"] == "original"
    assert preparation.task_registry.get_task("session", "scout") is None


@pytest.mark.asyncio
async def test_cached_override_retry_retains_prior_paid_totals(override_env):
    source, db, run_id, mapper = override_env
    await preparation.remap_prepared_inventory(source, {}, run_id=run_id, session_id="session")
    mapper.side_effect = None
    mapper.return_value = (None, [])
    await preparation.remap_prepared_inventory(source, {}, run_id=run_id, session_id="session")
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT status,total_tokens,prompt_tokens,completion_tokens,turn_count,total_cost FROM run_agents WHERE statement_type='SCOUT'").fetchall()
    conn.close()
    assert rows == [("succeeded", 15, 10, 5, 1, 0.25)]


@pytest.mark.asyncio
async def test_override_model_failure_terminalizes_allocated_audit(override_env, monkeypatch):
    source, db, run_id, mapper = override_env
    def fail_model(*args):
        raise RuntimeError("construction failed")
    monkeypatch.setattr(server, "_create_proxy_model", fail_model)
    with pytest.raises(RuntimeError, match="construction failed"):
        await preparation.remap_prepared_inventory(source, {}, run_id=run_id, session_id="session")
    mapper.assert_not_awaited()
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status,total_tokens FROM run_agents WHERE statement_type='SCOUT'").fetchone()
    conn.close()
    assert row == ("failed", 0)
    assert preparation.task_registry.get_task("session", "scout") is None
