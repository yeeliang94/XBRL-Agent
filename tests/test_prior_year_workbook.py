"""Rolled-forward inputs guide extraction, survive retries and remain unchanged on export."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from concept_model.facts_api import FactWrite, apply_fact, clear_facts_for_template
from concept_model.prior_year import (
    SOURCE,
    check_results,
    compare_value,
    import_workbook,
    inspect_workbook,
    guidance,
)
from tests._human_file_fixture import (
    SOFP,
    add_run,
    filled_file,
    leaf_facts,
    make_sofp_db,
    sofp_config,
)


def setup_file(tmp_path):
    db = make_sofp_db(tmp_path)
    config = sofp_config()
    source_run = add_run(db, config)
    facts = [(u, p, v) for u, p, v in leaf_facts(db) if p == "PY"]
    workbook = filled_file(db, source_run, facts, tmp_path)
    session_dir = tmp_path / "session"
    root = session_dir / "prior_year"
    root.mkdir(parents=True)
    token = hashlib.sha256(workbook.read_bytes()).hexdigest()
    path = root / f"{token}.xlsx"
    path.write_bytes(workbook.read_bytes())
    path.with_suffix(".json").write_text(
        json.dumps({"filename": "rolled.xlsx", "unit": "units"})
    )
    config["prior_year_workbook"] = token
    run = add_run(db, config, "running")
    return db, run, session_dir, config, workbook, facts


def test_import_guidance_retry_protection_and_comparative_checks(tmp_path):
    db, run, session_dir, config, workbook, facts = setup_file(tmp_path)
    summary = import_workbook(db, run, session_dir, config)
    assert summary["inputs"] == len(facts)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute(
            "SELECT COUNT(*) FROM run_concept_facts WHERE run_id=? AND source=?",
            (run, SOURCE),
        ).fetchone()[0] == len(facts)
        for uid, period, value in facts:
            with pytest.raises(HTTPException) as refused:
                apply_fact(
                    conn, run, FactWrite(concept_uuid=uid, period="PY", value=value + 1)
                )
            assert "preserved" in refused.value.detail
        with pytest.raises(HTTPException):
            apply_fact(
                conn, run, FactWrite(concept_uuid=facts[0][0], value=123, source=SOURCE)
            )
        apply_fact(conn, run, FactWrite(concept_uuid=facts[0][0], value=123))
    text = guidance(db, run, SOFP)
    assert facts[0][0] in text
    assert str(facts[0][2]) in text
    assert any(r.status == "warning" for r in check_results(db, run))
    assert "differs" in compare_value(
        db, run, SOFP, facts[0][0], facts[0][2] + 5, "PDF page 8: restated comparative"
    )
    assert any(r.status == "failed" for r in check_results(db, run))
    # A difference dismissed in review stays accepted when the same figure is
    # rechecked; a new difference reopens it.
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE run_concept_conflicts SET status='dismissed' WHERE run_id=? AND kind='prior_year_comparative'",
            (run,),
        )
    assert "already accepted" in compare_value(
        db, run, SOFP, facts[0][0], facts[0][2] + 5, "PDF page 8: restated comparative"
    )
    assert not any(r.status == "failed" for r in check_results(db, run))
    assert "1 differences accepted" in next(
        r.message for r in check_results(db, run) if r.name == "Prior-year comparative coverage"
    )
    compare_value(db, run, SOFP, facts[0][0], facts[0][2] + 6, "PDF page 8: restated again")
    assert any(r.status == "failed" for r in check_results(db, run))

    # A malformed tool call is reported back to the agent instead of ending its run.
    from concept_model.prior_year import register_comparison_tool

    class Agent:
        def tool_plain(self, fn):
            self.check = fn
            return fn

    agent = Agent()
    register_comparison_tool(agent, db, run, SOFP)
    assert "not recorded" in agent.check(facts[0][0], facts[0][2], " ")
    clear_facts_for_template(db, run, SOFP)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM run_concept_facts WHERE run_id=? AND source=?",
            (run, SOURCE),
        ).fetchone()[0] == len(facts)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM run_concept_conflicts WHERE run_id=? AND kind='prior_year_comparative'",
                (run,),
            ).fetchone()[0]
            == 1
        )
    for uid, _, value in facts:
        compare_value(db, run, SOFP, uid, value, "PDF page 8: comparative")
    assert (
        next(
            r
            for r in check_results(db, run)
            if r.name == "Prior-year comparative coverage"
        ).status
        == "passed"
    )


def test_prior_only_reader_preserves_zero_and_rejects_first_statements(tmp_path):
    db, run, session_dir, config, workbook, facts = setup_file(tmp_path)
    with sqlite3.connect(db) as conn:
        inputs, summary = inspect_workbook(conn, config, workbook, "units")
        assert {(f["concept_uuid"], f["value"]) for f in inputs} == {
            (u, v) for u, _, v in facts
        }
        assert all(f["period"] == "PY" and f["source_cell"] for f in inputs)
        with pytest.raises(ValueError, match="no prior-year"):
            inspect_workbook(
                conn, {**config, "first_financial_statements": True}, workbook, "units"
            )
    path = session_dir / "prior_year" / f"{config['prior_year_workbook']}.xlsx"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        import_workbook(db, run, session_dir, config)


def test_upload_import_current_year_patch_and_saved_source(tmp_path, monkeypatch):
    import server

    db, run, session_dir, config, workbook, facts = setup_file(tmp_path)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-test")
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    client = TestClient(server.app)
    macro = client.post(
        "/api/prior-year/session",
        files={"file": ("rolled.xlsm", workbook.read_bytes())},
        data={"unit": "units", "config": json.dumps(config)},
    )
    assert macro.status_code == 422 and ".xlsx" in macro.json()["detail"]
    response = client.post(
        "/api/prior-year/session",
        files={"file": ("rolled.xlsx", workbook.read_bytes())},
        data={"unit": "units", "config": json.dumps(config)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["inputs"] == len(facts)
    # Draft autosave must retain the workbook choice across refresh/start.
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE runs SET status='draft' WHERE id=?", (run,))
    saved = client.patch(
        f"/api/runs/{run}",
        json={
            "prior_year_workbook": config["prior_year_workbook"],
            "prior_year_layout": "b" * 64,
        },
    )
    assert saved.status_code == 200, saved.text
    with sqlite3.connect(db) as conn:
        stored = json.loads(
            conn.execute(
                "SELECT run_config_json FROM runs WHERE id=?", (run,)
            ).fetchone()[0]
        )
        assert stored["prior_year_workbook"] == config["prior_year_workbook"]
        assert stored["prior_year_layout"] == "b" * 64
        conn.execute("UPDATE runs SET status='running' WHERE id=?", (run,))
    import_workbook(db, run, session_dir, config)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        apply_fact(conn, run, FactWrite(concept_uuid=facts[0][0], value=777))
        conn.execute(
            "UPDATE runs SET status='completed',session_id='session' WHERE id=?", (run,)
        )
    source = client.get(f"/api/runs/{run}/prior-year-workbook")
    assert source.status_code == 200
    assert source.content == workbook.read_bytes()
    doc = client.get(f"/api/runs/{run}/mtool-fill").json()
    assert doc["meta"]["preserve_prior_year"]
    assert doc["writes"] and all(w["period"] == "CY" for w in doc["writes"])

    # Matching PY inputs must not admit stale CY values outside canonical facts.
    from eval.human_file import read_human_file
    from openpyxl import load_workbook

    with sqlite3.connect(db) as conn:
        read = read_human_file(conn, run, workbook, "units", include_notes=False)
    untouched_cy = next(
        f for f in read.facts
        if f["period"] == "CY" and f["concept_uuid"] == facts[1][0]
    )
    replacement = tmp_path / "stale-current-year.xlsx"
    wb = load_workbook(workbook)
    for stale_value in (0, 999999):
        wb[untouched_cy["source_sheet"]][untouched_cy["source_cell"]] = stale_value
        wb.save(replacement)
        refused = client.post(
            f"/api/runs/{run}/mtool-fill/patch",
            files={"template": ("replacement.xlsx", replacement.read_bytes())},
            data={"fill_notes": "false"},
        )
        assert refused.status_code == 422, refused.text
        assert "already contains current-year numeric inputs" in refused.text
    wb.close()

    patched = client.post(
        f"/api/runs/{run}/mtool-fill/patch",
        files={"template": ("rolled.xlsx", source.content)},
        data={"fill_notes": "false"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["prior_year_preservation"]["preserved_inputs"] == len(facts)
    result = patched.json()
    output = client.get(
        result["download_url"], params={"acknowledge_degraded": "Reviewed test fixture"}
    )
    assert output.status_code == 200, output.text
    destination = tmp_path / "result.xlsx"
    destination.write_bytes(output.content)
    with sqlite3.connect(db) as conn:
        read = read_human_file(
            conn, run, destination, "units", periods=("PY",), include_notes=False
        )
        imported = [
            f for f in read.facts if f["value"] is not None and not f["calculated"]
        ]
    assert {(f["concept_uuid"], f["value"]) for f in imported} == {
        (u, v) for u, _, v in facts
    }
    # A blank replacement must not silently discard the reviewed comparatives.
    blank = Path("XBRL-template-MFRS/Company/01-SOFP-CuNonCu.xlsx").read_bytes()
    refused = client.post(
        f"/api/runs/{run}/mtool-fill/patch",
        files={"template": ("blank.xlsx", blank)},
        data={"fill_notes": "false"},
    )
    assert refused.status_code == 422
    assert "prior-year" in refused.text


@pytest.mark.parametrize("standard", ["mfrs", "mpers"])
def test_numeric_note_categories_and_explicit_zero_import(tmp_path, standard):
    from openpyxl import Workbook
    from concept_model.bootstrap import _import_one
    from db.schema import init_db
    from notes_types import NotesTemplateType, notes_template_path

    db = tmp_path / "notes.db"
    init_db(db)
    tid = _import_one(
        db,
        notes_template_path(NotesTemplateType.ISSUED_CAPITAL, standard=standard),
        "company",
    )
    config = {
        "filing_standard": standard,
        "filing_level": "company",
        "denomination": "units",
        "statements": [],
        "notes_to_run": ["ISSUED_CAPITAL"],
    }
    prefix = "ifrs-full" if standard == "mfrs" else "ifrs-smes"
    wb = Workbook()
    ws = wb.active
    ws.title = "Notes-Issuedcapital"
    marker = lambda name: f"native.xsd#{prefix}_{name}"
    ws["E18"] = "::".join(
        marker(n)
        for n in [
            "DisclosureOfClassesOfShareCapitalTable",
            "ClassesOfShareCapitalAxis",
            "OrdinarySharesMember",
        ]
    )
    if standard == "mpers":
        ws["E18"] = "::".join(
            [
                marker("DisclosureOfClassesOfShareCapitalTable"),
                marker("ClassesOfShareCapitalAxis"),
                "native.xsd#ssmt-mpers_OrdinarySharesMember",
            ]
        )
    ws["G18"] = ws["E18"].value
    ws["C19"], ws["D19"] = "#LAYOUTSCSR#", "#PRIM#"
    ws["C20"], ws["E20"], ws["G20"] = "#DOM#", "Ordinary shares", "Ordinary shares"
    ws["C24"], ws["E24"], ws["G24"] = "#ENDT#", "31/12/2025", "31/12/2024"
    ws["A31"] = marker("NumberOfSharesIssuedAndFullyPaid")
    ws["D31"] = "*Number of shares issued and fully paid"
    ws["E31"], ws["G31"] = None, 0
    path = tmp_path / "numeric.xlsx"
    wb.save(path)
    wb.close()
    token = hashlib.sha256(path.read_bytes()).hexdigest()
    root = tmp_path / "prior_year"
    root.mkdir()
    destination = root / f"{token}.xlsx"
    destination.write_bytes(path.read_bytes())
    destination.with_suffix(".json").write_text(
        json.dumps({"filename": "numeric.xlsx", "unit": "units"})
    )
    config["prior_year_workbook"] = token
    run = add_run(db, config, "running")
    summary = import_workbook(db, run, tmp_path, config)
    assert summary["inputs"] == 1
    with sqlite3.connect(db) as conn:
        facts = conn.execute(
            "SELECT concept_uuid,period,dimension_key,value FROM run_concept_facts WHERE run_id=? AND source=?",
            (run, SOURCE),
        ).fetchall()
        assert len(facts) == 1
        uid, period, dims, value = facts[0]
        assert period == "PY" and value == 0
        assert "OrdinarySharesMember" in dims
        assert "agrees" in compare_value(
            db,
            run,
            tid,
            uid,
            0,
            "PDF page 8: printed zero",
            dimensions=json.loads(dims),
        )
        with pytest.raises(ValueError, match="units must match"):
            inspect_workbook(
                conn, {**config, "denomination": "thousands"}, path, "units"
            )


@pytest.mark.parametrize("standard", ["mfrs", "mpers"])
def test_group_and_company_comparatives_keep_separate_scopes(tmp_path, standard):
    from concept_model.bootstrap import _import_one
    from db.schema import init_db
    from mtool.exporter import build_fill_doc
    from mtool.template_map import resolve_filing_doc
    from mtool.offline_fill import fill_workbook
    from statement_types import StatementType, template_path

    db = tmp_path / "group.db"
    init_db(db)
    template = template_path(StatementType.SOFP, "CuNonCu", "group", standard)
    tid = _import_one(db, template, "group")
    config = {
        "filing_standard": standard,
        "filing_level": "group",
        "denomination": "units",
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
    }
    seed = add_run(db, config)
    with sqlite3.connect(db) as conn:
        uid = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE template_id=? AND kind='LEAF' AND render_sheet LIKE '%Sub%' LIMIT 1",
            (tid,),
        ).fetchone()[0]
        for scope, value in [("Group", 123), ("Company", 45)]:
            conn.execute(
                "INSERT INTO run_concept_facts(run_id,concept_uuid,period,entity_scope,value,value_status,updated_at) VALUES(?,?,'PY',?,?,'observed','now')",
                (seed, uid, scope, value),
            )
    doc = build_fill_doc(
        db, seed, filing_standard=standard, filing_level="group", denomination="units"
    )
    ready, _ = resolve_filing_doc(str(template), doc)
    path = tmp_path / "group.xlsx"
    assert fill_workbook(str(template), ready, str(path), strict=True)["status"] == "ok"
    with sqlite3.connect(db) as conn:
        inputs, _ = inspect_workbook(conn, config, path, "units")
        assert {(f["entity_scope"], f["period"], f["value"]) for f in inputs} == {
            ("Group", "PY", 123),
            ("Company", "PY", 45),
        }


def test_unknown_layout_requires_confirmation_of_the_exact_column_map(tmp_path):
    from openpyxl import load_workbook
    from concept_model.prior_year import inspect_workbook

    db, run, session_dir, config, workbook, facts = setup_file(tmp_path)
    native = tmp_path / "native.xlsx"
    wb = load_workbook(workbook)
    with sqlite3.connect(db) as conn:
        for sheet, row, primary in conn.execute(
            "SELECT n.render_sheet,n.render_row,sa.primary_concept "
            "FROM concept_nodes n JOIN concept_semantic_addresses sa USING(concept_uuid) "
            "WHERE n.template_id=? AND n.kind IN ('LEAF','COMPUTED')",
            (SOFP,),
        ):
            if primary:
                wb[sheet].cell(row=row, column=7, value="native.xsd#" + primary)
    wb.create_sheet("Unregistered layout")
    wb.save(native)
    wb.close()
    with sqlite3.connect(db) as conn:
        inputs, summary = inspect_workbook(conn, config, native, "units")
    assert summary["requires_confirmation"]
    assert {(f["concept_uuid"], f["value"]) for f in inputs} == {
        (u, v) for u, _, v in facts
    }
    token = hashlib.sha256(native.read_bytes()).hexdigest()
    path = session_dir / "prior_year" / f"{token}.xlsx"
    path.write_bytes(native.read_bytes())
    path.with_suffix(".json").write_text(
        json.dumps({"filename": "native.xlsx", "unit": "units"})
    )
    config["prior_year_workbook"] = token
    with pytest.raises(ValueError, match="Confirm the detected"):
        import_workbook(db, run, session_dir, config)
    config["prior_year_layout"] = summary["layout_digest"]
    imported = import_workbook(db, run, session_dir, config)
    assert imported["inputs"] == len(facts)


@pytest.mark.parametrize(
    "standard,level",
    [("mfrs", "company"), ("mpers", "company"), ("mfrs", "group"), ("mpers", "group")],
)
def test_socie_export_selects_periods_by_identity_instead_of_excel_column(
    tmp_path, standard, level
):
    from concept_model.bootstrap import _import_one
    from db.schema import init_db
    from mtool.exporter import build_fill_doc
    from statement_types import StatementType, template_path

    db = tmp_path / "matrix.db"
    init_db(db)
    tid = _import_one(
        db, template_path(StatementType.SOCIE, "Default", level, standard), level
    )
    config = {
        "filing_standard": standard,
        "filing_level": level,
        "statements": ["SOCIE"],
        "variants": {"SOCIE": "Default"},
        "denomination": "units",
        "prior_year_workbook": "a" * 64,
    }
    run = add_run(db, config)
    with sqlite3.connect(db) as conn:
        targets = conn.execute(
            "SELECT t.concept_uuid,t.period,t.entity_scope,t.target_row,t.target_col FROM concept_targets t JOIN concept_nodes n USING(concept_uuid) "
            "WHERE n.template_id=? AND n.kind='MATRIX_CELL' AND NOT EXISTS(SELECT 1 FROM concept_edges e WHERE e.parent_uuid=n.concept_uuid)",
            (tid,),
        ).fetchall()
        assert any(t[1] == "PY" for t in targets)
        pairs = set()
        for uid, period, scope, row, col in targets:
            if (period, scope) in pairs:
                continue
            pairs.add((period, scope))
            conn.execute(
                "INSERT INTO run_concept_facts(run_id,concept_uuid,period,entity_scope,value,value_status,updated_at) VALUES(?,?,?,?,123,'observed','now')",
                (run, uid, period, scope),
            )
    doc = build_fill_doc(
        db, run, filing_standard=standard, filing_level=level, denomination="units"
    )
    assert doc["writes"] and all(write["period"] == "CY" for write in doc["writes"])
    assert doc["meta"]["preserve_prior_year"]
    assert not doc["meta"]["first_financial_statements"]


def test_export_refuses_a_prior_year_formula_changed_by_current_year_inputs(tmp_path):
    from openpyxl import load_workbook
    from mtool.exporter import build_fill_doc
    from mtool.template_map import resolve_filing_doc
    from mtool.offline_fill import fill_workbook
    from concept_model.prior_year import verify_preservation

    db, run, session_dir, config, workbook, facts = setup_file(tmp_path)
    with sqlite3.connect(db) as conn:
        inputs, _ = inspect_workbook(conn, config, workbook, "units")
    fact = inputs[0]
    colrow = fact["source_cell"]
    assert colrow.startswith("C")
    wb = load_workbook(workbook)
    ws = wb[fact["source_sheet"]]
    ws[colrow] = f"={fact['value']}+B{colrow[1:]}"
    ws["B" + colrow[1:]] = None
    formula = tmp_path / "formula.xlsx"
    wb.save(formula)
    wb.close()
    token = hashlib.sha256(formula.read_bytes()).hexdigest()
    retained = session_dir / "prior_year" / f"{token}.xlsx"
    retained.write_bytes(formula.read_bytes())
    retained.with_suffix(".json").write_text(
        json.dumps({"filename": "formula.xlsx", "unit": "units"})
    )
    config["prior_year_workbook"] = token
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE runs SET run_config_json=? WHERE id=?", (json.dumps(config), run)
        )
    import_workbook(db, run, session_dir, config)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        apply_fact(
            conn,
            run,
            FactWrite(concept_uuid=fact["concept_uuid"], period="CY", value=777),
        )
    doc = build_fill_doc(
        db, run, filing_standard="mfrs", filing_level="company", denomination="units"
    )
    ready, _ = resolve_filing_doc(str(formula), doc)
    after = tmp_path / "after.xlsx"
    assert fill_workbook(str(formula), ready, str(after))["status"] == "ok"
    with sqlite3.connect(db) as conn:
        with pytest.raises(ValueError, match="values changed during preparation"):
            verify_preservation(conn, run, formula, after)


def test_import_validation_failure_leaves_a_terminal_audit_run(tmp_path, monkeypatch):
    import asyncio
    import server
    from pydantic_ai.models.test import TestModel

    db, _, session_dir, config, _, _ = setup_file(tmp_path)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-test")
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(server, "_CANONICAL_BOOTSTRAP_OK", True)
    monkeypatch.setattr(
        server, "_create_proxy_model", lambda *args, **kwargs: TestModel()
    )
    request = server.RunConfigRequest(
        **{**config, "first_financial_statements": True, "use_scout": False}
    )

    async def collect():
        return [
            event
            async for event in server.run_multi_agent_stream(
                "session", session_dir, request, "test-key", "", "test-model"
            )
        ]

    events = asyncio.run(collect())
    assert any(
        e.get("event") == "error" and "prior_year_import_failed" in json.dumps(e)
        for e in events
    )
    with sqlite3.connect(db) as conn:
        run, status = conn.execute(
            "SELECT id,status FROM runs WHERE session_id='session' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert status == "failed"
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM run_agents WHERE run_id=? AND status='running'",
                (run,),
            ).fetchone()[0]
            == 0
        )


def test_import_refuses_existing_current_year_inputs_and_wrong_comparative_dates(
    tmp_path,
):
    from openpyxl import load_workbook

    db, run, session_dir, config, workbook, facts = setup_file(tmp_path)
    with sqlite3.connect(db) as conn:
        inputs, _ = inspect_workbook(conn, config, workbook, "units")
    fact = inputs[0]
    wb = load_workbook(workbook)
    wb[fact["source_sheet"]]["B" + fact["source_cell"][1:]] = 123
    current = tmp_path / "current.xlsx"
    wb.save(current)
    with sqlite3.connect(db) as conn:
        with pytest.raises(ValueError, match="already contains current-year"):
            inspect_workbook(conn, config, current, "units")
        wb[fact["source_sheet"]]["B" + fact["source_cell"][1:]] = None
        for sheet, row, primary in conn.execute(
            "SELECT n.render_sheet,n.render_row,sa.primary_concept FROM concept_nodes n JOIN concept_semantic_addresses sa USING(concept_uuid) WHERE n.template_id=?",
            (SOFP,),
        ):
            if primary:
                wb[sheet].cell(row=row, column=7, value="native.xsd#" + primary)
        for ws in wb:
            ws["A1"] = "#ENDT#"
            ws["B1"] = "31/12/2026"
            ws["C1"] = "31/12/2025"
            ws["A2"] = "#PRIM#"
        wrong = tmp_path / "wrong-date.xlsx"
        wb.save(wrong)
        wb.close()
        with pytest.raises(ValueError, match="date.*does not match"):
            inspect_workbook(
                conn,
                {
                    **config,
                    "infopack": {
                        "reporting_period_cy": "31/12/2025",
                        "reporting_period_py": "31/12/2024",
                    },
                },
                wrong,
                "units",
            )


def test_incomplete_comparative_assessment_remains_a_visible_warning(
    tmp_path, monkeypatch
):
    import asyncio
    import server
    from cross_checks.framework import CrossCheckResult

    def fail():
        raise RuntimeError("assessment unavailable")

    warning = CrossCheckResult(
        name="Prior-year comparative assessment",
        status="warning",
        message="Assessment incomplete",
    )
    results = asyncio.run(
        server._run_notes_advisory_bounded(
            fail, run_id=1, label="prior-year-comparatives", failure_results=[warning]
        )
    )
    assert len(results) == 1 and results[0].status == "warning"


def test_imported_comparatives_cannot_turn_a_no_write_agent_into_success(
    tmp_path,
):
    import asyncio
    import fitz
    from concept_model.bootstrap import _import_one
    from notes.coordinator import _run_single_notes_agent
    from notes_types import NotesTemplateType, notes_template_path
    from pydantic_ai.models.test import TestModel

    db, run, session_dir, config, _, _ = setup_file(tmp_path)
    _import_one(db, notes_template_path(NotesTemplateType.RELATED_PARTY), "company")
    import_workbook(db, run, session_dir, config)
    pdf = tmp_path / "empty.pdf"
    document = fitz.open()
    document.new_page()
    document.save(pdf)
    document.close()
    outcome = asyncio.run(
        _run_single_notes_agent(
            NotesTemplateType.RELATED_PARTY,
            str(pdf),
            [],
            "company",
            TestModel(
                call_tools=[], custom_output_text="Finished without extracting figures."
            ),
            str(tmp_path),
            max_retries=0,
            run_id=run,
            db_path=str(db),
            denomination="units",
        )
    )
    assert outcome.status == "failed"
    assert "without writing" in outcome.error

    # The face agent's scratch workbook is seeded the same way.
    from coordinator import _run_single_agent
    from statement_types import StatementType
    from tests._human_file_fixture import TEMPLATES

    face = asyncio.run(
        _run_single_agent(
            StatementType.SOFP,
            "CuNonCu",
            str(pdf),
            str(TEMPLATES / "01-SOFP-CuNonCu.xlsx"),
            TestModel(
                call_tools=[], custom_output_text="Finished without extracting figures."
            ),
            str(tmp_path),
            run_id=run,
            db_path=str(db),
            denomination="units",
        )
    )
    assert face.status == "failed"
    assert "without writing a workbook" in face.error
    assert (tmp_path / "SOFP_filled.xlsx").exists()


def test_native_subtotal_inputs_are_recalculated_not_imported(tmp_path):
    """mTool types SOCIE other reserves; the canonical model calculates it."""
    from openpyxl import Workbook
    from concept_model.bootstrap import _import_one
    from concept_model.prior_year import validate_export_source
    from db.schema import init_db

    db = tmp_path / "audit.db"
    init_db(db)
    _import_one(db, Path("XBRL-template-MFRS/Company/09-SOCIE.xlsx"), "company")
    config = {
        "filing_standard": "mfrs",
        "filing_level": "company",
        "denomination": "units",
        "statements": ["SOCIE"],
        "variants": {"SOCIE": "Default"},
    }
    x = "full_ifrs-cor_2022-03-24.xsd#ifrs-full_"
    members = ["IssuedCapitalMember", "OtherReservesMember"]
    wb = Workbook()
    ws = wb.active
    ws.title = "SOCIE"
    for top, date in ((2, "31/12/2025"), (10, "31/12/2024")):
        for col, member in enumerate(members, 5):
            ws.cell(top, col).value = "::".join(
                x + n for n in ("StatementOfChangesInEquityTable", "ComponentsOfEquityAxis", member)
            )
            ws.cell(top + 2, col).value = date
        ws.cell(top + 1, 3).value, ws.cell(top + 1, 4).value = "#DOM#", "#PRIM#"
        ws.cell(top + 2, 3).value = "#ENDT#"
        ws.cell(top + 3, 1).value = x + "Equity@periodStartLabel"
        ws.cell(top + 3, 4).value = "*Equity at beginning of period"
    ws["E13"], ws["F13"] = 1000, 250
    workbook = tmp_path / "native.xlsx"
    wb.save(workbook)
    wb.close()
    token = hashlib.sha256(workbook.read_bytes()).hexdigest()
    root = tmp_path / "prior_year"
    root.mkdir()
    (root / f"{token}.xlsx").write_bytes(workbook.read_bytes())
    (root / f"{token}.json").write_text(json.dumps({"filename": "native.xlsx", "unit": "units"}))
    config["prior_year_workbook"] = token
    run = add_run(db, config, "running")

    summary = import_workbook(db, run, tmp_path, config)

    assert (summary["inputs"], summary["recalculated_inputs"]) == (1, 1)
    with sqlite3.connect(db) as conn:
        assert [r[0] for r in conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND source=?", (run, SOURCE)
        )] == [1000]
        # The same file must still be accepted when the mTool draft is prepared.
        validate_export_source(conn, run, workbook)
