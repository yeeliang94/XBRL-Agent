"""Phase 5 — reviewer-tab API endpoints (Steps 11-14).

GET /review, POST /flags/{id}/answer, POST /re-review,
POST /revert-to-original. Modeled on tests/test_concepts_routes.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


_TEMPLATE = "mfrs-company-sofp-test-v1"
PARENT = "00000000-0000-0000-0000-0000000000aa"
LEAF1 = "00000000-0000-0000-0000-0000000000b1"


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
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
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    run_id = int(conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES ('2026-05-29T00:00:00Z', 'x.pdf', 'completed', '2026-05-29Z')"
    ).lastrowid)
    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path, shape) "
        "VALUES (?, 'x.xlsx', 'linear')", (_TEMPLATE,))
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) VALUES "
        "(?, ?, 'COMPUTED', 'Total assets', 'SOFP', 10, 'B')", (PARENT, _TEMPLATE))
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) VALUES "
        "(?, ?, 'LEAF', 'Cash', 'SOFP', 5, 'B')", (LEAF1, _TEMPLATE))
    conn.execute(
        "INSERT INTO concept_edges(parent_uuid, child_uuid, coefficient) "
        "VALUES (?, ?, 1.0)", (PARENT, LEAF1))
    conn.commit()
    conn.close()
    return TestClient(srv.app), db, run_id, srv


def _wf(db, run_id, uid, value, **kw):
    from concept_model.facts_api import write_fact, FactWrite
    write_fact(db, run_id, FactWrite(
        concept_uuid=uid, period="CY", entity_scope="Company", value=value,
        value_status="observed", source=kw.get("source", "x"),
        evidence=kw.get("evidence"), actor=kw.get("actor", "agent")))


def _await_rereview(tc, run_id, timeout_s: float = 10.0) -> dict:
    """Poll the background re-review status endpoint until it reports done.

    The POST only launches the pass (it can take minutes in production); the
    outcome arrives via GET /re-review/status. Each TestClient call re-enters
    the portal loop, giving the background task time to run the FunctionModel
    pass to completion.
    """
    import time
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        s = tc.get(f"/api/runs/{run_id}/re-review/status")
        assert s.status_code == 200, s.text
        body = s.json()
        if body.get("status") == "done":
            return body
        time.sleep(0.05)
    raise AssertionError("re-review did not finish within the timeout")


# ---------------------------------------------------------------------------
# Step 11 — GET /review
# ---------------------------------------------------------------------------


def test_get_review_returns_diff_flag_and_crosschecks(client):
    tc, db, run_id, srv = client
    _wf(db, run_id, LEAF1, 100.0)
    from concept_model.versioning import snapshot_facts
    snapshot_facts(db, run_id)
    _wf(db, run_id, LEAF1, 120.0, source="fix", evidence="p12", actor="reviewer")
    # A flag + a cross-check row.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO reviewer_flags(run_id, category, reasoning, status, "
        "created_at) VALUES (?, 'stuck', 'cannot reconcile', 'open', '2026Z')",
        (run_id,))
    conn.execute(
        "INSERT INTO cross_checks(run_id, check_name, status, message) "
        "VALUES (?, 'sofp_balance', 'failed', 'off by 20')", (run_id,))
    conn.commit()
    conn.close()

    r = tc.get(f"/api/runs/{run_id}/review")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_reviewer_version"] is True
    assert len(body["diff"]) == 1
    assert body["diff"][0]["original"] == 100.0
    assert body["diff"][0]["current"] == 120.0
    assert len(body["flags"]) == 1 and body["flags"][0]["category"] == "stuck"
    assert len(body["cross_checks"]) == 1
    assert body["cross_checks"][0]["status"] == "failed"


def test_get_review_404_for_unknown_run(client):
    tc, _db, _run_id, _srv = client
    assert tc.get("/api/runs/99999/review").status_code == 404


def test_get_review_no_reviewer_version_when_no_snapshot(client):
    tc, db, run_id, _srv = client
    _wf(db, run_id, LEAF1, 100.0)
    body = tc.get(f"/api/runs/{run_id}/review").json()
    assert body["has_reviewer_version"] is False
    assert body["diff"] == []


# ---------------------------------------------------------------------------
# Step 12 — POST /flags/{id}/answer
# ---------------------------------------------------------------------------


def test_answer_flag_updates_status_and_text(client):
    tc, db, run_id, _srv = client
    conn = sqlite3.connect(str(db))
    fid = int(conn.execute(
        "INSERT INTO reviewer_flags(run_id, category, reasoning, status, "
        "created_at) VALUES (?, 'stuck', 'r', 'open', '2026Z')",
        (run_id,)).lastrowid)
    conn.commit()
    conn.close()

    r = tc.post(f"/api/runs/{run_id}/flags/{fid}/answer",
                json={"human_answer": "The PPE note is on page 44."})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "answered"
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT status, human_answer FROM reviewer_flags WHERE id = ?", (fid,)
    ).fetchone()
    conn.close()
    assert row[0] == "answered" and "page 44" in row[1]


def test_answer_flag_404_for_unknown_flag(client):
    tc, _db, run_id, _srv = client
    r = tc.post(f"/api/runs/{run_id}/flags/9999/answer",
                json={"human_answer": "x"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Step 13 — POST /re-review
# ---------------------------------------------------------------------------


def _patch_for_rereview(srv, monkeypatch):
    """Stub the heavy machinery so the re-review endpoint runs the real
    reviewer pass over a FunctionModel without touching disk/LLM."""
    def _fix_scripted(messages, info: AgentInfo) -> ModelResponse:
        for m in messages:
            for part in getattr(m, "parts", []):
                if part.part_kind == "tool-return":
                    return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="apply_fix",
            args={"concept_uuid": LEAF1, "value": 130.0,
                  "reason": "re-review fix", "evidence": "p12"})])

    monkeypatch.setattr(srv, "_create_proxy_model",
                        lambda *a, **k: FunctionModel(_fix_scripted))
    # No failing cross-checks needed to invoke (we seed a conflict instead);
    # avoid the disk-bound re-check + re-export.
    monkeypatch.setattr(srv, "_recheck_from_facts", lambda rid: [])
    monkeypatch.setattr(srv, "_reexport_remerge_durable", lambda rid: True)


def test_re_review_starts_pass_and_preserves_original_snapshot(client, monkeypatch):
    tc, db, run_id, srv = client
    _wf(db, run_id, LEAF1, 100.0)
    from concept_model.versioning import snapshot_facts
    snapshot_facts(db, run_id)  # ORIGINAL = 100
    _wf(db, run_id, LEAF1, 120.0, actor="reviewer")  # a prior reviewer state
    # Seed an open conflict so the reviewer pass is invoked.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO run_concept_conflicts(run_id, concept_uuid, period, "
        "entity_scope, kind, detail, status, created_at) VALUES "
        "(?, ?, 'CY', 'Company', 'partial_state', 'x', 'open', '2026Z')",
        (run_id, PARENT))
    conn.commit()
    conn.close()

    _patch_for_rereview(srv, monkeypatch)
    r = tc.post(f"/api/runs/{run_id}/re-review", json={"guidance": "look at p44"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"
    done = _await_rereview(tc, run_id)
    assert done["invoked"] is True

    # The ORIGINAL snapshot (100) is preserved — re-review must not re-snapshot.
    conn = sqlite3.connect(str(db))
    snap = conn.execute(
        "SELECT value FROM run_fact_snapshots WHERE run_id=? AND concept_uuid=?",
        (run_id, LEAF1)).fetchone()[0]
    conn.close()
    assert snap == 100.0


def test_refresh_persisted_cross_checks_replaces_rows(client, monkeypatch):
    """Peer-review P1: manual re-review / revert must refresh the persisted
    cross_checks so the Review tab + a later re-review don't read stale rows.
    The helper replaces (not appends) the run's rows."""
    tc, db, run_id, srv = client
    # A STALE failing row, as the original pipeline persisted it.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cross_checks(run_id, check_name, status, message) "
        "VALUES (?, 'sofp_assets_balance', 'failed', 'off by 20')", (run_id,))
    conn.commit()
    conn.close()
    # The re-check now reports the check passing (the reviewer fixed the fact).
    monkeypatch.setattr(srv, "_recheck_from_facts", lambda rid: [
        {"name": "sofp_assets_balance", "status": "passed", "expected": 170.0,
         "actual": 170.0, "diff": 0.0, "tolerance": 1.0, "message": "ok",
         "target_sheet": "SOFP", "target_row": 10}])

    assert srv._refresh_persisted_cross_checks(run_id) is True

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT check_name, status FROM cross_checks WHERE run_id=?", (run_id,),
    ).fetchall()
    conn.close()
    # Replaced, not appended: exactly one row, now passing.
    assert len(rows) == 1
    assert rows[0]["status"] == "passed"


