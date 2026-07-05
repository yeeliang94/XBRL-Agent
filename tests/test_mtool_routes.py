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


def test_oversized_upload_is_413(client, monkeypatch):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    import api.mtool as m
    monkeypatch.setattr(m, "_MAX_TEMPLATE_BYTES", 1000)  # our template > 1 KB
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(), data={})
    assert resp.status_code == 413


def test_zip_bomb_uncompressed_budget_is_413(client, monkeypatch):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    import api.mtool as m
    monkeypatch.setattr(m, "_MAX_UNCOMPRESSED_BYTES", 100)  # tiny budget
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(), data={})
    assert resp.status_code == 413
    assert "decompresses" in resp.json()["detail"]


def test_directory_bomb_member_count_is_413(client, monkeypatch):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    import api.mtool as m
    monkeypatch.setattr(m, "_MAX_ZIP_MEMBERS", 1)  # our template has > 1 part
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(), data={})
    assert resp.status_code == 413
    assert "too many zip members" in resp.json()["detail"]


def _add_note(db, run_id, sheet, row, label, html):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO notes_cells(run_id, sheet, row, label, html, "
            "updated_at) VALUES (?,?,?,?,?,?)",
            (run_id, sheet, row, label, html, "2026-07-05T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


def test_get_notes_fill_doc(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _add_note(db, run_id, "Notes-Listofnotes", 17,
              "Property, plant and equipment", "<h3>PPE</h3>")
    resp = tc.get(f"/api/runs/{run_id}/mtool-notes-fill")
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert doc["meta"]["counts"]["notes"] == 1
    assert doc["footnotes"][0]["label"] == "Property, plant and equipment"


def test_get_notes_fill_doc_running_run_is_409(client):
    tc, db, _ = client
    run_id = _make_run(db, status="running")
    assert tc.get(f"/api/runs/{run_id}/mtool-notes-fill").status_code == 409


def test_patch_fill_notes_off_omits_notes_block(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    _add_note(db, run_id, "Notes-CI", 12, "Corporate information", "<p>x</p>")
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(),
                   data={"strict": "true", "fill_notes": "false"})
    assert resp.status_code == 200, resp.text
    report = json.loads(resp.headers["X-mTool-Report"])
    assert "notes" not in report


def test_patch_fill_notes_on_reports_notes_block(client):
    """With notes present, the header carries a notes block. Our SOFP template
    has no +FootnoteTexts sheet, so notes can't land — but the download must
    still succeed (numbers filled) and surface the notes status gracefully."""
    tc, db, _ = client
    run_id = _make_run(db)
    n = _seed_distinct_leaves(db, run_id)
    _add_note(db, run_id, "Notes-CI", 12, "Corporate information", "<p>x</p>")
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(), data={"strict": "true"})
    assert resp.status_code == 200, resp.text
    report = json.loads(resp.headers["X-mTool-Report"])
    assert report["counts"]["written"] == n          # numbers still filled
    assert "notes" in report                          # notes block present
    assert report["notes"]["status"] == "degraded"    # no +FootnoteTexts here
    # Combined status must reflect the notes failure, NOT hide behind numeric ok.
    assert report["status"] == "degraded"
    assert report["numeric_status"] == "ok"
    # The returned bytes are still a valid workbook.
    import openpyxl
    openpyxl.load_workbook(io.BytesIO(resp.content))


def test_patch_notes_exception_preserves_numeric_fill(client, monkeypatch):
    """A notes-fill raise must NOT discard the already-good numeric workbook.

    Notes fill is best-effort (gotcha #22): on an unexpected exception the
    numeric-only fill is still returned (200) with the notes side degraded."""
    tc, db, _ = client
    run_id = _make_run(db)
    n = _seed_distinct_leaves(db, run_id)
    _add_note(db, run_id, "Notes-CI", 12, "Corporate information", "<p>x</p>")
    import api.mtool as m

    def boom(*_a, **_k):
        raise RuntimeError("notes patcher exploded")

    monkeypatch.setattr(m, "fill_footnotes", boom)
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(), data={"strict": "true"})
    assert resp.status_code == 200, resp.text
    report = json.loads(resp.headers["X-mTool-Report"])
    assert report["counts"]["written"] == n       # numbers survived the raise
    assert report["notes"]["status"] == "degraded"
    assert report["status"] == "degraded"
    assert report["numeric_status"] == "ok"
    import openpyxl
    openpyxl.load_workbook(io.BytesIO(resp.content))  # still a valid workbook


def test_patch_invalid_notes_doc_reports_degraded(client, monkeypatch):
    """An invalid machine-generated notes doc is reported degraded, not
    silently collapsed into "skipped"."""
    tc, db, _ = client
    run_id = _make_run(db)
    n = _seed_distinct_leaves(db, run_id)
    import api.mtool as m
    # footnotes present but each missing 'html' -> validate_notes_input errors.
    monkeypatch.setattr(
        m, "build_notes_fill_doc",
        lambda *_a, **_k: {"footnotes": [{"label": "PPE"}], "meta": {}})
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(), data={"strict": "true"})
    assert resp.status_code == 200, resp.text
    report = json.loads(resp.headers["X-mTool-Report"])
    assert report["counts"]["written"] == n
    assert report["notes"]["status"] == "degraded"
    assert report["status"] == "degraded"


