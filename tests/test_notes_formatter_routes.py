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

    # The application uses GOOGLE_API_KEY for its enterprise proxy. Setting
    # OPENAI_API_KEY made this fixture depend on a developer .env in serial
    # runs and fail in clean xdist workers before reaching the route behavior.
    monkeypatch.setenv("GOOGLE_API_KEY", "sk-test")
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


def test_notes_formatter_rejects_numeric_sheet_without_html(formatter_client):
    client, run_id, _server = formatter_client
    r = client.post(
        f"/api/runs/{run_id}/notes-format",
        json={"sheet": "Notes-Issuedcapital"},
    )
    assert r.status_code == 422


@pytest.mark.parametrize("sheet", ["Notes-Issuedcapital", "Notes-RelatedPartytran"])
def test_notes_formatter_accepts_canonical_html_on_mixed_sheet(formatter_client, monkeypatch, sheet):
    from concept_model.filing_targets import resolve_writable_html_target
    import notes.formatting_agent as fa

    client, run_id, server_module = formatter_client
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        target = resolve_writable_html_target(conn, family_prefix="mfrs-company-", sheet=sheet, row=4)
        assert target is not None
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=sheet, row=4,
            label=target["label"], concept_uuid=target["concept_uuid"],
            html="<p>Disclosure</p>", source_pages=[1], style_source="unstyled")
    calls = []

    async def formatter(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "changed_rows": 1}

    monkeypatch.setattr(fa, "run_notes_formatter", formatter)
    response = client.post(f"/api/runs/{run_id}/notes-format", json={"sheet": sheet})
    assert response.status_code == 200
    assert _poll_done(client, run_id, sheet)["changed_rows"] == 1
    assert calls[0]["sheet"] == sheet


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


def test_running_status_exposes_visual_phase(formatter_client):
    client, run_id, server_module = formatter_client
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.upsert_notes_format_task(conn, run_id, "Notes-Listofnotes", status="running",
                                     model="m", summary="Rechecking corrected notes against the source PDF…")
    response = client.get(f"/api/runs/{run_id}/notes-format/status", params={"sheet": "Notes-Listofnotes"})
    assert response.json()["summary"] == "Rechecking corrected notes against the source PDF…"


@pytest.mark.parametrize("change", ["content", "source_pages", "appearance"])
def test_formatter_status_invalidates_changed_verified_output(formatter_client, change):
    import json
    from notes.visual_review import note_verification_identity
    from notes.formatting_agent import _resolve_notes_table_theme
    from mtool.notes_decorate import NotesTableStyle
    client, run_id, server_module = formatter_client
    sheet = "Notes-Listofnotes"
    html = "<table><tr><td>Total</td><td>10</td></tr></table>"
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=sheet, row=112,
            label="Disclosure", html=html, source_pages=[3])
    style = NotesTableStyle.from_theme(_resolve_notes_table_theme(str(server_module.AUDIT_DB_PATH), run_id))
    receipt = note_verification_identity(html, style, [3])
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.upsert_notes_format_task(conn, run_id, sheet, "done",
            result={"ok": True, "verified_rows": {112: receipt}})
    url = f"/api/runs/{run_id}/notes-format/status"
    assert client.get(url, params={"sheet": sheet}).json()["result"]["ok"]
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        if change == "appearance":
            conn.execute("UPDATE runs SET notes_table_style=? WHERE id=?",
                (json.dumps({"borderStyle": "all", "borderColor": "#ff0000"}), run_id))
        else:
            repo.upsert_notes_cell(conn, run_id=run_id, sheet=sheet, row=112, label="Disclosure",
                html=html.replace("10", "20") if change == "content" else html,
                source_pages=[4] if change == "source_pages" else None)
    status = client.get(url, params={"sheet": sheet}).json()
    assert status["error_type"] == "verification_stale"
    assert status["failed_rows"] == [112]
    assert not status["result"]["ok"]
    assert "Recheck" in status["error"]


