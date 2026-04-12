"""HTTP endpoint tests for the History API (Phase 3).

Covers the four new endpoints under /api/runs:
  GET    /api/runs                    — list with filters + pagination
  GET    /api/runs/{id}               — detail hydration
  DELETE /api/runs/{id}               — remove from DB (leaves disk alone)
  GET    /api/runs/{id}/download/filled — stream merged workbook

All tests use a fresh tmp_path DB so runs don't leak between cases.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db.schema import init_db
from db import repository as repo


def _seed_run(
    db_path: Path,
    *,
    session_id: str,
    pdf_filename: str,
    output_dir: str,
    status: str = "completed",
    created_at: str | None = None,
    config: dict | None = None,
    merged_workbook_path: str | None = None,
    agent_models: list[tuple[str, str]] | None = None,
) -> int:
    """Insert a run directly via SQL so tests can fully control timestamps."""
    now = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
            "output_dir, run_config_json, scout_enabled, started_at, ended_at, "
            "merged_workbook_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now, pdf_filename, status, session_id, output_dir,
                json.dumps(config) if config is not None else None,
                0, now, now, merged_workbook_path,
            ),
        )
        run_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        for stmt, model in (agent_models or []):
            conn.execute(
                "INSERT INTO run_agents(run_id, statement_type, variant, model, "
                "status, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, stmt, None, model, "succeeded", now, now),
            )
        conn.commit()
    finally:
        conn.close()
    return run_id


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    """Wire server globals to a tmp output dir + fresh DB."""
    import server
    out = tmp_path / "output"
    out.mkdir()
    db_path = out / "xbrl_agent.db"
    init_db(db_path)

    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db_path)

    return TestClient(server.app), db_path, out


# ---------------------------------------------------------------------------
# GET /api/runs — list
# ---------------------------------------------------------------------------

def test_get_runs_returns_empty_list(api_env):
    client, _, _ = api_env
    r = client.get("/api/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["runs"] == []
    assert body["total"] == 0


def test_get_runs_returns_seeded_runs(api_env):
    client, db_path, _ = api_env
    _seed_run(db_path, session_id="a", pdf_filename="a.pdf",
              output_dir="/tmp/a",
              created_at="2026-04-01T10:00:00Z")
    _seed_run(db_path, session_id="b", pdf_filename="b.pdf",
              output_dir="/tmp/b",
              created_at="2026-04-02T10:00:00Z")
    _seed_run(db_path, session_id="c", pdf_filename="c.pdf",
              output_dir="/tmp/c",
              created_at="2026-04-03T10:00:00Z")

    body = client.get("/api/runs").json()
    assert body["total"] == 3
    # Newest first
    assert [r["pdf_filename"] for r in body["runs"]] == ["c.pdf", "b.pdf", "a.pdf"]


def test_get_runs_applies_filename_filter(api_env):
    client, db_path, _ = api_env
    _seed_run(db_path, session_id="a", pdf_filename="FINCO-2021.pdf", output_dir="/tmp/a")
    _seed_run(db_path, session_id="b", pdf_filename="otherco.pdf", output_dir="/tmp/b")

    body = client.get("/api/runs", params={"q": "finco"}).json()
    assert body["total"] == 1
    assert body["runs"][0]["pdf_filename"] == "FINCO-2021.pdf"


def test_get_runs_applies_status_filter(api_env):
    client, db_path, _ = api_env
    _seed_run(db_path, session_id="a", pdf_filename="a.pdf", output_dir="/tmp/a", status="completed")
    _seed_run(db_path, session_id="b", pdf_filename="b.pdf", output_dir="/tmp/b", status="failed")
    body = client.get("/api/runs", params={"status": "failed"}).json()
    assert body["total"] == 1
    assert body["runs"][0]["status"] == "failed"


def test_get_runs_applies_date_range(api_env):
    client, db_path, _ = api_env
    _seed_run(db_path, session_id="a", pdf_filename="old.pdf", output_dir="/tmp/a",
              created_at="2026-03-15T00:00:00Z")
    _seed_run(db_path, session_id="b", pdf_filename="mid.pdf", output_dir="/tmp/b",
              created_at="2026-04-01T00:00:00Z")
    _seed_run(db_path, session_id="c", pdf_filename="new.pdf", output_dir="/tmp/c",
              created_at="2026-04-10T00:00:00Z")
    body = client.get(
        "/api/runs",
        params={"from": "2026-03-20T00:00:00Z", "to": "2026-04-05T00:00:00Z"},
    ).json()
    assert body["total"] == 1
    assert body["runs"][0]["pdf_filename"] == "mid.pdf"


def test_get_runs_pagination(api_env):
    client, db_path, _ = api_env
    for i in range(25):
        _seed_run(
            db_path, session_id=f"s{i}", pdf_filename=f"f{i}.pdf",
            output_dir=f"/tmp/s{i}",
            created_at=f"2026-04-{(i % 28) + 1:02d}T00:00:{i:02d}Z",
        )
    body = client.get("/api/runs", params={"limit": 10, "offset": 0}).json()
    assert body["total"] == 25
    assert len(body["runs"]) == 10
    assert body["limit"] == 10
    assert body["offset"] == 0

    body2 = client.get("/api/runs", params={"limit": 10, "offset": 20}).json()
    assert len(body2["runs"]) == 5


# ---------------------------------------------------------------------------
# GET /api/runs/{id} — detail
# ---------------------------------------------------------------------------

def test_list_runs_echoes_clamped_limit_and_offset(api_env):
    """Peer-review regression: limit is clamped to 200 for the DB query,
    and the response must echo the clamped value, not the raw request
    value. Otherwise Load More pagination math desyncs.
    """
    client, db_path, _ = api_env
    _seed_run(db_path, session_id="a", pdf_filename="a.pdf", output_dir="/tmp/a")

    r = client.get("/api/runs?limit=500&offset=-5")
    assert r.status_code == 200
    body = r.json()
    # limit was clamped to 200, offset was clamped to 0 — both must match
    # what the DB actually saw.
    assert body["limit"] == 200
    assert body["offset"] == 0


def test_get_run_detail_returns_full_payload(api_env):
    client, db_path, _ = api_env
    run_id = _seed_run(
        db_path, session_id="detail", pdf_filename="finco.pdf",
        output_dir="/tmp/detail",
        config={"statements": ["SOFP", "SOPL"], "variants": {}, "models": {}, "use_scout": False, "infopack": None},
        agent_models=[("SOFP", "gemini-3-flash"), ("SOPL", "gpt-5.4")],
    )
    body = client.get(f"/api/runs/{run_id}").json()
    assert body["id"] == run_id
    assert body["pdf_filename"] == "finco.pdf"
    assert body["session_id"] == "detail"
    assert {a["statement_type"] for a in body["agents"]} == {"SOFP", "SOPL"}
    assert body["config"]["statements"] == ["SOFP", "SOPL"]


def test_get_run_detail_404_on_missing(api_env):
    client, _, _ = api_env
    r = client.get("/api/runs/99999")
    assert r.status_code == 404


def test_run_detail_endpoint_returns_agent_events(api_env):
    """Phase 7.3: the detail endpoint embeds each agent's persisted events
    as a list of live-SSE-shaped dicts {event, data, timestamp}.

    Also pins the terminal-event shape contract (Phase 7.4 normalization):
    new runs persist {success: bool, error: str | None}; legacy pre-Phase-6.5
    rows wrote {status: "succeeded", ...} and must be normalized at serialize
    time so the frontend only ever sees one shape.
    """
    client, db_path, _ = api_env
    run_id = _seed_run(
        db_path,
        session_id="events-api",
        pdf_filename="events.pdf",
        output_dir="/tmp/events-api",
        agent_models=[("SOFP", "m"), ("SOPL", "m")],
    )

    # SOFP gets Phase-6.5+ rows: tool_call, tool_result, and a live-shape
    # complete event. SOPL gets a LEGACY-shape complete row to verify the
    # serializer normalizes {status: "succeeded"} → {success: true}.
    with repo.db_session(db_path) as conn:
        agents = repo.fetch_run_agents(conn, run_id)
        agent_by_stmt = {a.statement_type: a for a in agents}
        sofp_id = agent_by_stmt["SOFP"].id
        sopl_id = agent_by_stmt["SOPL"].id

        repo.log_event(conn, sofp_id, "tool_call", {
            "tool_name": "read_template",
            "tool_call_id": "tc_1",
            "args": {"path": "/x.xlsx"},
        })
        repo.log_event(conn, sofp_id, "tool_result", {
            "tool_name": "read_template",
            "tool_call_id": "tc_1",
            "result_summary": "ok",
            "duration_ms": 50,
        })
        repo.log_event(conn, sofp_id, "complete", {
            "success": True, "error": None, "workbook_path": "/out.xlsx",
        })
        # Legacy shape — mirrors what the pre-6.5 post-run block wrote.
        repo.log_event(conn, sopl_id, "complete", {
            "status": "succeeded",
            "error": None,
            "workbook_path": "/out2.xlsx",
            "has_trace": True,
        })

    body = client.get(f"/api/runs/{run_id}").json()

    # Pull each agent's events by statement so order doesn't matter.
    agents_by_stmt = {a["statement_type"]: a for a in body["agents"]}
    sofp = agents_by_stmt["SOFP"]
    sopl = agents_by_stmt["SOPL"]

    # Both agents have the new `events` field as an SSE-shaped list.
    assert "events" in sofp
    assert isinstance(sofp["events"], list)
    assert len(sofp["events"]) == 3

    # Each item has the live-SSE triple.
    for evt in sofp["events"]:
        assert set(["event", "data", "timestamp"]).issubset(evt.keys())

    # The complete row on SOFP already had the live shape — pass-through.
    sofp_complete = next(e for e in sofp["events"] if e["event"] == "complete")
    assert sofp_complete["data"]["success"] is True
    assert sofp_complete["data"]["error"] is None

    # The legacy complete row on SOPL is normalized to the live shape.
    assert len(sopl["events"]) == 1
    sopl_complete = sopl["events"][0]
    assert sopl_complete["event"] == "complete"
    assert sopl_complete["data"]["success"] is True
    # Original fields are preserved so debugging isn't lost.
    assert sopl_complete["data"].get("workbook_path") == "/out2.xlsx"


# ---------------------------------------------------------------------------
# DELETE /api/runs/{id}
# ---------------------------------------------------------------------------

def test_delete_run_200_and_gone_from_list(api_env):
    client, db_path, _ = api_env
    run_id = _seed_run(db_path, session_id="del", pdf_filename="del.pdf", output_dir="/tmp/del")
    r = client.delete(f"/api/runs/{run_id}")
    assert r.status_code == 200

    body = client.get("/api/runs").json()
    assert body["total"] == 0


def test_delete_run_does_not_remove_output_directory(api_env, tmp_path):
    """History delete is DB-only. The on-disk folder must remain intact."""
    client, db_path, out = api_env
    session_id = "preserve-me"
    session_dir = out / session_id
    session_dir.mkdir()
    (session_dir / "filled.xlsx").write_bytes(b"not-empty")

    run_id = _seed_run(
        db_path, session_id=session_id, pdf_filename="keep.pdf",
        output_dir=str(session_dir),
        merged_workbook_path=str(session_dir / "filled.xlsx"),
    )
    r = client.delete(f"/api/runs/{run_id}")
    assert r.status_code == 200

    # Disk state untouched.
    assert session_dir.exists()
    assert (session_dir / "filled.xlsx").exists()


def test_delete_run_404_on_missing(api_env):
    client, _, _ = api_env
    r = client.delete("/api/runs/99999")
    assert r.status_code == 404


def test_delete_run_rejects_running_status(api_env):
    """Regression for peer-review [CRITICAL]: deleting a run that's still
    executing cascades away the parent row while the coordinator is still
    writing child records, creating orphans or FK violations. The endpoint
    must refuse with 409 Conflict and leave the row intact.
    """
    client, db_path, _ = api_env
    run_id = _seed_run(
        db_path, session_id="still-running", pdf_filename="in-progress.pdf",
        output_dir="/tmp/still-running", status="running",
    )

    r = client.delete(f"/api/runs/{run_id}")
    assert r.status_code == 409
    assert "running" in r.json()["detail"].lower()

    # Row must still exist afterwards.
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, "DB row was deleted despite 409"
    assert row[0] == "running"


def test_delete_run_rejects_session_in_active_runs(api_env, monkeypatch):
    """Second-layer guard: even if the DB says 'completed' (e.g. because of
    a stale row left by a crash), if the session is currently in the
    in-memory `active_runs` set an active extraction is happening right now
    and must not be deleted."""
    client, db_path, _ = api_env
    run_id = _seed_run(
        db_path, session_id="racing-session", pdf_filename="race.pdf",
        output_dir="/tmp/racing-session", status="completed",
    )
    import server
    monkeypatch.setattr(server, "active_runs", {"racing-session"})

    r = client.delete(f"/api/runs/{run_id}")
    assert r.status_code == 409
    assert "running" in r.json()["detail"].lower() or "active" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/runs/{id}/download/filled
# ---------------------------------------------------------------------------

def test_download_filled_uses_runs_merged_workbook_path(api_env, tmp_path):
    """Endpoint reads runs.merged_workbook_path — never derived from session_id."""
    client, db_path, out = api_env
    session_dir = out / "download-ok"
    session_dir.mkdir()
    wb_path = session_dir / "filled.xlsx"
    wb_path.write_bytes(b"fake xlsx bytes")

    run_id = _seed_run(
        db_path, session_id="download-ok", pdf_filename="f.pdf",
        output_dir=str(session_dir),
        merged_workbook_path=str(wb_path),
    )
    r = client.get(f"/api/runs/{run_id}/download/filled")
    assert r.status_code == 200
    assert r.content == b"fake xlsx bytes"
    # FastAPI's FileResponse sets content-disposition with the filename.
    cd = r.headers.get("content-disposition", "")
    assert f"run_{run_id}_filled.xlsx" in cd


def test_download_filled_404_when_merged_workbook_path_null(api_env):
    """A failed run has merged_workbook_path=NULL. Endpoint returns 404 with
    a clear message, NOT a 500 or a guessed path."""
    client, db_path, _ = api_env
    run_id = _seed_run(
        db_path, session_id="failed-run", pdf_filename="f.pdf",
        output_dir="/tmp/failed-run",
        merged_workbook_path=None,
        status="failed",
    )
    r = client.get(f"/api/runs/{run_id}/download/filled")
    assert r.status_code == 404
    body = r.json()
    assert "merged workbook" in body.get("detail", "").lower()


def test_download_filled_404_if_run_missing(api_env):
    client, _, _ = api_env
    r = client.get("/api/runs/99999/download/filled")
    assert r.status_code == 404


def test_download_filled_404_if_path_stored_but_file_deleted(api_env, tmp_path):
    """merged_workbook_path is set but the file was deleted from disk."""
    client, db_path, _ = api_env
    phantom_path = tmp_path / "ghost.xlsx"  # never created
    run_id = _seed_run(
        db_path, session_id="ghost", pdf_filename="f.pdf",
        output_dir=str(tmp_path),
        merged_workbook_path=str(phantom_path),
    )
    r = client.get(f"/api/runs/{run_id}/download/filled")
    assert r.status_code == 404
    body = r.json()
    assert "no longer exists" in body.get("detail", "").lower()