def test_patch_non_letter_column_map_is_422(client):
    """A structurally-valid map with a non-letter column is rejected up front
    (422), not surfaced only as a buried per-write error."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_distinct_leaves(db, run_id)
    doc = tc.get(f"/api/runs/{run_id}/mtool-fill").json()
    sheet = doc["meta"]["sheets_covered"][0]
    bad = json.dumps(
        {sheet: {"label_column": "A", "columns": {"current_year": "1"}}})
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(),
                   data={"column_map": bad})
    assert resp.status_code == 422


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


# ------------------------------------------------ notes preview (diagnostic)

def test_notes_preview_reports_plan_and_slot_count(client):
    """The dry-run diagnostic returns a per-note plan + the template's existing
    fn_* slot count, and writes nothing. Our SOFP template has no fn_* slots,
    so a seeded note lands in `unresolved` and template_fn_slots is 0 — the
    signal that the concept isn't popup-backed."""
    tc, db, _ = client
    run_id = _make_run(db)
    _add_note(db, run_id, "Notes-CI", 12, "Corporate information", "<p>x</p>")
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/notes-preview",
                   files=_upload_our_template(),
                   data={"create_missing_notes": "false"})
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert plan["notes_in_run"] == 1
    assert plan["template_fn_slots"] == 0
    assert plan["will_fill_existing"] == [] and plan["will_create"] == []
    # No +FootnoteTexts sheet -> the fill degrades with an error, not a per-note
    # unresolved; either way nothing is planned to write.
    assert plan["unresolved"] or plan["errors"]


def test_notes_preview_running_run_is_409(client):
    tc, db, _ = client
    run_id = _make_run(db, status="running")
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/notes-preview",
                   files=_upload_our_template())
    assert resp.status_code == 409


def test_patch_accepts_create_missing_notes_param(client):
    """The create_missing_notes toggle is wired through the patch endpoint
    (no 4xx/5xx from the extra form field); numeric fill is unaffected."""
    tc, db, _ = client
    run_id = _make_run(db)
    n = _seed_distinct_leaves(db, run_id)
    _add_note(db, run_id, "Notes-CI", 12, "Corporate information", "<p>x</p>")
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch",
                   files=_upload_our_template(),
                   data={"strict": "true", "create_missing_notes": "true"})
    assert resp.status_code == 200, resp.text
    report = json.loads(resp.headers["X-mTool-Report"])
    assert report["counts"]["written"] == n
