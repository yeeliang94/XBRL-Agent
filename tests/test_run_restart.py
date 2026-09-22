"""Full-run restart: isolated draft plus safe preparation reuse."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

import server
import api.preparation as preparation
import recovery.run_restart as restart
from db import repository as repo
from db.schema import init_db


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_reusable_preparation(source_dir, *, model="model", config="config-key"):
    uploaded = source_dir / "uploaded.pdf"
    prepared_html = source_dir / "prepared-revision.html"
    prepared_pdf = source_dir / "prepared-revision.pdf"
    prepared_html.write_text("<p>prepared</p>", encoding="utf-8")
    prepared_pdf.write_bytes(b"prepared pdf")
    (source_dir / "source.html").write_text("<p>source</p>", encoding="utf-8")
    (source_dir / "source_meta.json").write_text(
        '{"origin":"llm_transcription"}', encoding="utf-8",
    )
    metadata = {
        "status": "succeeded",
        "inventory_reconciled": True,
        "source_sha256": _sha256(uploaded),
        "contract_version": 6,
        "model_name": model,
        "configuration_key": config,
        "revision": "revision",
        "pages": [{"page": 1, "rotation": 0, "verified": True}],
        "blocks": [],
        "html_file": prepared_html.name,
        "pdf_file": prepared_pdf.name,
        "html_sha256": _sha256(prepared_html),
        "pdf_sha256": _sha256(prepared_pdf),
    }
    (source_dir / "preparation.json").write_text(
        json.dumps(metadata), encoding="utf-8",
    )
    (source_dir / "preparation_status.json").write_text(
        json.dumps({
            "status": "succeeded",
            "infopack": {"entity_name": "Example Berhad"},
        }),
        encoding="utf-8",
    )


def _parent(tmp_path, monkeypatch, *, status="completed"):
    output_root = tmp_path / "output"
    output_root.mkdir()
    db_path = output_root / "audit.db"
    init_db(db_path)
    source_dir = output_root / "parent-session"
    source_dir.mkdir()
    (source_dir / "uploaded.pdf").write_bytes(b"same source")
    (source_dir / "original_filename.txt").write_text("Accounts.pdf", encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        run_id = repo.create_run(
            conn,
            pdf_filename="Accounts.pdf",
            session_id="parent-session",
            output_dir=str(source_dir),
            config={
                "statements": ["SOFP", "SOPL"],
                "variants": {"SOFP": "CuNonCu"},
                "filing_level": "company",
                "filing_standard": "mfrs",
            },
            status=status,
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db_path)
    monkeypatch.setattr(server, "_reload_runtime_settings", lambda: None)
    monkeypatch.setattr(
        preparation, "_configuration", lambda: ("model", "scout", "config-key")
    )
    return TestClient(server.app), run_id, source_dir, db_path


def test_restart_creates_isolated_draft_and_reuses_preparation(tmp_path, monkeypatch):
    client, parent_id, source_dir, db_path = _parent(tmp_path, monkeypatch)
    prepared_html = source_dir / "prepared-revision.html"
    prepared_pdf = source_dir / "prepared-revision.pdf"
    metadata = source_dir / "preparation.json"
    prepared_html.write_text("<p>prepared</p>", encoding="utf-8")
    prepared_pdf.write_bytes(b"prepared pdf")
    metadata.write_text('{"inventory_reconciled":true}', encoding="utf-8")
    status = {
        "status": "succeeded",
        "stage": "ready",
        "infopack": {"entity_name": "Example Berhad"},
        "run_id": parent_id,
    }
    monkeypatch.setattr(
        restart,
        "_read_reusable_preparation",
        lambda *_args, **_kwargs: (
            status,
            [metadata, prepared_html, prepared_pdf],
        ),
    )
    monkeypatch.setattr(restart, "read_prepared_document", lambda *_a, **_k: object())

    response = client.post(f"/api/runs/{parent_id}/restart")

    assert response.status_code == 200
    payload = response.json()
    assert payload["preparation_reused"] is True
    assert payload["run_id"] != parent_id
    target = server.OUTPUT_DIR / payload["session_id"]
    assert (target / "uploaded.pdf").read_bytes() == b"same source"
    assert (target / "prepared-revision.html").is_file()
    assert payload["preparation"]["run_id"] == payload["run_id"]
    assert payload["preparation"]["reuse_from_run_id"] == parent_id

    conn = sqlite3.connect(db_path)
    try:
        child = repo.fetch_run(conn, payload["run_id"])
        lineage = conn.execute(
            "SELECT parent_run_id FROM run_lineage WHERE child_run_id=?",
            (payload["run_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert child is not None and child.status == "draft"
    assert child.session_id != "parent-session"
    assert child.config["infopack"]["entity_name"] == "Example Berhad"
    assert lineage == (parent_id,)


def test_reusable_preparation_is_validated_without_stubbing(tmp_path, monkeypatch):
    _client, _parent_id, source_dir, _db_path = _parent(tmp_path, monkeypatch)
    _write_reusable_preparation(source_dir)

    reusable = restart._read_reusable_preparation(
        source_dir, model_name="model", configuration_key="config-key",
    )

    assert reusable is not None
    status, files = reusable
    assert status["infopack"]["entity_name"] == "Example Berhad"
    assert {path.name for path in files} >= {
        "preparation.json",
        "source.html",
        "source_meta.json",
        "prepared-revision.html",
        "prepared-revision.pdf",
    }


def test_reusable_preparation_requires_source_metadata(tmp_path, monkeypatch):
    _client, _parent_id, source_dir, _db_path = _parent(tmp_path, monkeypatch)
    _write_reusable_preparation(source_dir)
    (source_dir / "source_meta.json").unlink()

    assert restart._read_reusable_preparation(
        source_dir, model_name="model", configuration_key="config-key",
    ) is None


@pytest.mark.parametrize(
    "invalid_case",
    [
        "failed_status",
        "missing_infopack",
        "unreconciled_inventory",
        "missing_source_html",
        "changed_source",
        "changed_configuration",
    ],
)
def test_reusable_preparation_rejects_incomplete_or_stale_bundles(
    tmp_path, monkeypatch, invalid_case,
):
    _client, _parent_id, source_dir, _db_path = _parent(tmp_path, monkeypatch)
    _write_reusable_preparation(source_dir)
    model = "model"
    configuration_key = "config-key"

    if invalid_case in {"failed_status", "missing_infopack"}:
        path = source_dir / "preparation_status.json"
        status = json.loads(path.read_text(encoding="utf-8"))
        if invalid_case == "failed_status":
            status["status"] = "failed"
        else:
            status.pop("infopack")
        path.write_text(json.dumps(status), encoding="utf-8")
    elif invalid_case == "unreconciled_inventory":
        path = source_dir / "preparation.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["inventory_reconciled"] = False
        path.write_text(json.dumps(metadata), encoding="utf-8")
    elif invalid_case == "missing_source_html":
        (source_dir / "source.html").unlink()
    elif invalid_case == "changed_source":
        (source_dir / "uploaded.pdf").write_bytes(b"changed source")
    elif invalid_case == "changed_configuration":
        configuration_key = "different-config"

    assert restart._read_reusable_preparation(
        source_dir,
        model_name=model,
        configuration_key=configuration_key,
    ) is None


def test_restart_clone_runs_off_the_endpoint_event_loop(tmp_path, monkeypatch):
    client, parent_id, _source_dir, _db_path = _parent(tmp_path, monkeypatch)
    calls = {}

    def configuration():
        calls["endpoint_thread"] = threading.get_ident()
        return "model", "scout", "config-key"

    def clone(*_args, **_kwargs):
        calls["clone_thread"] = threading.get_ident()
        return restart.RestartDraft(
            run_id=parent_id + 1,
            session_id="child-session",
            preparation_reused=True,
            preparation={"status": "succeeded"},
        )

    monkeypatch.setattr(preparation, "_configuration", configuration)
    monkeypatch.setattr(restart, "clone_run_as_draft", clone)

    response = client.post(f"/api/runs/{parent_id}/restart")

    assert response.status_code == 200
    assert calls["clone_thread"] != calls["endpoint_thread"]


def test_restart_falls_back_to_normal_preparation_when_reuse_is_unavailable(
    tmp_path, monkeypatch,
):
    client, parent_id, _source_dir, _db_path = _parent(tmp_path, monkeypatch)
    monkeypatch.setattr(
        restart, "_read_reusable_preparation", lambda *_args, **_kwargs: None
    )
    started = []

    def fake_start(directory, run_id):
        started.append((directory, run_id))
        return {"status": "queued", "run_id": run_id}

    monkeypatch.setattr(preparation, "start_preparation", fake_start)

    response = client.post(f"/api/runs/{parent_id}/restart")

    assert response.status_code == 200
    payload = response.json()
    assert payload["preparation_reused"] is False
    assert payload["preparation"]["status"] == "queued"
    assert started == [(server.OUTPUT_DIR / payload["session_id"], payload["run_id"])]


def test_restart_rejects_a_running_parent(tmp_path, monkeypatch):
    client, parent_id, _source_dir, _db_path = _parent(
        tmp_path, monkeypatch, status="running"
    )

    response = client.post(f"/api/runs/{parent_id}/restart")

    assert response.status_code == 409
    assert "still running" in response.json()["detail"]


def test_parent_can_be_deleted_without_deleting_restarted_child(tmp_path, monkeypatch):
    client, parent_id, _source_dir, db_path = _parent(tmp_path, monkeypatch)
    monkeypatch.setattr(
        restart, "_read_reusable_preparation", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        preparation,
        "start_preparation",
        lambda _directory, run_id: {"status": "queued", "run_id": run_id},
    )
    child_id = client.post(f"/api/runs/{parent_id}/restart").json()["run_id"]

    response = client.delete(f"/api/runs/{parent_id}")

    assert response.status_code == 200
    conn = sqlite3.connect(db_path)
    try:
        assert repo.fetch_run(conn, parent_id) is None
        assert repo.fetch_run(conn, child_id) is not None
        assert conn.execute(
            "SELECT count(*) FROM run_lineage WHERE child_run_id=?", (child_id,)
        ).fetchone()[0] == 0
    finally:
        conn.close()
