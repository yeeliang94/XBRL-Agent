"""Preparation outcomes are durable and independent of an attached browser."""
import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import server
import api.preparation as prep
from api.preparation import start_preparation as real_start, ensure_prepared as real_ensure
from db.schema import init_db
from db import repository as repo


@pytest.fixture
def upload(tmp_path, monkeypatch):
    directory = tmp_path / "1c1b622e-52d3-4c3f-b512-e3780101777d"
    directory.mkdir()
    (directory / "uploaded.pdf").write_bytes(b"original")
    db = tmp_path / "audit.db"
    init_db(db)
    conn = sqlite3.connect(db)
    run_id = repo.create_run(conn, pdf_filename="fixture.pdf", session_id=directory.name,
                             output_dir=str(directory), config=None, status="draft")
    conn.commit()
    conn.close()
    monkeypatch.setattr(prep.server, "AUDIT_DB_PATH", db)
    monkeypatch.setattr(prep.server, "OUTPUT_DIR", tmp_path)
    return directory, db, run_id


def test_interrupted_attempt_is_never_reported_complete(upload):
    directory, _, run_id = upload
    prep._write(directory, {"attempt_id": "old", "status": "working", "run_id": run_id})
    state = prep.snapshot(directory)
    assert state["status"] == "failed"
    assert state["error"] == "preparation_interrupted"
    assert json.loads((directory / "preparation_status.json").read_text())["status"] == "failed"


def test_status_write_uses_resilient_atomic_replace(upload, monkeypatch):
    directory, _, _ = upload
    calls = []

    def replace(source, destination):
        calls.append((Path(source), Path(destination)))
        Path(source).replace(destination)

    monkeypatch.setattr(prep, "replace_with_retry", replace)

    prep._write(directory, {"attempt_id": "a", "status": "working"})

    assert calls and calls[0][1] == directory / "preparation_status.json"
    assert prep._read(directory)["status"] == "working"


def test_snapshot_reports_interruption_when_terminal_status_cannot_persist(upload, monkeypatch):
    directory, _, _ = upload
    prep._write(directory, {"attempt_id": "a", "status": "working"})
    monkeypatch.setattr(prep, "_write", lambda *_: (_ for _ in ()).throw(PermissionError("locked")))

    state = prep.snapshot(directory)

    assert state["status"] == "failed"
    assert state["error"] == "preparation_interrupted"


def test_worker_contains_secondary_status_write_failure(upload, monkeypatch, caplog):
    directory, db, run_id = upload

    async def failed_prepare(*_args):
        raise RuntimeError("primary failure")

    monkeypatch.setattr(prep, "_prepare", failed_prepare)
    monkeypatch.setattr(prep, "_update", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("locked")))

    prep._worker(directory, db, run_id, "attempt")

    assert "Could not persist worker failure status" in caplog.text


def test_retry_after_restart_closes_previous_running_audit_without_status_poll(upload, monkeypatch):
    directory, db, run_id = upload
    with repo.db_session(db) as conn:
        agent_id = repo.create_run_agent(conn, run_id, statement_type="SOURCE_PREPARATION",
                                         variant=None, model="fake")
    prep._write(directory, {"attempt_id": "old", "status": "working", "run_id": run_id})
    monkeypatch.setattr(prep, "_worker", lambda *args: None)
    current = real_start(directory, run_id, retry=True)
    prep._workers[prep._key(directory)].join(3)
    prep._workers.pop(prep._key(directory), None)
    assert current["attempt_id"] != "old"
    with repo.db_session(db) as conn:
        assert conn.execute("SELECT status FROM run_agents WHERE id=?", (agent_id,)).fetchone()[0] == "failed"


def test_start_is_durable_and_duplicate_requests_join(upload, monkeypatch):
    directory, _, run_id = upload
    entered = threading.Event()
    release = threading.Event()
    def worker(path, db, rid, attempt):
        assert prep._read(path)["attempt_id"] == attempt
        entered.set()
        release.wait(3)
        prep._update(path, attempt, status="succeeded")
    monkeypatch.setattr(prep, "_worker", worker)
    first = real_start(directory, run_id)
    try:
        assert entered.wait(1)
        second = real_start(directory, run_id)
        assert first["attempt_id"] == second["attempt_id"]
        assert directory.name in prep.active_session_ids()
    finally:
        release.set()
        prep._workers[prep._key(directory)].join(3)
        prep._workers.pop(prep._key(directory), None)


def test_stale_attempt_cannot_replace_new_snapshot(upload):
    directory, _, _ = upload
    prep._write(directory, {"attempt_id": "new", "status": "working"})
    with pytest.raises(RuntimeError, match="superseded"):
        prep._update(directory, "old", status="succeeded")
    assert prep._read(directory)["status"] == "working"


def test_extraction_rejects_failed_preparation(upload):
    directory, _, run_id = upload
    prep._write(directory, {"attempt_id": "x", "status": "failed", "message": "Page 3 could not be verified"})
    with pytest.raises(RuntimeError, match="Page 3"):
        asyncio.run(real_ensure(directory, run_id))