def test_refresh_persisted_cross_checks_noop_when_nothing_to_check(client, monkeypatch):
    """When the re-check has nothing to run (no facts / no succeeded
    statements), the refresh is a no-op and leaves existing rows untouched."""
    tc, db, run_id, srv = client
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cross_checks(run_id, check_name, status) "
        "VALUES (?, 'x', 'failed')", (run_id,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(srv, "_recheck_from_facts", lambda rid: None)
    assert srv._refresh_persisted_cross_checks(run_id) is False
    conn = sqlite3.connect(str(db))
    n = conn.execute(
        "SELECT COUNT(*) FROM cross_checks WHERE run_id=?", (run_id,)).fetchone()[0]
    conn.close()
    assert n == 1  # untouched


def _set_status(db, run_id, status):
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
    conn.commit()
    conn.close()


def _run_status(db, run_id):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
    finally:
        conn.close()


def test_safe_downgrade_completed_to_errors_on_failure(client):
    """A 'completed' run with a failing check after refresh is downgraded."""
    tc, db, run_id, srv = client
    _set_status(db, run_id, "completed")
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cross_checks(run_id, check_name, status) "
        "VALUES (?, 'sofp_assets_balance', 'failed')", (run_id,))
    conn.commit()
    conn.close()
    assert srv._safe_downgrade_run_status(run_id) is True
    assert _run_status(db, run_id) == "completed_with_errors"