def test_retry_checks_stale_and_unfinished_notes_and_keeps_other_receipts(formatter_client, monkeypatch):
    from notes.visual_review import note_verification_identity
    from notes.formatting_agent import _resolve_notes_table_theme
    from mtool.notes_decorate import NotesTableStyle
    client, run_id, server_module = formatter_client
    sheet = "Notes-Listofnotes"
    html = "<p>Original disclosure</p>"
    style = NotesTableStyle.from_theme(_resolve_notes_table_theme(str(server_module.AUDIT_DB_PATH), run_id))
    receipt = note_verification_identity(html, style, [3])
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        for row in (112, 113, 114):
            repo.upsert_notes_cell(conn, run_id=run_id, sheet=sheet, row=row,
                label="Disclosure", html=html, source_pages=[3],
                style_source="unstyled" if row == 113 else "formatter")
        repo.upsert_notes_format_task(conn, run_id, sheet, "done",
            result={"ok": True, "verified_rows": {112: receipt, 114: receipt}})
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=sheet, row=112,
            label="Disclosure", html="<p>Edited disclosure</p>")
    async def fake_formatter(**kwargs):
        assert kwargs["rows"] == [112, 113]
        with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
            receipts = {cell.row: note_verification_identity(cell.html, style, cell.source_pages)
                for cell in repo.list_formatter_cells_for_run(conn, run_id) if cell.row in kwargs["rows"]}
        return {"ok": True, "verified_rows": receipts, "changed_rows": 0}
    monkeypatch.setattr("notes.formatting_agent.run_notes_formatter", fake_formatter)
    assert client.post(f"/api/runs/{run_id}/notes-format", json={"sheet": sheet}).status_code == 200
    status = _poll_done(client, run_id, sheet)
    assert status["result"]["ok"]
    assert set(status["result"]["verified_rows"]) == {"112", "113", "114"}


def test_notes_formatter_reports_already_formatted_sheet(formatter_client):
    """A retry with no unfinished rows must not claim the sheet is empty."""
    client, run_id, server_module = formatter_client
    sheet = "Notes-Listofnotes"
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=sheet, row=112,
            label="Disclosure of other notes", html="<p>abc</p>",
            evidence="Page 3", source_pages=[3], style_source="formatter",
        )

    response = client.post(
        f"/api/runs/{run_id}/notes-format", json={"sheet": sheet},
    )
    assert response.status_code == 200
    done = _poll_done(client, run_id, sheet)
    assert done["status"] == "done"
    assert done["error_type"] == "no_unfinished_rows"
    assert "already formatted" in done["error"].lower()


