"""Human-file API routes: attach, replace, remove, and the live comparison."""
from __future__ import annotations

import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests._human_file_fixture import (
    add_run,
    filled_file,
    leaf_facts,
    make_sofp_db,
    sofp_config,
)

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    import server as srv

    importlib.reload(srv)
    srv.AUDIT_DB_PATH = make_sofp_db(tmp_path)
    return srv


def _upload(client, run_id, path, name="human.xlsx", unit="units"):
    return client.post(
        f"/api/runs/{run_id}/human-file", data={"unit": unit},
        files={"file": (name, path.read_bytes(), _XLSX)})


def test_attach_compare_edit_replace_and_remove(app, tmp_path):
    db = app.AUDIT_DB_PATH
    run_id = add_run(db, sofp_config())
    facts = leaf_facts(db)
    path = filled_file(db, run_id, facts, tmp_path)
    client = TestClient(app.app)

    resp = _upload(client, run_id, path)
    assert resp.status_code == 200, resp.text
    assert resp.json()["file"]["summary"]["typed_values"] == len(facts)
    assert client.get(f"/api/runs/{run_id}/human-file").json()["file"][
        "filename"] == "human.xlsx"

    totals = client.get(f"/api/runs/{run_id}/human-comparison").json()[
        "figures"]["totals"]["Company"]
    assert totals == {"human_filled": len(facts), "both_filled": len(facts),
                      "same_value": len(facts), "ai_only": 0}

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO notes_cells(run_id, sheet, row, label, html, "
            "concept_uuid, updated_at) VALUES (?, 'Notes-CI', 4, "
            "'Empty note', '<p></p>', 'empty-field', '2026-09-25')",
            (run_id,),
        )
    assert client.get(f"/api/runs/{run_id}/human-comparison").json()[
        "notes"]["totals"]["ai_only"] == 0

    uuid, period, value = facts[0]
    edit = client.patch(f"/api/runs/{run_id}/facts/{uuid}",
                        json={"value": value + 1, "period": period})
    assert edit.status_code == 200, edit.text
    comparison = client.get(f"/api/runs/{run_id}/human-comparison").json()
    assert comparison["figures"]["totals"]["Company"]["same_value"] == len(facts) - 1
    assert [s["status"] for s in comparison["figures"]["slots"]
            if (s["concept_uuid"], s["period"]) == (uuid, period)] == ["different"]

    assert _upload(client, run_id, path, name="second.xlsx").status_code == 200
    assert client.get(f"/api/runs/{run_id}/human-file").json()["file"][
        "filename"] == "second.xlsx"

    assert client.delete(f"/api/runs/{run_id}/human-file").status_code == 200
    assert client.get(f"/api/runs/{run_id}/human-file").json() == {"file": None}
    assert client.get(f"/api/runs/{run_id}/human-comparison").status_code == 404
    assert client.delete(f"/api/runs/{run_id}/human-file").status_code == 404


def test_refuses_unfinished_runs_and_non_workbooks(app, tmp_path):
    db = app.AUDIT_DB_PATH
    source = add_run(db, sofp_config())
    path = filled_file(db, source, leaf_facts(db), tmp_path)
    draft = add_run(db, sofp_config(), status="draft")
    client = TestClient(app.app)

    assert _upload(client, draft, path).status_code == 409
    assert _upload(client, 9999, path).status_code == 404
    assert _upload(client, source, path, name="human.csv").status_code == 422
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM human_files").fetchone()[0] == 0


def test_routes_require_a_signed_in_user(app, monkeypatch):
    monkeypatch.delenv("AUTH_MODE", raising=False)
    client = TestClient(app.app)

    assert client.get("/api/runs/1/human-file").status_code == 401
    assert client.get("/api/runs/1/human-comparison").status_code == 401
    assert client.delete("/api/runs/1/human-file").status_code == 401