def test_safe_downgrade_noop_when_clean(client):
    """A clean 'completed' run (no failures / conflicts) is left completed."""
    tc, db, run_id, srv = client
    _set_status(db, run_id, "completed")
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cross_checks(run_id, check_name, status) "
        "VALUES (?, 'sofp_assets_balance', 'passed')", (run_id,))
    conn.commit()
    conn.close()
    assert srv._safe_downgrade_run_status(run_id) is False
    assert _run_status(db, run_id) == "completed"


def test_safe_downgrade_never_upgrades_or_touches_non_completed(client):
    """It only ever downgrades a 'completed' run — never promotes a
    completed_with_errors / failed run (which could hide a failed agent)."""
    tc, db, run_id, srv = client
    # All checks pass, no conflicts...
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cross_checks(run_id, check_name, status) "
        "VALUES (?, 'sofp_assets_balance', 'passed')", (run_id,))
    conn.commit()
    conn.close()
    # ...but the run is completed_with_errors / failed: leave it as-is.
    for status in ("completed_with_errors", "failed", "correction_exhausted"):
        _set_status(db, run_id, status)
        assert srv._safe_downgrade_run_status(run_id) is False
        assert _run_status(db, run_id) == status


def test_safe_downgrade_on_open_conflict(client):
    """A real open reconciliation conflict downgrades a completed run, but the
    correction_exhausted sentinel (surfaced via its own status) does not."""
    tc, db, run_id, srv = client
    # The correction_exhausted sentinel alone must NOT downgrade.
    _set_status(db, run_id, "completed")
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO run_concept_conflicts(run_id, concept_uuid, period, "
        "entity_scope, kind, detail, status, created_at) VALUES "
        "(?, '', 'CY', 'Company', 'correction_exhausted', 'x', 'open', '2026Z')",
        (run_id,))
    conn.commit()
    conn.close()
    assert srv._safe_downgrade_run_status(run_id) is False
    assert _run_status(db, run_id) == "completed"

    # A real open conflict downgrades.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO run_concept_conflicts(run_id, concept_uuid, period, "
        "entity_scope, kind, detail, status, created_at) VALUES "
        "(?, ?, 'CY', 'Company', 'partial_state', 'x', 'open', '2026Z')",
        (run_id, PARENT))
    conn.commit()
    conn.close()
    assert srv._safe_downgrade_run_status(run_id) is True
    assert _run_status(db, run_id) == "completed_with_errors"


def test_re_review_status_is_idle_before_any_pass(client):
    """The status endpoint reports 'idle' when no pass has been launched."""
    tc, db, run_id, srv = client
    r = tc.get(f"/api/runs/{run_id}/re-review/status")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "idle"}


