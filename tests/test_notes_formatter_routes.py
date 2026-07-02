from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db import repository as repo
from db.schema import init_db


@pytest.fixture()
def formatter_client(tmp_path: Path, monkeypatch):
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    init_db(server_module.AUDIT_DB_PATH)
    from concept_model.bootstrap import import_all_notes_templates
    import_all_notes_templates(server_module.AUDIT_DB_PATH)

    out = tmp_path / "sess"
    out.mkdir()
    (out / "uploaded.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "sample.pdf", session_id="sess", output_dir=str(out),
            config={"notes_to_run": ["list_of_notes"], "model": "m"},
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-Listofnotes", row=112,
            label="Disclosure of other notes", html="<p>abc</p>",
            evidence="Page 3", source_pages=[3],
        )

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(server_module, "_create_proxy_model", lambda *a, **k: "fake-model")
    return TestClient(server_module.app), run_id, server_module


def _poll_done(client: TestClient, run_id: int, sheet: str) -> dict:
    for _ in range(20):
        r = client.get(
            f"/api/runs/{run_id}/notes-format/status",
            params={"sheet": sheet},
        )
        assert r.status_code == 200
        body = r.json()
        if body["status"] == "done":
            return body
        time.sleep(0.05)
    raise AssertionError("formatter task did not finish")


def test_notes_formatter_status_idle(formatter_client):
    client, run_id, _server = formatter_client
    r = client.get(
        f"/api/runs/{run_id}/notes-format/status",
        params={"sheet": "Notes-Listofnotes"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "idle"


def test_notes_formatter_rejects_numeric_sheet(formatter_client):
    client, run_id, _server = formatter_client
    r = client.post(
        f"/api/runs/{run_id}/notes-format",
        json={"sheet": "Notes-Issuedcapital"},
    )
    assert r.status_code == 422


def test_notes_formatter_reports_already_running(formatter_client):
    client, run_id, server_module = formatter_client
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.claim_notes_format_task(
            conn, run_id, "Notes-Listofnotes", model="m",
        )
    r = client.post(
        f"/api/runs/{run_id}/notes-format",
        json={"sheet": "Notes-Listofnotes"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running"
    assert body["already_running"] is True


def test_notes_formatter_validation_failure_records_done(formatter_client, monkeypatch):
    client, run_id, server_module = formatter_client

    async def fake_run_notes_formatter(**_kwargs):
        return {
            "ok": False,
            "error": "row 112: rendered text changed",
            "summary": "Rejected unsafe patch.",
            "confidence": 0.9,
            "changed_rows": 0,
        }

    import notes.formatting_agent as formatting_agent
    monkeypatch.setattr(
        formatting_agent, "run_notes_formatter", fake_run_notes_formatter,
    )
    r = client.post(
        f"/api/runs/{run_id}/notes-format",
        json={"sheet": "Notes-Listofnotes"},
    )
    assert r.status_code == 200
    done = _poll_done(client, run_id, "Notes-Listofnotes")
    assert done["status"] == "done"
    assert done["error"] == "row 112: rendered text changed"
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert cells[0].html == "<p>abc</p>"


def test_notes_formatter_turn_budget_records_done(formatter_client, monkeypatch):
    """A UsageLimitExceeded from the pass persists a structured 'turn budget'
    outcome and writes no cells (pins the _thread_main except branch)."""
    client, run_id, server_module = formatter_client

    from pydantic_ai.exceptions import UsageLimitExceeded

    async def fake_run_notes_formatter(**_kwargs):
        raise UsageLimitExceeded(
            "The next request would exceed the request_limit of 16"
        )

    import notes.formatting_agent as formatting_agent
    monkeypatch.setattr(
        formatting_agent, "run_notes_formatter", fake_run_notes_formatter,
    )
    r = client.post(
        f"/api/runs/{run_id}/notes-format",
        json={"sheet": "Notes-Listofnotes"},
    )
    assert r.status_code == 200
    done = _poll_done(client, run_id, "Notes-Listofnotes")
    assert done["status"] == "done"
    assert "turn budget" in (done["error"] or "")
    assert "turn budget" in (done["summary"] or "")
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert cells[0].html == "<p>abc</p>"
