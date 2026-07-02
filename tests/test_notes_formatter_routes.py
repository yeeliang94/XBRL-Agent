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
        # The launch endpoint only formats finished runs (lifecycle interlock).
        repo.mark_run_finished(conn, run_id, "completed")

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


def test_notes_formatter_launch_refused_on_non_terminal_run(formatter_client):
    """Formatting is post-extraction review tooling — a draft/running run 409s."""
    client, _run_id, server_module = formatter_client
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        running_id = repo.create_run(
            conn, "other.pdf", session_id="s2", output_dir="",
            config={"notes_to_run": ["list_of_notes"]},
        )
    r = client.post(
        f"/api/runs/{running_id}/notes-format",
        json={"sheet": "Notes-Listofnotes"},
    )
    assert r.status_code == 409
    assert "finished" in r.json()["detail"]


def test_notes_formatter_launch_refused_while_notes_reviewer_running(formatter_client):
    """Interlock: the formatter must not start over a running reviewer pass."""
    client, run_id, server_module = formatter_client
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.claim_notes_review_task(conn, run_id, model="m")
    r = client.post(
        f"/api/runs/{run_id}/notes-format",
        json={"sheet": "Notes-Listofnotes"},
    )
    assert r.status_code == 409
    assert "reviewer" in r.json()["detail"]


def test_notes_reviewer_launch_refused_while_formatter_running(formatter_client):
    """Mirror interlock: the reviewer must not start over a running formatter."""
    client, run_id, server_module = formatter_client
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.claim_notes_format_task(
            conn, run_id, "Notes-Listofnotes", model="m",
        )
    r = client.post(f"/api/runs/{run_id}/notes-review/re-review", json={})
    assert r.status_code == 409
    assert "formatter" in r.json()["detail"]


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
    assert done["error_type"] == "turn_budget"
    assert "turn budget" in (done["error"] or "")
    assert "turn budget" in (done["summary"] or "")
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert cells[0].html == "<p>abc</p>"


def test_notes_formatter_timeout_records_error_type(formatter_client, monkeypatch):
    """A pass that outlives the wall-clock cap lands as error_type='timeout'."""
    import asyncio as aio

    client, run_id, server_module = formatter_client
    monkeypatch.setattr(server_module, "NOTES_FORMATTER_WALLCLOCK_TIMEOUT", 0.05)

    async def slow_run_notes_formatter(**_kwargs):
        await aio.sleep(5)
        return {"ok": True}

    import notes.formatting_agent as formatting_agent
    monkeypatch.setattr(
        formatting_agent, "run_notes_formatter", slow_run_notes_formatter,
    )
    r = client.post(
        f"/api/runs/{run_id}/notes-format",
        json={"sheet": "Notes-Listofnotes"},
    )
    assert r.status_code == 200
    done = _poll_done(client, run_id, "Notes-Listofnotes")
    assert done["error_type"] == "timeout"
    assert "timed out" in (done["error"] or "")


def test_notes_formatter_revert_restores_pre_format_html(formatter_client):
    """Revert restores the v27 snapshot into notes_cells and marks the task
    'reverted'; rows deleted since the pass are left alone."""
    client, run_id, server_module = formatter_client
    sheet = "Notes-Listofnotes"
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        # Emulate a completed pass: snapshot of the pre-format HTML, styled
        # HTML written over it, task row 'done'.
        repo.save_notes_format_snapshots(conn, run_id, sheet, {112: "<p>abc</p>"})
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=sheet, row=112,
            label="Disclosure of other notes",
            html='<table><tr><td style="text-align: right">abc</td></tr></table>',
            evidence="Page 3", source_pages=[3],
        )
        repo.upsert_notes_format_task(
            conn, run_id, sheet, "done", model="m", summary="Formatted.",
            confidence=0.9, changed_rows=1, result={"ok": True},
        )

    status = client.get(
        f"/api/runs/{run_id}/notes-format/status", params={"sheet": sheet},
    ).json()
    assert status["can_revert"] is True

    r = client.post(
        f"/api/runs/{run_id}/notes-format/revert", json={"sheet": sheet},
    )
    assert r.status_code == 200
    assert r.json()["restored_rows"] == 1

    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert cells[0].html == "<p>abc</p>"

    status = client.get(
        f"/api/runs/{run_id}/notes-format/status", params={"sheet": sheet},
    ).json()
    assert status["status"] == "done"
    assert status["error_type"] == "reverted"
    assert status["error"] is None


def test_notes_formatter_trace_endpoint_serves_and_guards(formatter_client):
    """The trace endpoint serves the on-disk JSON, 400s an unknown sheet
    (which also blocks traversal via the query param), 404s a missing file."""
    client, run_id, server_module = formatter_client
    sheet = "Notes-Listofnotes"

    r = client.get(
        f"/api/runs/{run_id}/notes-format/trace", params={"sheet": sheet},
    )
    assert r.status_code == 404  # no trace captured yet

    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run = repo.fetch_run(conn, run_id)
    trace_path = (
        Path(run.output_dir) / f"notes_format_{sheet}_conversation_trace.json"
    )
    trace_path.write_text('{"messages": [{"raw": "hi"}]}', encoding="utf-8")

    r = client.get(
        f"/api/runs/{run_id}/notes-format/trace", params={"sheet": sheet},
    )
    assert r.status_code == 200
    assert r.json()["messages"] == [{"raw": "hi"}]

    r = client.get(
        f"/api/runs/{run_id}/notes-format/trace",
        params={"sheet": "../../etc/passwd"},
    )
    assert r.status_code == 400


def test_notes_formatter_revert_without_snapshot_404s(formatter_client):
    client, run_id, _server = formatter_client
    r = client.post(
        f"/api/runs/{run_id}/notes-format/revert",
        json={"sheet": "Notes-Listofnotes"},
    )
    assert r.status_code == 404


def test_notes_formatter_revert_while_running_409s(formatter_client):
    client, run_id, server_module = formatter_client
    sheet = "Notes-Listofnotes"
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.save_notes_format_snapshots(conn, run_id, sheet, {112: "<p>abc</p>"})
        repo.claim_notes_format_task(conn, run_id, sheet, model="m")
    r = client.post(
        f"/api/runs/{run_id}/notes-format/revert", json={"sheet": sheet},
    )
    assert r.status_code == 409
