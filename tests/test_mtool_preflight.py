"""Filing-readiness preflight for the mTool fill (peer-review finding 4).

Run status alone was never sufficient: ``completed_with_errors`` is fillable,
and the exporter deliberately writes conflicting figures rather than blanking
cells the operator can't see. ``mtool/preflight.py`` is the real gate — it
blocks on unadjudicated data that would REACH the workbook (open conflicts,
open reviewer flags, unresolved notes coverage) and an override requires a
written reason that lands on the receipt.

There is deliberately NO exposure gate (2026-08-05 replay decision): the v2
build hid these routes behind ``XBRL_MTOOL_FILL``; the product owner chose to
keep the fill exposed, so the preflight and the degraded-artifact
acknowledgment are the whole safety story. A pin below asserts the gate stays
gone.
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
    from concept_model.filing_targets import persist_template_manifest
    from concept_model.importer import import_company_targets, import_template
    from concept_model.parser import parse_template
    tree = parse_template(str(SOFP))
    jp = Path(db_path).parent / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    tid = import_template(db_path, jp)
    import_company_targets(db_path, tid)
    persist_template_manifest(db_path, SOFP)
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


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    # No XBRL_MTOOL_FILL: the replay ships without an exposure gate, so the
    # env var must have no bearing on anything below.
    monkeypatch.delenv("XBRL_MTOOL_FILL", raising=False)
    import server as srv
    importlib.reload(srv)
    db = tmp_path / "xbrl.db"
    srv.AUDIT_DB_PATH = db
    from db.schema import init_db
    init_db(db)
    _import_company_sofp(db)
    return TestClient(srv.app), db, srv


# ------------------------------------------------------------- no gate (pin)

def test_no_exposure_gate_exists(client):
    """The replay decision: routes are live with no flag set, the config
    payload carries no `mtool_fill` toggle, and server has no gate helper."""
    tc, db, srv = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    # The workbook-producing route answers (with a fill, not a gate 404).
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch", files=_upload(),
                   data={"strict": "true"})
    assert resp.status_code == 200, resp.text
    assert "mtool_fill" not in tc.get("/api/config").json()
    assert not hasattr(srv, "_mtool_fill_enabled")


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


def test_clean_run_passes_preflight(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is True
    assert body["blockers"] == []


def test_open_conflict_blocks_the_fill_with_a_plain_reason(client):
    tc, db, _ = client
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


def test_completed_with_errors_alone_does_not_block(client):
    """Finding 4: run status is not the filing-readiness gate. A run that
    finished with errors but has no unadjudicated data still fills."""
    tc, db, _ = client
    run_id = _make_run(db, status="completed_with_errors")
    _seed_leaves(db, run_id)
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is True
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch", files=_upload(),
                   data={"strict": "true"})
    assert resp.status_code == 200, resp.text


def test_open_reviewer_flag_blocks(client):
    tc, db, _ = client
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


def test_answered_reviewer_flag_does_not_block(client):
    tc, db, _ = client
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


def test_unresolved_notes_coverage_blocks(client):
    tc, db, _ = client
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


def test_reviewer_resolved_coverage_row_does_not_block(client):
    tc, db, _ = client
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


def test_unavailable_notes_inventory_blocks(client):
    tc, db, _ = client
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


def test_conflict_outside_the_fill_warns_but_does_not_block(client):
    """A conflict on a row this fill can't write (SOCIE isn't filled yet)
    shouldn't stop a legitimate SOFP fill — but it shouldn't be silent."""
    tc, db, _ = client
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


def test_value_on_presentation_heading_blocks_as_quarantined(client):
    tc, db, _ = client
    run_id = _make_run(db)
    conn = sqlite3.connect(str(db))
    try:
        heading_uuid = conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE canonical_label = 'Statement of financial position' "
            "AND kind = 'ABSTRACT' LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, updated_at) "
            "VALUES (?, ?, 'CY', 'Company', 123, 'observed', 't')",
            (run_id, heading_uuid),
        )
        conn.commit()
    finally:
        conn.close()

    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()

    assert body["ok"] is False
    blocker = next(
        item for item in body["blockers"]
        if item["code"] == "invalid_targets_quarantined"
    )
    assert blocker["count"] == 1


def test_empty_field_manifest_never_reports_filing_ready(client):
    tc, db, _ = client
    run_id = _make_run(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DELETE FROM template_slots")
        conn.execute("DELETE FROM template_manifest_exceptions")
        conn.commit()
    finally:
        conn.close()

    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()

    assert body["ok"] is False
    assert body["field_semantics"]["readiness"] == "needs_review"
    assert any(
        item["code"] == "template_catalog_missing"
        for item in body["blockers"]
    )


def test_explicit_acknowledgement_overrides_and_is_recorded(client):
    tc, db, _ = client
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


def test_blank_acknowledgement_is_not_an_override(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _open_conflict(db, run_id)
    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch", files=_upload(),
                   data={"strict": "true", "acknowledge_preflight": "   "})
    assert resp.status_code == 409


# ------------------------------------------- peer-review fixes (2026-08-05)

def test_open_notes_review_flag_blocks(client):
    """The prose notes fill into the filing too — an unanswered notes-reviewer
    doubt is the same false green as an unanswered face flag."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO notes_review_flags(run_id, kind, reason, sheet, row, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, "row_collision", "Two notes share row 14",
             "Notes-Listofnotes", 14, "open", "t", "t"))
        conn.commit()
    finally:
        conn.close()
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is False
    assert [b["code"] for b in body["blockers"]] == ["open_notes_review_flags"]
    assert "Notes-Listofnotes" in body["blockers"][0]["examples"][0]