def test_cancellation_cannot_publish_success(upload):
    directory, _, _ = upload
    prep._write(directory, {"attempt_id": "x", "status": "working", "cancel_requested": True})
    with pytest.raises(asyncio.CancelledError):
        prep._update(directory, "x", status="succeeded")


@pytest.mark.asyncio
async def test_worker_prepares_before_scout_and_reconciles_before_ready(upload, monkeypatch):
    directory, db, run_id = upload
    import ingest.document_preparation as capture
    import scout.prepared_map
    import notes.source_manifest
    calls = []
    prepared = SimpleNamespace(metadata_path=directory / "preparation.json",
                               prepared_pdf_path=directory / "prepared.pdf", revision="r1")
    async def fake_prepare(*args, **kwargs):
        calls.append("capture")
        kwargs["on_progress"]({"stage": "verifying", "captured": 2, "verified": 1, "checked": 2, "total": 2})
        return prepared
    async def fake_map(source, model, **kwargs):
        assert source is prepared
        calls.append("map")
        return SimpleNamespace(degraded=False, notes_inventory=[], to_json=lambda: '{"notes_inventory": []}'), []
    async def reconcile(*args, **kwargs):
        assert kwargs["assignments"] == []
        calls.append("reconcile")
        return prepared
    monkeypatch.setattr(capture, "prepare_document", fake_prepare)
    monkeypatch.setattr(capture, "reconcile_prepared_inventory", reconcile, raising=False)
    monkeypatch.setattr(scout.prepared_map, "build_prepared_document_map", fake_map)
    monkeypatch.setattr(notes.source_manifest, "build_prepared_manifest", lambda *a, **k: calls.append("validate"))
    monkeypatch.setattr(prep.server, "_reload_runtime_settings", lambda: None)
    monkeypatch.setattr(prep.server, "_resolve_api_key", lambda: "test-key")
    monkeypatch.setattr(prep.server, "_create_proxy_model", lambda *a: object())
    monkeypatch.setattr(prep.server, "_configured_default_models", lambda: {})
    prep._write(directory, {"attempt_id": "a", "status": "queued", "run_id": run_id})
    await prep._prepare(directory, db, run_id, "a")
    assert calls == ["capture", "map", "reconcile", "validate"]
    state = prep._read(directory)
    assert state["status"] == state["scout_status"] == "succeeded"
    assert state["prepared"] is True
    assert state["verified"] == 1
    assert state["checked"] == 2
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT status FROM run_agents WHERE run_id=?", (run_id,)).fetchall() == [("succeeded",), ("succeeded",)]
    conn.close()


@pytest.mark.asyncio
async def test_invalid_scout_map_keeps_actionable_failure_in_preparation_status(upload, monkeypatch):
    directory, db, run_id = upload
    import ingest.document_preparation as capture
    import scout.prepared_map
    from scout.prepared_map import DocumentMapPreparationError

    prepared = SimpleNamespace(
        metadata_path=directory / "preparation.json",
        prepared_pdf_path=directory / "prepared.pdf",
        revision="r1",
    )

    async def fake_prepare(*args, **kwargs):
        return prepared

    async def invalid_map(*args, **kwargs):
        raise DocumentMapPreparationError(
            "The AI service returned an invalid document map after 3 attempts. "
            "Retry document preparation."
        )

    monkeypatch.setattr(capture, "prepare_document", fake_prepare)
    monkeypatch.setattr(scout.prepared_map, "build_prepared_document_map", invalid_map)
    monkeypatch.setattr(prep.server, "_reload_runtime_settings", lambda: None)
    monkeypatch.setattr(prep.server, "_resolve_api_key", lambda: "test-key")
    monkeypatch.setattr(prep.server, "_create_proxy_model", lambda *a: object())
    monkeypatch.setattr(prep.server, "_configured_default_models", lambda: {})
    prep._write(directory, {"attempt_id": "a", "status": "queued", "run_id": run_id})

    await prep._prepare(directory, db, run_id, "a")

    state = prep._read(directory)
    assert state["status"] == state["scout_status"] == "failed"
    assert state["error"] == "DocumentMapPreparationError"
    assert state["message"].startswith(
        "The AI service returned an invalid document map after 3 attempts."
    )


@pytest.mark.asyncio
async def test_missing_credentials_has_one_actionable_failure_and_terminal_audit(upload, monkeypatch):
    directory, db, run_id = upload
    monkeypatch.setattr(prep.server, "_reload_runtime_settings", lambda: None)
    monkeypatch.setattr(prep.server, "_resolve_api_key", lambda: "")
    prep._write(directory, {"attempt_id": "a", "status": "queued", "run_id": run_id})
    await prep._prepare(directory, db, run_id, "a")
    state = prep._read(directory)
    assert state["status"] == "failed"
    assert "API key" in state["message"]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT status FROM run_agents").fetchall() == [("failed",)]
    conn.close()
