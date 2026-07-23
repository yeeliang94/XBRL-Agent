"""Phase 4A drills — stage-level resume rules + transactional staging.

Each test is one of the plan's pre-registered failure drills:

* reuse/rerun selection (one failed agent; Stop-All partial success;
  succeeded-but-factless; never-started),
* refusals (live parent, missing source document, config mismatch),
* staging (fact provenance, lineage + source hash, child-owned rows),
* cross-family protection (an MPERS fact can never ride a MFRS reuse),
* rollback (a mid-staging failure leaves ZERO copied facts and no lineage).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from db.schema import init_db
from recovery.stage_resume import (
    build_resume_plan,
    compute_source_sha256,
    stage_resume,
    template_prefix_for,
)


SOFP_LEAF = "00000000-0000-0000-0000-00000000c001"
SOPL_LEAF = "00000000-0000-0000-0000-00000000c002"
MPERS_LEAF = "00000000-0000-0000-0000-00000000c003"


def _seed_parent(tmp_path, *, status="failed", sofp_agent="succeeded",
                 sopl_agent="failed", with_pdf=True, sofp_facts=True):
    db = tmp_path / "resume.db"
    init_db(db)
    out_dir = tmp_path / "parent_out"
    out_dir.mkdir(exist_ok=True)
    if with_pdf:
        (out_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake body")
    conn = sqlite3.connect(str(db))
    cfg = {"statements": ["SOFP", "SOPL"], "filing_standard": "mfrs",
           "filing_level": "company", "variants": {"SOFP": "CuNonCu"}}
    run_id = int(conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, output_dir, "
        "run_config_json) VALUES ('2026-07-23T00:00:00Z','x.pdf',?,?,?)",
        (status, str(out_dir), json.dumps(cfg))).lastrowid)
    for tid in ("mfrs-company-sofp-cunoncu-v1", "mfrs-company-sopl-function-v1",
                "mpers-company-sofp-cunoncu-v1"):
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, shape) "
            "VALUES (?, 'x.xlsx', 'linear')", (tid,))
    for uid, tid, label in (
            (SOFP_LEAF, "mfrs-company-sofp-cunoncu-v1", "Cash"),
            (SOPL_LEAF, "mfrs-company-sopl-function-v1", "Revenue"),
            (MPERS_LEAF, "mpers-company-sofp-cunoncu-v1", "Cash (MPERS)")):
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, ?, 'LEAF', ?, 'SOFP', 5, 'B')", (uid, tid, label))
    if sofp_agent:
        conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, variant, model, "
            "status, started_at) VALUES (?,?,?,?,?,?)",
            (run_id, "SOFP", "CuNonCu", "gpt-test", sofp_agent,
             "2026-07-23T00:00:00Z"))
    if sopl_agent:
        conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, variant, model, "
            "status, started_at) VALUES (?,?,?,?,?,?)",
            (run_id, "SOPL", "Function", "gpt-test", sopl_agent,
             "2026-07-23T00:00:00Z"))
    if sofp_facts:
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, source, evidence, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, SOFP_LEAF, "CY", "Company", 100.0, "observed",
             "extraction", "page 12: Cash 100", "2026-07-23T00:00:00Z"))
        # A same-run MPERS-family fact (pathological, but exactly what the
        # cross-family drill must prove never rides along).
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, source, evidence, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, MPERS_LEAF, "CY", "Company", 999.0, "observed",
             "extraction", "page 1", "2026-07-23T00:00:00Z"))
    conn.commit()
    conn.close()
    return db, run_id, out_dir


def _make_child(db) -> int:
    conn = sqlite3.connect(str(db))
    child = int(conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status) "
        "VALUES ('2026-07-23T01:00:00Z','x.pdf','running')").lastrowid)
    conn.commit()
    conn.close()
    return child


def test_reuses_succeeded_reruns_failed(tmp_path):
    db, run_id, _ = _seed_parent(tmp_path)
    plan = build_resume_plan(db, run_id)
    assert plan.refusal is None
    assert [d.statement for d in plan.reused] == ["SOFP"]
    assert [d.statement for d in plan.rerun] == ["SOPL"]
    assert plan.reused[0].variant == "CuNonCu"


def test_stop_all_partial_reuses_exactly_terminal_successes(tmp_path):
    db, run_id, _ = _seed_parent(
        tmp_path, status="aborted", sopl_agent=None)
    plan = build_resume_plan(db, run_id)
    assert [d.statement for d in plan.reused] == ["SOFP"]
    rerun = {d.statement: d.reason for d in plan.rerun}
    assert rerun == {"SOPL": "never started in the parent"}


def test_live_parent_refused(tmp_path):
    db, run_id, _ = _seed_parent(tmp_path, status="running")
    plan = build_resume_plan(db, run_id)
    assert plan.refusal and "not resumable" in plan.refusal


def test_missing_source_pdf_refused(tmp_path):
    db, run_id, _ = _seed_parent(tmp_path, with_pdf=False)
    plan = build_resume_plan(db, run_id)
    assert plan.refusal and "uploaded.pdf is missing" in plan.refusal


def test_config_mismatch_refused(tmp_path):
    db, run_id, _ = _seed_parent(tmp_path)
    plan = build_resume_plan(db, run_id, expected_standard="mpers")
    assert plan.refusal and "standard mismatch" in plan.refusal
    plan = build_resume_plan(db, run_id, expected_level="group")
    assert plan.refusal and "level mismatch" in plan.refusal


def test_succeeded_without_facts_reruns(tmp_path):
    db, run_id, _ = _seed_parent(tmp_path, sofp_facts=False)
    plan = build_resume_plan(db, run_id)
    assert plan.reused == []
    reasons = {d.statement: d.reason for d in plan.rerun}
    assert "no canonical facts" in reasons["SOFP"]


def test_reviewed_state_is_labelled(tmp_path):
    db, run_id, _ = _seed_parent(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO run_fact_snapshots(run_id, concept_uuid, period, "
        "entity_scope, value, value_status, children_status, source, "
        "evidence, snapshot_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id, SOFP_LEAF, "CY", "Company", 90.0, "observed", None,
         "extraction", "page 12", "2026-07-23T00:30:00Z"))
    conn.commit()
    conn.close()
    plan = build_resume_plan(db, run_id)
    assert plan.parent_had_reviewer_writes is True
    assert "reviewed state" in plan.reused[0].reason


def test_staging_copies_facts_with_provenance_lineage_and_family_scope(
        tmp_path):
    db, run_id, out_dir = _seed_parent(tmp_path)
    # A scratch workbook the staging should carry over.
    (out_dir / "SOFP_filled.xlsx").write_bytes(b"fake xlsx")
    plan = build_resume_plan(db, run_id)
    child = _make_child(db)
    child_dir = tmp_path / "child_out"

    result = stage_resume(db, plan, child, child_dir)

    assert result["copied_facts"] == 1          # the MFRS fact only
    assert result["workbooks_copied"] == ["SOFP"]
    assert (child_dir / "SOFP_filled.xlsx").exists()
    assert result["source_sha256"] == compute_source_sha256(
        out_dir / "uploaded.pdf")

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        facts = conn.execute(
            "SELECT * FROM run_concept_facts WHERE run_id=?",
            (child,)).fetchall()
        assert len(facts) == 1
        assert facts[0]["concept_uuid"] == SOFP_LEAF, (
            "the MPERS-family fact must never ride an MFRS reuse")
        assert facts[0]["source"] == f"resume:run_{run_id}:extraction"
        assert facts[0]["evidence"] == "page 12: Cash 100"
        agent = conn.execute(
            "SELECT * FROM run_agents WHERE run_id=? AND "
            "statement_type='SOFP'", (child,)).fetchone()
        assert agent["status"] == "succeeded"
        assert agent["model"] == f"reused:run_{run_id}"
        lineage = conn.execute(
            "SELECT * FROM run_lineage WHERE child_run_id=?",
            (child,)).fetchone()
        assert lineage["parent_run_id"] == run_id
        assert json.loads(lineage["reused_statements"]) == ["SOFP/CuNonCu"]
        assert json.loads(lineage["rerun_statements"]) == ["SOPL/Function"]
        # Parent untouched (immutability).
        assert conn.execute(
            "SELECT count(*) FROM run_concept_facts WHERE run_id=?",
            (run_id,)).fetchone()[0] == 2
    finally:
        conn.close()


def test_staging_failure_rolls_back_everything(tmp_path):
    db, run_id, _ = _seed_parent(tmp_path)
    plan = build_resume_plan(db, run_id)
    child = _make_child(db)
    # Force the LAST staging insert (lineage) to fail after facts copied.
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE run_lineage")
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.OperationalError):
        stage_resume(db, plan, child, tmp_path / "child_out")

    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute(
            "SELECT count(*) FROM run_concept_facts WHERE run_id=?",
            (child,)).fetchone()[0] == 0, "partial staging must roll back"
        assert conn.execute(
            "SELECT count(*) FROM run_agents WHERE run_id=?",
            (child,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_refused_plan_cannot_be_staged(tmp_path):
    db, run_id, _ = _seed_parent(tmp_path, status="running")
    plan = build_resume_plan(db, run_id)
    with pytest.raises(ValueError):
        stage_resume(db, plan, 999, tmp_path / "child_out")


def test_template_prefix_scopes_family_and_statement():
    assert template_prefix_for("MFRS", "Company", "SOFP") == \
        "mfrs-company-sofp-"
    assert template_prefix_for("mpers", "group", "SOCIE") == \
        "mpers-group-socie-"


def test_cli_resume_is_preview_only_without_flag(tmp_path, monkeypatch):
    """run.py --resume-from prints the plan but launches NOTHING while
    XBRL_STAGE_RESUME is off — no child run row, no staged facts."""
    import run as run_mod
    import server

    db, run_id, _ = _seed_parent(tmp_path)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", str(db))
    monkeypatch.setattr(run_mod, "ENV_FILE", tmp_path / "no.env")
    monkeypatch.delenv("XBRL_STAGE_RESUME", raising=False)

    result = run_mod.run_resume(run_id, output_dir=str(tmp_path / "out"))

    assert result.success is False
    assert any("preview only" in e for e in result.errors)
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute(
            "SELECT count(*) FROM runs").fetchone()[0] == 1, (
            "preview must not create a child run")
        assert conn.execute(
            "SELECT count(*) FROM run_lineage").fetchone()[0] == 0
    finally:
        conn.close()


def test_cli_resume_refuses_bad_parent(tmp_path, monkeypatch):
    import run as run_mod
    import server

    db, run_id, _ = _seed_parent(tmp_path, status="running")
    monkeypatch.setattr(server, "AUDIT_DB_PATH", str(db))
    monkeypatch.setattr(run_mod, "ENV_FILE", tmp_path / "no.env")

    result = run_mod.run_resume(run_id, output_dir=str(tmp_path / "out"))
    assert result.success is False
    assert any("not resumable" in e for e in result.errors)


def test_parser_accepts_resume_from():
    import run as run_mod
    args = run_mod.build_parser().parse_args(["--resume-from", "42"])
    assert args.resume_from == 42
    args = run_mod.build_parser().parse_args([])
    assert args.resume_from is None
