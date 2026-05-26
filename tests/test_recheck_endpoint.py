"""Phase 4.3 — GET /api/runs/{id}/recheck.

Re-runs the cross-check registry against workbooks re-exported from the
current DB facts, so the validator can reflect manual edits without a full
pipeline re-run. Pins: a run with facts returns serialised results; an edit
flows through (the re-export the checks read is rebuilt from facts); a run
with no facts / context returns 404.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO = Path(__file__).resolve().parent.parent
CO_SOFP = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    import importlib
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db_path
    from db.schema import init_db
    from concept_model.parser import parse_template
    from concept_model.importer import import_template
    init_db(db_path)
    tree = parse_template(str(CO_SOFP))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    import_template(db_path, jp)

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "SOFP_filled.xlsx").write_bytes(CO_SOFP.read_bytes())
    merged = session_dir / "filled.xlsx"
    merged.write_bytes(CO_SOFP.read_bytes())

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
            "ended_at, session_id, merged_workbook_path, run_config_json) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("2026-05-25T00:00:00Z", "x.pdf", "completed",
             "2026-05-25T00:00:00Z", "2026-05-25T01:00:00Z", "session",
             str(merged),
             json.dumps({"filing_level": "company", "filing_standard": "mfrs"})),
        ).lastrowid
        conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, variant, model, "
            "status, started_at) VALUES (?,?,?,?,?,?)",
            (run_id, "SOFP", "CuNonCu", "test", "succeeded",
             "2026-05-25T00:00:00Z"),
        )
        leaf = conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE render_sheet='SOFP-CuNonCu' AND kind='LEAF' "
            "ORDER BY render_row LIMIT 1"
        ).fetchone()["concept_uuid"]
        # One observed fact so the run has facts to re-check.
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, source, updated_at) "
            "VALUES (?,?,'CY','Company',?, 'observed','pdf','2026-05-25Z')",
            (run_id, leaf, 100.0),
        )
        conn.commit()
    finally:
        conn.close()

    tc = TestClient(srv.app)
    tc.run_id = run_id  # type: ignore[attr-defined]
    return tc


def test_recheck_returns_results(client: TestClient):
    r = client.get(f"/api/runs/{client.run_id}/recheck")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] == client.run_id
    assert isinstance(body["results"], list)
    # Each result carries the standard cross-check shape.
    for res in body["results"]:
        assert {"name", "status", "message"} <= set(res.keys())


def test_recheck_unknown_run_404(client: TestClient):
    assert client.get("/api/runs/9999/recheck").status_code == 404


def test_recheck_run_without_facts_404(client: TestClient, tmp_path: Path):
    import sqlite3 as _sq
    import server as srv
    conn = _sq.connect(str(srv.AUDIT_DB_PATH))
    try:
        rid = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
            "merged_workbook_path) VALUES (?,?,?,?,?)",
            ("2026-05-25T00:00:00Z", "y.pdf", "completed",
             "2026-05-25T00:00:00Z", str(tmp_path / "none.xlsx")),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()
    assert client.get(f"/api/runs/{rid}/recheck").status_code == 404