def test_notes_formatter_validation_failure_records_done(formatter_client, monkeypatch):
    client, run_id, server_module = formatter_client
    seen_sources = []
    from notes.auto_format import PDF_FORMAT_CANDIDATE_SOURCES

    async def fake_run_notes_formatter(**kwargs):
        seen_sources.append(kwargs["style_sources"])
        return {
            "ok": False,
            "error": "row 112: rendered text changed",
            "summary": "Rejected unsafe patch.",
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
    assert seen_sources == [PDF_FORMAT_CANDIDATE_SOURCES | {None}]
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
    # A realistic style-only pair: same text + geometry, styling added.
    pre_format = "<table><tr><td>abc</td></tr></table>"
    styled = '<table><tr><td style="text-align: right">abc</td></tr></table>'
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        # Emulate a completed pass: snapshot of the pre-format HTML, styled
        # HTML written over it, task row 'done'.
        repo.save_notes_format_snapshots(conn, run_id, sheet, {112: pre_format})
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=sheet, row=112,
            label="Disclosure of other notes",
            html=styled, evidence="Page 3", source_pages=[3],
            style_source="formatter",
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
    assert r.json()["skipped_rows"] == []

    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert cells[0].html == pre_format
    assert cells[0].style_source == "unstyled"

    from notes.auto_format import candidate_sheets
    assert candidate_sheets(str(server_module.AUDIT_DB_PATH), run_id, [sheet]) == [sheet]

    status = client.get(
        f"/api/runs/{run_id}/notes-format/status", params={"sheet": sheet},
    ).json()
    assert status["status"] == "done"
    assert status["error_type"] == "reverted"
    assert status["error"] is None


def test_notes_formatter_token_totals_round_trip(formatter_client, monkeypatch):
    """Token telemetry returned by the pass persists onto the v27 task row
    and comes back through the status endpoint (Step 8 Verify)."""
    client, run_id, server_module = formatter_client

    async def fake_run_notes_formatter(**_kwargs):
        return {
            "ok": True, "summary": "Formatted.",
            "changed_rows": 1, "skipped_rows": [],
            "prompt_tokens": 1200, "completion_tokens": 345,
            "cache_read_tokens": 800, "cache_write_tokens": 50,
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
    assert done["prompt_tokens"] == 1200
    assert done["completion_tokens"] == 345
    assert done["cache_read_tokens"] == 800
    assert done["cache_write_tokens"] == 50


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


def test_notes_formatter_revert_keeps_content_edited_after_formatting(formatter_client):
    """A row whose CONTENT the user edited after the formatter pass is kept
    on revert — restoring the snapshot would clobber the newer edit."""
    client, run_id, server_module = formatter_client
    sheet = "Notes-Listofnotes"
    edited = "<p>User rewrote this note entirely after formatting.</p>"
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.save_notes_format_snapshots(conn, run_id, sheet, {112: "<p>abc</p>"})
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=sheet, row=112,
            label="Disclosure of other notes", html=edited,
            evidence="Page 3", source_pages=[3],
            style_source="formatter",
        )
        repo.upsert_notes_format_task(
            conn, run_id, sheet, "done", model="m", summary="Formatted.",
            confidence=0.9, changed_rows=1, result={"ok": True},
        )
    r = client.post(
        f"/api/runs/{run_id}/notes-format/revert", json={"sheet": sheet},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["restored_rows"] == 0
    assert body["skipped_rows"] == [112]
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert cells[0].html == edited
    assert cells[0].style_source == "unstyled"


def test_guarded_claims_are_mutually_exclusive(formatter_client):
    """The cross-table interlock is atomic: check-other + claim-mine happen
    inside one BEGIN IMMEDIATE transaction in the repo helpers."""
    client, run_id, server_module = formatter_client
    sheet = "Notes-Listofnotes"
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        assert repo.claim_notes_format_task_guarded(
            conn, run_id, sheet, model="m",
        ) == "claimed"
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        assert repo.claim_notes_review_task_guarded(
            conn, run_id, model="m",
        ) == "formatter_running"
        assert repo.claim_notes_format_task_guarded(
            conn, run_id, sheet, model="m",
        ) == "format_running"
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.upsert_notes_format_task(conn, run_id, sheet, "done", model="m")
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        assert repo.claim_notes_review_task_guarded(
            conn, run_id, model="m",
        ) == "claimed"
        assert repo.claim_notes_format_task_guarded(
            conn, run_id, sheet, model="m",
        ) == "reviewer_running"


def test_notes_formatter_revert_refused_while_notes_reviewer_running(formatter_client):
    """Revert carries the same reviewer interlock as launch — both write the
    sheet's prose rows."""
    client, run_id, server_module = formatter_client
    sheet = "Notes-Listofnotes"
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.save_notes_format_snapshots(conn, run_id, sheet, {112: "<p>abc</p>"})
        repo.claim_notes_review_task(conn, run_id, model="m")
    r = client.post(
        f"/api/runs/{run_id}/notes-format/revert", json={"sheet": sheet},
    )
    assert r.status_code == 409
    assert "reviewer" in r.json()["detail"]


def test_notes_formatter_partial_status_exposes_failed_rows(formatter_client, monkeypatch):
    client, run_id, _ = formatter_client
    summary = "Formatting saved for 1 row(s); 1 row(s) remain unresolved: 113."

    async def fake_run_notes_formatter(**_kwargs):
        return {
            "ok": False, "summary": summary, "confidence": 0.9,
            "changed_rows": 1, "failed_rows": [113],
            "error_type": "validation_failed", "error": "row 113: invalid target",
        }

    monkeypatch.setattr(
        "notes.formatting_agent.run_notes_formatter", fake_run_notes_formatter,
    )
    response = client.post(
        f"/api/runs/{run_id}/notes-format", json={"sheet": "Notes-Listofnotes"},
    )
    assert response.status_code == 200
    done = _poll_done(client, run_id, "Notes-Listofnotes")
    assert done["failed_rows"] == [113]
    assert done["changed_rows"] == 1
    assert done["summary"] == summary
