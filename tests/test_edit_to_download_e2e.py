from concept_model.importer import import_company_targets
"""Phase 0 validation — the edit→recompute→download loop end to end.

Exercises the real HTTP surface: PATCH a leaf value via the facts endpoint,
then GET /api/runs/{id}/download/filled and confirm the streamed workbook
carries the edited value. This is the integration proof that Phases 1-3 hang
together (a live-PDF run is still owed, but the wiring is exercised here).
"""
from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient


REPO = Path(__file__).resolve().parent.parent
CO_SOFP = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    import importlib
    import server as server_module
    importlib.reload(server_module)
    server_module.AUDIT_DB_PATH = db_path

    from db.schema import init_db
    from concept_model.parser import parse_template
    from concept_model.importer import import_template
    init_db(db_path)
    tree = parse_template(str(CO_SOFP))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    _ct_tid = import_template(db_path, jp)
    import_company_targets(db_path, _ct_tid)

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
            "SELECT concept_uuid, render_sheet, render_row FROM concept_nodes "
            "WHERE render_sheet='SOFP-CuNonCu' AND kind='LEAF' "
            "ORDER BY render_row LIMIT 1"
        ).fetchone()
        conn.commit()
    finally:
        conn.close()

    tc = TestClient(server_module.app)
    tc.run_id = run_id  # type: ignore[attr-defined]
    tc.leaf_uuid = leaf["concept_uuid"]  # type: ignore[attr-defined]
    tc.sheet = leaf["render_sheet"]  # type: ignore[attr-defined]
    tc.row = int(leaf["render_row"])  # type: ignore[attr-defined]
    return tc


def test_edit_then_download_reflects_value(client: TestClient):
    # 1. Edit a leaf value over HTTP.
    r = client.patch(
        f"/api/runs/{client.run_id}/facts/{client.leaf_uuid}",
        json={"value": 65432.0},
    )
    assert r.status_code == 200, r.text
    assert r.json()["value_status"] == "user_override"

    # 2. Download the merged workbook — it must rebuild from the DB fact.
    dl = client.get(f"/api/runs/{client.run_id}/download/filled")
    assert dl.status_code == 200, dl.text
    wb = openpyxl.load_workbook(io.BytesIO(dl.content), data_only=False)
    # The face sheet (sheet name == render_sheet) carries the edited value in B.
    assert wb[client.sheet][f"B{client.row}"].value == 65432.0
