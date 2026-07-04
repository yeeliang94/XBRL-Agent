"""Route tests for the mTool fill pipeline (api/mtool.py, Phase 4).

Uses the reload-server + swap-AUDIT_DB_PATH pattern from test_eval_routes.py.
AUTH_MODE=dev is the suite default (conftest), so requests auto-session.
"""
from __future__ import annotations

import io
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
SOFP = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


def _import_company_sofp(db_path) -> str:
    from concept_model.importer import import_company_targets, import_template
    from concept_model.parser import parse_template
    tree = parse_template(str(SOFP))
    jp = Path(db_path).parent / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    tid = import_template(db_path, jp)
    import_company_targets(db_path, tid)
    return tid


def _make_run(db, status="completed") -> int:
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
            "run_config_json) VALUES (?, ?, ?, ?, ?)",
            ("2026-07-05T00:00:00Z", "x.pdf", status, "2026-07-05T00:00:00Z",
             json.dumps({"filing_standard": "mfrs", "filing_level": "company",
                         "denomination": "thousands"})),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return run_id


def _seed_distinct_leaves(db, run_id, n=4):
    conn = sqlite3.connect(str(db))
    try:
        leaves = conn.execute(
            """
            SELECT concept_uuid FROM concept_nodes
            WHERE kind='LEAF' AND render_sheet LIKE '%Sub%'
              AND canonical_label IN (
                SELECT canonical_label FROM concept_nodes
                GROUP BY canonical_label HAVING COUNT(*) = 1)
            ORDER BY render_row LIMIT ?
            """, (n,)).fetchall()
        for i, (uuid,) in enumerate(leaves):
            conn.execute(
                "INSERT OR REPLACE INTO run_concept_facts("
                "run_id, concept_uuid, period, entity_scope, value, "
                "value_status, updated_at) VALUES (?,?,?,?,?,?,?)",
                (run_id, uuid, "CY", "Company", 1000 + i, "observed",
                 "2026-07-05T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()
    return len(leaves)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    import importlib
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db
    from db.schema import init_db
    init_db(db)
    _import_company_sofp(db)
    return TestClient(srv.app), db, srv


# ---------------------------------------------------------------- GET doc

def test_get_fill_doc_on_completed_run(client):
    tc, db, _ = client
    run_id = _make_run(db)
    n = _seed_distinct_leaves(db, run_id)
    resp = tc.get(f"/api/runs/{run_id}/mtool-fill")
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert len(doc["writes"]) == n
    assert doc["strict"] is True
    assert doc["meta"]["filing_standard"] == "mfrs"
    assert doc["meta"]["columns_unresolved"] is True


def test_get_fill_doc_on_running_run_is_409(client):
    tc, db, _ = client
    run_id = _make_run(db, status="running")
    resp = tc.get(f"/api/runs/{run_id}/mtool-fill")
    assert resp.status_code == 409


def test_get_fill_doc_unknown_run_is_404(client):
    tc, _, _ = client
    assert tc.get("/api/runs/99999/mtool-fill").status_code == 404


# ---------------------------------------------------------------- POST patch

def _upload_our_template():
    return {"template": ("01-SOFP-CuNonCu.xlsx", SOFP.read_bytes(),
                         "application/vnd.openxmlformats-officedocument."
                         "spreadsheetml.sheet")}


def test_patch_with_explicit_column_map(client, tmp_path):
    tc, db, _ = client
    run_id = _make_run(db)
    n = _seed_distinct_leaves(db, run_id)
    # Our template's sub-sheet is A=label, B=CY.
    doc = tc.get(f"/api/runs/{run_id}/mtool-fill").json()
    sheet = doc["meta"]["sheets_covered"][0]
    cmap = {sheet: {"label_column": "A", "columns": {"current_year": "B"}}}

    resp = tc.post(
        f"/api/runs/{run_id}/mtool-fill/patch",
        files=_upload_our_template(),
        data={"column_map": json.dumps(cmap), "strict": "true"},
    )
    assert resp.status_code == 200, resp.text
    report = json.loads(resp.headers["X-mTool-Report"])
    assert report["status"] == "ok"
    assert report["counts"]["written"] == n
    assert report["counts"]["unresolved"] == 0

    # The returned bytes are a valid workbook with the values.
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(resp.content))
    assert sheet in wb.sheetnames


def test_patch_auto_detects_column_map(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    resp = tc.post(
        f"/api/runs/{run_id}/mtool-fill/patch",
        files=_upload_our_template(),
        data={"strict": "true"},  # no column_map → auto-detect
    )
    assert resp.status_code == 200, resp.text
    report = json.loads(resp.headers["X-mTool-Report"])
    assert report["counts"]["written"] >= 1


def test_patch_running_run_is_409(client):
    tc, db, _ = client
    run_id = _make_run(db, status="running")
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(), data={})
    assert resp.status_code == 409


def test_patch_non_xlsx_upload_is_422(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    resp = tc.post(
        f"/api/runs/{run_id}/mtool-fill/patch",
        files={"template": ("junk.xlsx", b"not a zip",
                            "application/octet-stream")},
        data={},
    )
    assert resp.status_code == 422


def test_patch_no_facts_is_422(client):
    tc, db, _ = client
    run_id = _make_run(db)  # no facts seeded
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(), data={})
    assert resp.status_code == 422


def test_patch_malformed_column_map_is_422_not_500(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    # columns must be an object; a string where a dict belongs used to raise
    # AttributeError deep in apply_column_map -> uncaught 500 + temp leak.
    bad = json.dumps({"SOFP-Sub-CuNonCu": "B"})
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(),
                   data={"column_map": bad})
    assert resp.status_code == 422


def test_patch_column_map_bad_json_is_422(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(),
                   data={"column_map": "{not json"})
    assert resp.status_code == 422


def test_report_header_is_present_and_bounded(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(), data={"strict": "true"})
    assert resp.status_code == 200
    header = resp.headers["X-mTool-Report"]
    assert len(header.encode("utf-8")) <= 6000
    parsed = json.loads(header)
    assert "counts" in parsed and "truncated" in parsed


def test_no_temp_dirs_leak_across_error_and_success(client, tmp_path):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    # An error path (malformed column_map) then a success path.
    tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
            files=_upload_our_template(),
            data={"column_map": json.dumps({"S": 1})})
    tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
            files=_upload_our_template(), data={"strict": "true"})
    staging = tmp_path / "_mtool_tmp"
    leftovers = list(staging.glob("*")) if staging.exists() else []
    assert leftovers == [], f"temp dirs leaked: {leftovers}"


def test_temp_dir_cleaned_after_patch(client, tmp_path):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(), data={"strict": "true"})
    assert resp.status_code == 200
    # BackgroundTask runs after the response is consumed by TestClient.
    leftovers = list((tmp_path / "_mtool_tmp").glob("*")) \
        if (tmp_path / "_mtool_tmp").exists() else []
    assert leftovers == []
