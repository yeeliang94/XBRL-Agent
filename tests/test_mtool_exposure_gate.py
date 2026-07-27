"""Step 8A — the mTool fill is a FILING action, and it is gated as one.

Two independent gates, and the distinction matters:

* **Exposure** (``XBRL_MTOOL_FILL``, default off) — whether this deployment
  offers the action at all. Code being written and Mac-tested doesn't make a
  filing-capable button safe to show; it stays hidden until a machine-generated
  workbook has passed Validate/Generate on Windows (plan Step 7).
* **Preflight** — whether THIS run's data is settled enough to file. Run status
  alone was never sufficient: ``completed_with_errors`` is fillable, and the
  exporter deliberately writes conflicting figures rather than blanking cells
  the operator can't see (peer-review finding 4).

Read-only routes stay available under both gates — they produce no artifact.
"""
from __future__ import annotations

import importlib
import io
import json
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
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
            "run_config_json) VALUES (?,?,?,?,?)",
            ("2026-07-27T00:00:00Z", "x.pdf", status, "2026-07-27T00:00:00Z",
             json.dumps({"filing_standard": "mfrs", "filing_level": "company",
                         "denomination": "thousands"}))).lastrowid
        conn.commit()
    finally:
        conn.close()
    return run_id


def _seed_leaves(db, run_id, n=4, value_status="observed"):
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
                (run_id, uuid, "CY", "Company", 1000 + i, value_status,
                 "2026-07-27T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()
    return len(leaves)


def _upload():
    return {"template": ("t.xlsx", io.BytesIO(SOFP.read_bytes()),
                         "application/vnd.openxmlformats-officedocument"
                         ".spreadsheetml.sheet")}


def _client(tmp_path, monkeypatch, *, exposed: bool):
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    monkeypatch.setenv("XBRL_MTOOL_FILL", "1" if exposed else "0")
    import server as srv
    importlib.reload(srv)
    db = tmp_path / "xbrl.db"
    srv.AUDIT_DB_PATH = db
    from db.schema import init_db
    init_db(db)
    _import_company_sofp(db)
    return TestClient(srv.app), db, srv


@pytest.fixture
def hidden(tmp_path, monkeypatch):
    return _client(tmp_path, monkeypatch, exposed=False)


@pytest.fixture
def exposed(tmp_path, monkeypatch):
    return _client(tmp_path, monkeypatch, exposed=True)


# ------------------------------------------------------------- exposure gate

def test_flag_defaults_off(monkeypatch):
    import server as srv
    monkeypatch.delenv("XBRL_MTOOL_FILL", raising=False)
    assert srv._mtool_fill_enabled() is False


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("maybe", False),
])
def test_flag_parsing(monkeypatch, value, expected):
    import server as srv
    monkeypatch.setenv("XBRL_MTOOL_FILL", value)
    assert srv._mtool_fill_enabled() is expected


def test_patch_404s_when_the_action_is_not_exposed(hidden):
    tc, db, _ = hidden
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch", files=_upload(),
                   data={"strict": "true"})
    assert resp.status_code == 404


def test_the_other_writing_routes_404_too(hidden):
    tc, db, _ = hidden
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    for path in ("mtool-fill/detect-columns", "mtool-fill/notes-preview"):
        resp = tc.post(f"/api/runs/{run_id}/{path}", files=_upload())
        assert resp.status_code == 404, path
    assert tc.get(
        f"/api/runs/{run_id}/mtool-fill/artifact/deadbeef").status_code == 404


def test_read_only_routes_stay_available_when_hidden(hidden):
    """A fill doc and a preflight verdict produce no filing artifact, so
    switching the action off must not blind the operator to what the run
    contains — or to the receipts of fills already produced."""
    tc, db, _ = hidden
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    assert tc.get(f"/api/runs/{run_id}/mtool-fill").status_code == 200
    assert tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").status_code == 200
    assert tc.get(f"/api/runs/{run_id}/mtool-fill/receipts").status_code == 200


def test_config_reports_the_flag(hidden):
    tc, _, _ = hidden
    assert tc.get("/api/config").json()["mtool_fill"] is False


def test_config_reports_the_flag_when_exposed(exposed):
    tc, _, _ = exposed
    assert tc.get("/api/config").json()["mtool_fill"] is True


# ---------------------------------------------------------------- preflight

