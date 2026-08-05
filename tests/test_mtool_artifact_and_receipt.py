"""Steps 11A + 19 — see the report before you hold the file, and leave a record.

**11A.** The old endpoint streamed the workbook AND squeezed the report into an
HTTP header, capped at 20 rows / 6 KB. The download fired first, so the
operator held the file before they could see whether it was clean — the exact
thing Step 11's own acceptance criterion asked for. Now: patch returns the full
report plus an artifact id; a second request fetches the file, and a degraded
fill won't release it without an explicit acknowledgement.

**19.** A filled MBRS workbook is a regulatory artifact, and a completed run
stays editable, so two fills of "the same" run can legitimately differ. Every
fill writes one receipt recording the fact revision, both file hashes, the
template fingerprint, the column map, the preflight verdict and any override.
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


def _upload():
    return {"template": ("t.xlsx", io.BytesIO(SOFP.read_bytes()),
                         "application/vnd.openxmlformats-officedocument"
                         ".spreadsheetml.sheet")}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    import server as srv
    importlib.reload(srv)
    db = tmp_path / "xbrl.db"
    srv.AUDIT_DB_PATH = db
    from db.schema import init_db
    from concept_model.importer import import_company_targets, import_template
    from concept_model.parser import parse_template
    init_db(db)
    tree = parse_template(str(SOFP))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    import_company_targets(db, import_template(db, jp))
    return TestClient(srv.app), db, srv


def _make_run(db) -> int:
    conn = sqlite3.connect(str(db))
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
            "run_config_json) VALUES (?,?,?,?,?)",
            ("2026-07-27T00:00:00Z", "x.pdf", "completed",
             "2026-07-27T00:00:00Z",
             json.dumps({"filing_standard": "mfrs", "filing_level": "company",
                         "denomination": "thousands"}))).lastrowid
        conn.commit()
    finally:
        conn.close()
    return run_id


def _seed(db, run_id, n=4, base=1000):
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
                (run_id, uuid, "CY", "Company", base + i, "observed", "t"))
        conn.commit()
    finally:
        conn.close()
    return len(leaves)


def _patch(tc, run_id, **data):
    return tc.post(f"/api/runs/{run_id}/mtool-fill/patch", files=_upload(),
                   data={"strict": "true", **data})


# --------------------------------------------------------------- Step 11A

def test_patch_returns_a_report_not_a_file(client):
    tc, db, _ = client
    run_id = _make_run(db)
    n = _seed(db, run_id)
    resp = _patch(tc, run_id)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["status"] == "ok"
    assert body["counts"]["written"] == n
    assert body["artifact_id"]
    assert body["download_url"].endswith(body["artifact_id"])
    assert body["artifact_expires_in_s"] > 0


def test_clean_fill_downloads_without_ceremony(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    body = _patch(tc, run_id).json()
    resp = tc.get(body["download_url"])
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats")
    import openpyxl
    openpyxl.load_workbook(io.BytesIO(resp.content))


def _degraded(tc, db, run_id):
    """Force a degraded fill: point the label column at an empty column so
    every write goes unresolved."""
    doc = tc.get(f"/api/runs/{run_id}/mtool-fill").json()
    sheet = doc["meta"]["sheets_covered"][0]
    cmap = {sheet: {"label_column": "Z", "columns": {"current_year": "B"}}}
    return _patch(tc, run_id, column_map=json.dumps(cmap))


def test_degraded_fill_withholds_the_file_until_acknowledged(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    body = _degraded(tc, db, run_id).json()
    assert body["status"] == "degraded"
    assert body["counts"]["unresolved"] > 0

    blocked = tc.get(body["download_url"])
    assert blocked.status_code == 409
    assert "Read the report" in blocked.json()["detail"]

    ok = tc.get(body["download_url"],
                params={"acknowledge_degraded": "I read it, proceed"})
    assert ok.status_code == 200


def test_degraded_acknowledgement_lands_on_the_receipt(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    body = _degraded(tc, db, run_id).json()
    tc.get(body["download_url"],
           params={"acknowledge_degraded": "aware of 4 unplaced rows"})
    receipt = tc.get(
        f"/api/runs/{run_id}/mtool-fill/receipts").json()["receipts"][0]
    assert receipt["degraded_ack"] == "aware of 4 unplaced rows"
    assert receipt["downloaded_at"]


def test_artifact_is_scoped_to_its_run(client):
    tc, db, _ = client
    run_a, run_b = _make_run(db), _make_run(db)
    _seed(db, run_a)
    _seed(db, run_b)
    artifact = _patch(tc, run_a).json()["artifact_id"]
    resp = tc.get(f"/api/runs/{run_b}/mtool-fill/artifact/{artifact}")
    assert resp.status_code == 404


def test_unknown_artifact_404s(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    assert tc.get(
        f"/api/runs/{run_id}/mtool-fill/artifact/nope").status_code == 404


def test_artifacts_are_capped_and_oldest_evicted(client, tmp_path,
                                                 monkeypatch):
    tc, db, _ = client
    import api.mtool as m
    monkeypatch.setattr(m, "_MAX_LIVE_ARTIFACTS", 2)
    run_id = _make_run(db)
    _seed(db, run_id)
    first = _patch(tc, run_id).json()
    _patch(tc, run_id)
    _patch(tc, run_id)
    # The oldest was evicted rather than accumulating on disk forever.
    assert tc.get(first["download_url"]).status_code == 404
    assert len(list((tmp_path / "_mtool_tmp").glob("*"))) <= 2


# ----------------------------------------------------------------- Step 19

def test_a_fill_writes_exactly_one_receipt(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    _patch(tc, run_id)
    receipts = tc.get(
        f"/api/runs/{run_id}/mtool-fill/receipts").json()["receipts"]
    assert len(receipts) == 1
    r = receipts[0]
    assert r["status"] == "ok"
    assert len(r["source_sha256"]) == 64
    assert len(r["output_sha256"]) == 64
    assert r["source_sha256"] != r["output_sha256"]  # we did write something
    assert r["template_fingerprint"]
    assert r["translation_version"] == "identity-1"
    assert r["column_map"]
    assert r["snapshot"]["fact_count"] == 4
    assert r["report"]["counts"]["written"] == 4
    assert r["created_at"]
    assert r["downloaded_at"] is None  # not taken yet


def test_refilling_the_same_run_yields_two_distinguishable_receipts(client):
    """The point of the receipt: a completed run stays editable, so 'the same
    run' can produce two different workbooks. Both are recorded, with distinct
    output hashes and distinct fact-revision digests."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id, base=1000)
    _patch(tc, run_id)
    _seed(db, run_id, base=9000)   # someone corrects the figures
    _patch(tc, run_id)

    receipts = tc.get(
        f"/api/runs/{run_id}/mtool-fill/receipts").json()["receipts"]
    assert len(receipts) == 2
    newer, older = receipts  # newest first
    assert newer["output_sha256"] != older["output_sha256"]
    assert newer["snapshot"]["digest"] != older["snapshot"]["digest"]