def test_active_flag_guidance_includes_open_and_answered(client):
    """Re-review guidance must carry still-OPEN prior flags, not just
    answered ones, so the reviewer keeps its stuck/dispute context
    (peer-review LOW)."""
    tc, db, run_id, srv = client
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO reviewer_flags(run_id, category, reasoning, status, "
        "created_at) VALUES (?, 'stuck', 'cannot reconcile PPE', 'open', '2026Z')",
        (run_id,))
    conn.execute(
        "INSERT INTO reviewer_flags(run_id, category, reasoning, status, "
        "human_answer, created_at) VALUES (?, 'disputes_prior', 'extraction "
        "erred', 'answered', 'use page 44', '2026Z')", (run_id,))
    conn.execute(
        "INSERT INTO reviewer_flags(run_id, category, reasoning, status, "
        "created_at) VALUES (?, 'stuck', 'old dismissed', 'dismissed', '2026Z')",
        (run_id,))
    conn.commit()
    conn.close()
    text = srv._active_flag_guidance(run_id)
    assert "cannot reconcile PPE" in text  # open flag included
    assert "use page 44" in text           # answered flag's human answer
    assert "old dismissed" not in text     # dismissed excluded


def test_re_review_without_guidance_also_starts(client, monkeypatch):
    tc, db, run_id, srv = client
    _wf(db, run_id, LEAF1, 100.0)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO run_concept_conflicts(run_id, concept_uuid, period, "
        "entity_scope, kind, detail, status, created_at) VALUES "
        "(?, ?, 'CY', 'Company', 'partial_state', 'x', 'open', '2026Z')",
        (run_id, PARENT))
    conn.commit()
    conn.close()
    _patch_for_rereview(srv, monkeypatch)
    r = tc.post(f"/api/runs/{run_id}/re-review", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"
    done = _await_rereview(tc, run_id)
    assert done["invoked"] is True


# ---------------------------------------------------------------------------
# Step 14 — POST /revert-to-original
# ---------------------------------------------------------------------------


def test_revert_restores_original_and_clears_reviewer_version(client, monkeypatch):
    tc, db, run_id, srv = client
    _wf(db, run_id, LEAF1, 100.0)
    # Cascade BEFORE snapshot so the parent total is part of the original
    # backup — mirrors the real pipeline, where the snapshot is taken after
    # extraction + cascade have populated parents.
    from concept_model.cascade import recompute_after_turn
    from concept_model.versioning import snapshot_facts
    recompute_after_turn(db, run_id)
    snapshot_facts(db, run_id)
    _wf(db, run_id, LEAF1, 120.0, source="fix", evidence="p12", actor="reviewer")
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO reviewer_flags(run_id, category, reasoning, status, "
        "created_at) VALUES (?, 'stuck', 'r', 'open', '2026Z')", (run_id,))
    conn.commit()
    conn.close()

    # Before revert: a reviewer version exists.
    assert tc.get(f"/api/runs/{run_id}/review").json()["has_reviewer_version"] is True

    monkeypatch.setattr(srv, "_reexport_remerge_durable", lambda rid: True)
    r = tc.post(f"/api/runs/{run_id}/revert-to-original")
    assert r.status_code == 200, r.text
    assert r.json()["reverted"] is True

    # Facts restored to original.
    conn = sqlite3.connect(str(db))
    val = conn.execute(
        "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=?",
        (run_id, LEAF1)).fetchone()[0]
    conn.close()
    assert val == 100.0

    # After revert: GET /review shows no reviewer version (empty diff +
    # dismissed flags) even though the snapshot is kept for re-review. The
    # dismissed flag must NOT appear in the main list (peer-review MEDIUM —
    # a stale answerable flag under a "No reviewer changes" header).
    body = tc.get(f"/api/runs/{run_id}/review").json()
    assert body["has_reviewer_version"] is False
    assert body["diff"] == []
    assert body["flags"] == []


def test_get_review_excludes_dismissed_and_resolved_flags(client):
    tc, db, run_id, _srv = client
    conn = sqlite3.connect(str(db))
    for cat, status in [("stuck", "open"), ("stuck", "dismissed"),
                        ("disputes_prior", "resolved"), ("stuck", "answered")]:
        conn.execute(
            "INSERT INTO reviewer_flags(run_id, category, reasoning, status, "
            "created_at) VALUES (?, ?, 'r', ?, '2026Z')", (run_id, cat, status))
    conn.commit()
    conn.close()
    flags = tc.get(f"/api/runs/{run_id}/review").json()["flags"]
    statuses = sorted(f["status"] for f in flags)
    assert statuses == ["answered", "open"]  # dismissed + resolved excluded


def test_re_review_picks_up_stored_failed_crosscheck(client, monkeypatch):
    """Regression (run #146): a stored failed cross-check must drive re-review
    even with zero open conflicts — sourcing from the cross_checks table, not a
    recheck that drops checks whose statement failed to extract."""
    tc, db, run_id, srv = client
    _wf(db, run_id, LEAF1, 100.0)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cross_checks(run_id, check_name, status, expected, "
        "actual, diff, message) VALUES (?, 'sopl_to_socie_profit', 'failed', "
        "-62023.0, -20678.0, 41345.0, 'profit mismatch')", (run_id,))
    conn.commit()
    conn.close()

    captured = {}

    async def _capture_pass(**kwargs):
        captured["failed"] = kwargs.get("failed_checks")
        captured["conflicts"] = kwargs.get("conflicts")
        return {"invoked": True, "writes_performed": 0, "flags_raised": 1, "error": None}

    monkeypatch.setattr(srv, "_run_reviewer_pass", _capture_pass)
    monkeypatch.setattr(srv, "_create_proxy_model", lambda *a, **k: object())
    r = tc.post(f"/api/runs/{run_id}/re-review", json={})
    assert r.status_code == 200, r.text
    done = _await_rereview(tc, run_id)
    names = [getattr(c, "name", None) for c in (captured["failed"] or [])]
    assert "sopl_to_socie_profit" in names
    # The reviewer was actually invoked (not the "nothing to review" no-op).
    assert done["invoked"] is True


def test_re_review_passes_model_override(client, monkeypatch):
    """The Review-tab model picker sends `model`; the endpoint must build the
    reviewer with it and echo it back."""
    tc, db, run_id, srv = client
    _wf(db, run_id, LEAF1, 100.0)
    captured = {}

    def _fake_create(model_name, proxy_url, api_key):
        captured["model"] = model_name
        return object()

    async def _noop_pass(**kwargs):
        return {"invoked": True, "writes_performed": 0, "flags_raised": 0, "error": None}

    monkeypatch.setattr(srv, "_create_proxy_model", _fake_create)
    monkeypatch.setattr(srv, "_recheck_from_facts", lambda rid: [])
    monkeypatch.setattr(srv, "_run_reviewer_pass", _noop_pass)
    r = tc.post(f"/api/runs/{run_id}/re-review", json={"model": "google.gemini-3"})
    assert r.status_code == 200, r.text
    # POST echoes the model name synchronously; the build happens in the pass.
    assert r.json()["model"] == "google.gemini-3"
    _await_rereview(tc, run_id)
    assert captured["model"] == "google.gemini-3"


def test_re_review_reports_ok_false_on_reviewer_error(client, monkeypatch):
    """A reviewer pass that fails (snapshot/construction/exhaustion) must
    surface ok:false + the error, not a phantom success (peer-review HIGH)."""
    tc, db, run_id, srv = client
    _wf(db, run_id, LEAF1, 100.0)

    async def _failing_pass(**kwargs):
        return {"invoked": True, "writes_performed": 0, "flags_raised": 0,
                "error": "snapshot failed: boom"}

    monkeypatch.setattr(srv, "_run_reviewer_pass", _failing_pass)
    monkeypatch.setattr(srv, "_recheck_from_facts", lambda rid: [])
    monkeypatch.setattr(srv, "_create_proxy_model", lambda *a, **k: object())
    r = tc.post(f"/api/runs/{run_id}/re-review", json={})
    assert r.status_code == 200
    body = _await_rereview(tc, run_id)
    assert body["ok"] is False
    assert "snapshot failed" in body["error"]


def test_revert_409_when_no_snapshot(client):
    tc, db, run_id, _srv = client
    _wf(db, run_id, LEAF1, 100.0)
    r = tc.post(f"/api/runs/{run_id}/revert-to-original")
    assert r.status_code == 409