def _open_conflict(db, run_id):
    """Flip one of the run's facts to 'conflict' — an unadjudicated figure."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE run_concept_facts SET value_status='conflict' "
            "WHERE run_id = ? AND rowid = (SELECT MIN(rowid) FROM "
            "run_concept_facts WHERE run_id = ?)", (run_id, run_id))
        conn.commit()
    finally:
        conn.close()


def test_clean_run_passes_preflight(exposed):
    tc, db, _ = exposed
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is True
    assert body["blockers"] == []


def test_open_conflict_blocks_the_fill_with_a_plain_reason(exposed):
    tc, db, _ = exposed
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _open_conflict(db, run_id)

    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch", files=_upload(),
                   data={"strict": "true"})
    assert resp.status_code == 409, resp.text
    preflight = resp.json()["detail"]["preflight"]
    codes = [b["code"] for b in preflight["blockers"]]
    assert "open_conflicts" in codes
    blocker = preflight["blockers"][codes.index("open_conflicts")]
    # Names the row, in words a product person can act on.
    assert blocker["examples"], "the blocker must name the conflicting row(s)"
    assert "Review values" in blocker["message"]


def test_completed_with_errors_alone_does_not_block(exposed):
    """Finding 4: run status is not the filing-readiness gate. A run that
    finished with errors but has no unadjudicated data still fills."""
    tc, db, _ = exposed
    run_id = _make_run(db, status="completed_with_errors")
    _seed_leaves(db, run_id)
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is True
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch", files=_upload(),
                   data={"strict": "true"})
    assert resp.status_code == 200, resp.text


def test_open_reviewer_flag_blocks(exposed):
    tc, db, _ = exposed
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO reviewer_flags(run_id, category, reasoning, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (run_id, "stuck", "Cannot reconcile the tax charge", "open",
             "t", "t"))
        conn.commit()
    finally:
        conn.close()
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is False
    assert [b["code"] for b in body["blockers"]] == ["open_reviewer_flags"]


def test_answered_reviewer_flag_does_not_block(exposed):
    tc, db, _ = exposed
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO reviewer_flags(run_id, category, status, created_at, "
            "updated_at) VALUES (?,?,?,?,?)",
            (run_id, "stuck", "answered", "t", "t"))
        conn.commit()
    finally:
        conn.close()
    assert tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()["ok"]


def test_unresolved_notes_coverage_blocks(exposed):
    tc, db, _ = exposed
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO notes_coverage_rows(run_id, note_num, status, title, "
            "updated_at) VALUES (?,?,?,?,?)",
            (run_id, 7, "missing", "Related party transactions", "t"))
        conn.commit()
    finally:
        conn.close()
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is False
    blocker = body["blockers"][0]
    assert blocker["code"] == "notes_coverage_unresolved"
    assert "Note 7 — Related party transactions" in blocker["examples"][0]


def test_reviewer_resolved_coverage_row_does_not_block(exposed):
    tc, db, _ = exposed
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO notes_coverage_rows(run_id, note_num, status, "
            "reviewer_verdict, updated_at) VALUES (?,?,?,?,?)",
            (run_id, 7, "missing", "not_applicable", "t"))
        conn.commit()
    finally:
        conn.close()
    assert tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()["ok"]


def test_unavailable_notes_inventory_blocks(exposed):
    tc, db, _ = exposed
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO notes_coverage_rows(run_id, note_num, status, "
            "updated_at) VALUES (?,?,?,?)",
            (run_id, -1, "inventory_unavailable", "t"))
        conn.commit()
    finally:
        conn.close()
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert [b["code"] for b in body["blockers"]] == [
        "notes_inventory_unavailable"]


def test_conflict_outside_the_fill_warns_but_does_not_block(exposed):
    """A conflict on a row this fill can't write (SOCIE isn't filled yet)
    shouldn't stop a legitimate SOFP fill — but it shouldn't be silent."""
    tc, db, _ = exposed
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    conn = sqlite3.connect(str(db))
    try:
        # A COMPUTED total is never written by the exporter.
        uuid = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE kind='COMPUTED' "
            "LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO run_concept_facts(run_id, concept_uuid, "
            "period, entity_scope, value, value_status, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, uuid, "CY", "Company", 42, "conflict", "t"))
        conn.commit()
    finally:
        conn.close()
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is True
    assert [w["code"] for w in body["warnings"]] == ["conflicts_outside_fill"]


def test_explicit_acknowledgement_overrides_and_is_recorded(exposed):
    tc, db, _ = exposed
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _open_conflict(db, run_id)

    resp = tc.post(
        f"/api/runs/{run_id}/mtool-fill/patch", files=_upload(),
        data={"strict": "true",
              "acknowledge_preflight": "Partner approved the disputed figure"})
    assert resp.status_code == 200, resp.text
    # The override is on the receipt, not just in a log line.
    receipts = tc.get(f"/api/runs/{run_id}/mtool-fill/receipts").json()["receipts"]
    assert len(receipts) == 1
    assert receipts[0]["preflight_override"] == (
        "Partner approved the disputed figure")
    assert [b["code"] for b in receipts[0]["preflight"]["blockers"]] == [
        "open_conflicts"]


def test_blank_acknowledgement_is_not_an_override(exposed):
    tc, db, _ = exposed
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _open_conflict(db, run_id)
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch", files=_upload(),
                   data={"strict": "true", "acknowledge_preflight": "   "})
    assert resp.status_code == 409