def test_answered_notes_review_flag_does_not_block(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO notes_review_flags(run_id, kind, reason, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (run_id, "row_collision", "answered already", "resolved",
             "t", "t"))
        conn.commit()
    finally:
        conn.close()
    assert tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()["ok"]


def _set_integrity_mode(db, run_id, mode):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE runs SET notes_integrity_mode = ? WHERE id = ?",
                     (mode, run_id))
        conn.commit()
    finally:
        conn.close()


def _store_integrity_verdict(db, run_id, *, status, requires_review,
                             mode="enforce"):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO notes_integrity_runs(run_id, generation_id, status, "
            "mode, requires_review, blocks_unresolved, tables_unresolved, "
            "pages_expected, pages_processed, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, 1, status, mode, 1 if requires_review else 0,
             3, 1, 0, 0, "t"))
        conn.commit()
    finally:
        conn.close()


def test_enforce_mode_with_no_verdict_blocks(client):
    """Failure to assess is never proof of no loss (gotcha #31) — an enforce
    run with NO stored verdict is a missing assessment, not a pass."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _set_integrity_mode(db, run_id, "enforce")
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is False
    assert [b["code"] for b in body["blockers"]] == ["notes_integrity_missing"]


def test_enforce_mode_needs_review_verdict_blocks(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _set_integrity_mode(db, run_id, "enforce")
    _store_integrity_verdict(db, run_id, status="needs_review",
                             requires_review=True)
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is False
    assert [b["code"] for b in body["blockers"]] == [
        "notes_integrity_needs_review"]
    assert "3 block(s)" in body["blockers"][0]["examples"][0]


def test_enforce_mode_clean_verdict_passes(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _set_integrity_mode(db, run_id, "enforce")
    _store_integrity_verdict(db, run_id, status="complete",
                             requires_review=False)
    assert tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()["ok"]


def test_shadow_mode_needs_review_warns_but_does_not_block(client):
    """Shadow computes the identical verdict but is contractually not allowed
    to change outcomes (gotcha #31) — so it warns, never blocks."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _set_integrity_mode(db, run_id, "shadow")
    _store_integrity_verdict(db, run_id, status="needs_review",
                             requires_review=True, mode="shadow")
    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is True
    assert [w["code"] for w in body["warnings"]] == [
        "notes_integrity_shadow_needs_review"]


def test_legacy_run_without_integrity_mode_asserts_nothing(client):
    """A pre-feature run (mode NULL) must not be blocked by a table it
    predates — legacy, off, and a real verdict are three different facts."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _store_integrity_verdict(db, run_id, status="needs_review",
                             requires_review=True)
    assert tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()["ok"]


# --------------------------------------- snapshot-consistent conflicts

def test_verdict_follows_the_doc_snapshot_not_a_second_read(client):
    """Concurrent-edit contract (peer review, 2026-08-05): the preflight
    verdict must describe the SAME fact revision as the fill doc, so a
    reviewer editing a conflict mid-request cannot produce a receipt whose
    facts and verdict disagree."""
    from mtool.exporter import build_fill_doc
    from mtool.preflight import evaluate_preflight, written_keys_from_doc

    _, db, srv = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)

    # Snapshot taken while the run is clean...
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    assert doc["meta"]["conflicts"] == []

    # ...then a reviewer flips a fact to 'conflict' AFTER the snapshot.
    _open_conflict(db, run_id)

    # Evaluated from the doc's own snapshot: still clean — consistent with
    # the writes the doc actually carries.
    from_snapshot = evaluate_preflight(
        db, run_id, filing_standard="mfrs", filing_level="company",
        written_keys=written_keys_from_doc(doc),
        conflicts=doc["meta"]["conflicts"])
    assert from_snapshot["ok"] is True

    # A FRESH evaluation (no doc to stay consistent with) sees the edit.
    fresh = evaluate_preflight(
        db, run_id, filing_standard="mfrs", filing_level="company",
        written_keys=written_keys_from_doc(doc))
    assert fresh["ok"] is False


def test_doc_snapshot_carries_conflicts_when_present(client):
    from mtool.exporter import build_fill_doc

    _, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _open_conflict(db, run_id)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    assert len(doc["meta"]["conflicts"]) == 1
    entry = doc["meta"]["conflicts"][0]
    assert {"canonical_label", "render_sheet", "period",
            "entity_scope", "kind"} <= set(entry)


# ---------------------------------------------------------------------------
# Incomplete face statements (run-84 finding, 2026-08-05)
# ---------------------------------------------------------------------------
#
# A face agent that stops early — the iteration cap, a turn timeout, a cancel —
# has usually already projected part of its figures into run_concept_facts, and
# the merge picks its scratch workbook up off disk regardless of status. None of
# the other blockers notice: those figures are not in conflict, they are merely
# incomplete. Run 84 filed-ready in exactly that state, with SOCF capped at 40
# turns and its partial cash-flow figures in the run.


def _add_face_agent(db, run_id, statement_type, status, error_type=None,
                    variant="CuNonCu"):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, variant, status, "
            "error_type, started_at) VALUES (?,?,?,?,?,?)",
            (run_id, statement_type, variant, status, error_type,
             "2026-08-05T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


def test_capped_face_statement_blocks_the_fill(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _add_face_agent(db, run_id, "SOFP", "succeeded")
    _add_face_agent(db, run_id, "SOCF", "failed",
                    error_type="iteration_capped", variant="Indirect")

    resp = tc.post(f"/api/runs/{run_id}/mtool-fill/patch", files=_upload(),
                   data={"strict": "true"})
    assert resp.status_code == 409, resp.text
    preflight = resp.json()["detail"]["preflight"]
    codes = [b["code"] for b in preflight["blockers"]]
    assert "incomplete_face_statements" in codes
    blocker = preflight["blockers"][codes.index("incomplete_face_statements")]
    assert blocker["count"] == 1
    # Names the statement and says why, in words an operator can act on.
    assert any("SOCF" in ex for ex in blocker["examples"]), blocker["examples"]
    assert any("step budget" in ex for ex in blocker["examples"]), \
        blocker["examples"]


def test_cancelled_face_statement_blocks_the_fill(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _add_face_agent(db, run_id, "SOCIE", "cancelled", error_type="cancelled")

    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is False
    assert "incomplete_face_statements" in [b["code"] for b in body["blockers"]]


def test_skipped_face_statement_does_not_block(client):
    """`skipped` is a NotPrepared variant with no template to fill — a
    legitimate non-outcome, not an unfinished statement."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _add_face_agent(db, run_id, "SOCI", "skipped")
    _add_face_agent(db, run_id, "SOFP", "completed_with_errors")

    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert body["ok"] is True, body["blockers"]


def test_incomplete_notes_agent_does_not_block_the_face_fill(client):
    """The blocker is scoped to the five face statements. Notes have their own
    coverage gate; a failed notes template must not be reported as an
    unfinished face statement."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed_leaves(db, run_id)
    _add_face_agent(db, run_id, "NOTES_LIST_OF_NOTES", "failed",
                    error_type="iteration_capped")

    body = tc.get(f"/api/runs/{run_id}/mtool-fill/preflight").json()
    assert "incomplete_face_statements" not in [
        b["code"] for b in body["blockers"]]