def test_receipt_records_who_filled_when_a_session_exists(client):
    """Best-effort: in dev mode there is no resolvable session, and an
    anonymous receipt is far better than a failed fill."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    _patch(tc, run_id)
    receipt = tc.get(
        f"/api/runs/{run_id}/mtool-fill/receipts").json()["receipts"][0]
    assert "operator" in receipt


def test_a_failed_receipt_write_withholds_the_artifact(client, tmp_path,
                                                       monkeypatch):
    """No receipt, no artifact (peer review, 2026-08-05).

    The original posture ("the receipt is best-effort, never fail a good
    fill") meant a registrar-bound workbook could exist with no record of
    what produced it — the exact gap the receipts table exists to close. Now
    a failed receipt write fails the request with a plain 500, registers NO
    artifact, and cleans the temp dir.
    """
    tc, db, _ = client
    import api.mtool as m
    monkeypatch.setattr(m, "write_fill_receipt",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("audit db is down")))
    run_id = _make_run(db)
    _seed(db, run_id)
    before = set(m._ARTIFACTS)
    resp = _patch(tc, run_id)
    assert resp.status_code == 500, resp.text
    assert "receipt" in str(resp.json()["detail"])
    assert "was not released" in str(resp.json()["detail"])
    assert set(m._ARTIFACTS) == before, \
        "no artifact may be registered without a receipt"
    leftovers = list((tmp_path / "_mtool_tmp").glob("*"))
    assert leftovers == [], f"temp dir leaked on a late failure: {leftovers}"


def test_receipt_module_raises_on_write_failure(tmp_path):
    """The module-level half of the same contract: the write RAISES rather
    than degrading to None, so no caller can quietly ignore a lost receipt."""
    from mtool.receipt import write_fill_receipt
    missing = tmp_path / "no-such-dir" / "x.db"
    with pytest.raises(Exception):
        write_fill_receipt(
            missing, run_id=1, snapshot={}, source_sha256=None,
            output_sha256=None, template_fingerprint=None, column_map=None,
            translation_version=None, preflight=None, preflight_override=None,
            operator=None, report=None)


def test_snapshot_is_one_consistent_read(tmp_path):
    """Numbers and notes used to be read at different moments in the request,
    so a mid-request edit could produce a workbook matching no single revision.
    The snapshot helper returns the rows AND the identity of that revision."""
    from concept_model.importer import import_company_targets, import_template
    from concept_model.parser import parse_template
    from db.schema import init_db
    from mtool.receipt import snapshot_facts

    db = tmp_path / "x.db"
    init_db(db)
    tree = parse_template(str(SOFP))
    jp = tmp_path / "t.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    import_company_targets(db, import_template(db, jp))
    run_id = _make_run(db)
    _seed(db, run_id, n=3)

    rows, identity = snapshot_facts(db, run_id, filing_standard="mfrs",
                                    filing_level="company")
    assert len(rows) == 3
    assert identity["fact_count"] == 3
    assert identity["max_updated_at"] == "t"
    # Re-reading unchanged data gives the same identity.
    assert snapshot_facts(db, run_id, filing_standard="mfrs",
                          filing_level="company")[1] == identity
