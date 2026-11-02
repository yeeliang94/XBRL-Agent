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
from concept_model.sign_effect import describe_overall_effect, overall_effects
from concept_model.prior_year import (
    SOURCE,
    check_results,
    compare_value,
    import_workbook,
    inspect_workbook,
    guidance,
    validate_export_source,
)
from mtool.preflight import evaluate_preflight
from tests._human_file_fixture import (
    SOFP,
    add_run,
    filled_file,
    import_template_file,
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


def preflight_codes(db, run):
    report = evaluate_preflight(db, run, filing_standard="mfrs", filing_level="company")
    return {item["code"] for item in report["blockers"]}


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
    assert "prior_year_comparatives_unassessed" in preflight_codes(db, run)
    # Formula totals are canonical comparatives too, not blank workbook inputs.
    with sqlite3.connect(db) as conn:
        total_uid, total_value = conn.execute(
            "SELECT f.concept_uuid,f.value FROM run_concept_facts f JOIN concept_nodes n USING(concept_uuid) "
            "WHERE f.run_id=? AND f.period='PY' AND f.source='cascade' AND n.kind='COMPUTED' AND f.value!=0 LIMIT 1",
            (run,),
        ).fetchone()
    assert compare_value(db, run, SOFP, total_uid, total_value, "PDF page 8: total") == "Comparative agrees."
    assert "differs" in compare_value(db, run, SOFP, total_uid, total_value + 7, "PDF page 8: revised total")
    failed_total = next(r for r in check_results(db, run) if r.actual == total_value + 7 and r.status == "failed")
    assert failed_total.expected == total_value
    assert "blank" not in failed_total.message
    compare_value(db, run, SOFP, total_uid, total_value, "PDF page 8: total")
    assert "differs" in compare_value(
        db, run, SOFP, facts[0][0], facts[0][2] + 5, "PDF page 8: restated comparative"
    )
    assert any(r.status == "failed" for r in check_results(db, run))
    assert "prior_year_comparatives_differ" in preflight_codes(db, run)
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
    assert "prior_year_comparatives_differ" not in preflight_codes(db, run)
    assert "1 differences accepted" in next(
        r.message for r in check_results(db, run) if r.name == "Prior-year comparative coverage"
    )
    compare_value(db, run, SOFP, facts[0][0], facts[0][2] + 6, "PDF page 8: restated again")
    assert any(r.status == "failed" for r in check_results(db, run))

    # A comparative the workbook leaves blank cannot be filed in this mode, so
    # it is named; a printed zero against a blank is not. Ordinary blank
    # template rows alone raise no coverage warning.
    with sqlite3.connect(db) as conn:
        blank_leaf = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE template_id=? AND kind='LEAF' AND concept_uuid NOT IN "
            "(SELECT concept_uuid FROM run_concept_facts WHERE run_id=?) LIMIT 1",
            (SOFP, run),
        ).fetchone()[0]
        conn.execute(
            "UPDATE runs SET run_config_json=json_set(run_config_json,'$.prior_year_summary',"
            "json('{\"blank_slots\": 300, \"unresolved_slots\": 0, \"not_in_file\": [], \"period_issues\": []}')) WHERE id=?",
            (run,),
        )
    assert "No imported value" in compare_value(db, run, SOFP, blank_leaf, 0, "PDF page 8: dash")
    assert "will not be filed" in compare_value(db, run, SOFP, blank_leaf, 500, "PDF page 8: 2024 column")
    assert any("leaves blank" in r.message for r in check_results(db, run))
    # A corrected reading of the report withdraws the earlier failure.
    assert "No imported value" in compare_value(db, run, SOFP, blank_leaf, 0, "PDF page 8: dash, rechecked")
    assert not any("leaves blank" in r.message for r in check_results(db, run))
    compare_value(db, run, SOFP, blank_leaf, 500, "PDF page 8: 2024 column")
    # A call for a field outside the agent's template leaves the failure in place.
    assert "No imported value" in compare_value(db, run, "other-template", blank_leaf, 500, "PDF page 8")
    results = check_results(db, run)
    assert any(r.status == "failed" and "leaves blank" in r.message for r in results)
    assert not any(r.name == "Prior-year import coverage" for r in results)

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
            == 3  # restated input, blank-in-workbook input, and calculated total
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


def test_stopped_automatic_layout_run_still_accepts_its_saved_workbook(tmp_path):
    # A stopped statement has no finished agent row naming its automatic layout.
    db, run, session_dir, config, workbook, _ = setup_file(tmp_path)
    automatic = {**config, "variants": {}}
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE runs SET run_config_json=? WHERE id=?", (json.dumps(automatic), run)
        )
    import_workbook(db, run, session_dir, automatic)
    with sqlite3.connect(db) as conn:
        validate_export_source(conn, run, workbook)
        stored = json.loads(
            conn.execute("SELECT run_config_json FROM runs WHERE id=?", (run,)).fetchone()[0]
        )
    assert stored["variants"] == {}


def test_unreadable_assessment_requires_review_instead_of_failing_preparation(tmp_path):
    db, run, session_dir, config, _, _ = setup_file(tmp_path)
    import_workbook(db, run, session_dir, config)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE runs SET run_config_json='{' WHERE id=?", (run,))
    assert "prior_year_comparatives_unassessed" in preflight_codes(db, run)


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

    # This run's figure replaces an older CY value; one without a figure is kept and named.
    from eval.human_file import read_human_file
    from openpyxl import load_workbook

    with sqlite3.connect(db) as conn:
        read = read_human_file(conn, run, workbook, "units", include_notes=False)
    cy_cell = {
        f["concept_uuid"]: (f["source_sheet"], f["source_cell"])
        for f in read.facts if f["period"] == "CY"
    }
    written_cell, stale_cell = cy_cell[facts[0][0]], cy_cell[facts[1][0]]
    zero_cell = cy_cell[facts[2][0]]
    replacement = tmp_path / "stale-current-year.xlsx"
    wb = load_workbook(workbook)
    wb[written_cell[0]][written_cell[1]] = 111
    wb[stale_cell[0]][stale_cell[1]] = 999999
    wb[zero_cell[0]][zero_cell[1]] = 0
    wb.save(replacement)
    wb.close()
    replaced = client.post(
        f"/api/runs/{run}/mtool-fill/patch",
        files={"template": ("replacement.xlsx", replacement.read_bytes())},
        data={"fill_notes": "false"},
    )
    assert replaced.status_code == 200, replaced.text
    preservation = replaced.json()["prior_year_preservation"]
    assert preservation["preserved_inputs"] == len(facts)
    assert {(c["sheet"], c["cell"], c["value"]) for c in preservation["current_year_values_kept"]} == {
        (*stale_cell, 999999), (*zero_cell, 0)
    }
    assert replaced.json()["status"] == "degraded"
    draft = tmp_path / "replaced.xlsx"
    draft.write_bytes(client.get(
        replaced.json()["download_url"],
        params={"acknowledge_degraded": "Reviewed test fixture"},
    ).content)
    wb = load_workbook(draft)
    assert wb[written_cell[0]][written_cell[1]].value == 777
    assert wb[stale_cell[0]][stale_cell[1]].value == 999999
    assert wb[zero_cell[0]][zero_cell[1]].value == 0
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


def test_upload_checks_the_workbook_against_scouts_suggested_layout(tmp_path, monkeypatch):
    import server
    from scout.infopack import Infopack, StatementPageRef
    from statement_types import StatementType

    db = make_sofp_db(tmp_path)
    liquidity = import_template_file(db, tmp_path, "02-SOFP-OrderOfLiquidity.xlsx")
    source_run = add_run(db, sofp_config("OrderOfLiquidity"))
    with sqlite3.connect(db) as conn:
        leaves = [r[0] for r in conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE template_id=? AND kind='LEAF' "
            "ORDER BY render_row LIMIT 2", (liquidity,))]
    workbook = filled_file(
        db, source_run, [(u, "PY", 900.0) for u in leaves], tmp_path,
        "02-SOFP-OrderOfLiquidity.xlsx",
    )
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-test")
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    client = TestClient(server.app)
    scout = json.loads(Infopack(
        toc_page=1,
        page_offset=0,
        statements={StatementType.SOFP: StatementPageRef(
            variant_suggestion="OrderOfLiquidity", face_page=3, note_pages=[], confidence="HIGH",
        )},
    ).to_json())

    def upload(config):
        return client.post(
            "/api/prior-year/session",
            files={"file": ("rolled.xlsx", workbook.read_bytes())},
            data={"unit": "units", "config": json.dumps(config)},
        )

    automatic = upload({**sofp_config(), "variants": {}, "infopack": scout})
    assert automatic.status_code == 200, automatic.text
    assert automatic.json()["summary"]["inputs"] == len(leaves)
    # A layout the operator chose still outranks the suggestion.
    assert upload({**sofp_config("CuNonCu"), "infopack": scout}).status_code == 422
    invalid = upload({**sofp_config(), "variants": {}, "infopack": {"statements": {"nope": {}}}})
    assert invalid.status_code == 422 and "document analysis" in invalid.text


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


def test_import_ignores_current_year_contents_and_refuses_wrong_comparative_dates(
    tmp_path,
):
    from openpyxl import load_workbook

    db, run, session_dir, config, workbook, facts = setup_file(tmp_path)
    with sqlite3.connect(db) as conn:
        inputs, _ = inspect_workbook(conn, config, workbook, "units")
    fact = inputs[0]
    wb = load_workbook(workbook)
    row = fact["source_cell"][1:]
    # Opening balance, explicit zero, linked formula and unreadable text.
    for value, expected in ((123, 1), (0, 1), ("=1+1", 0), ("n/a", 0), ("=#REF!", 0)):
        wb[fact["source_sheet"]]["B" + row] = value
        current = tmp_path / "current.xlsx"
        wb.save(current)
        with sqlite3.connect(db) as conn:
            imported, summary = inspect_workbook(conn, config, current, "units")
        assert {(f["concept_uuid"], f["period"], f["value"]) for f in imported} == {
            (f["concept_uuid"], "PY", f["value"]) for f in inputs
        }
        assert summary["current_year_values"] == expected
    with sqlite3.connect(db) as conn:
        wb[fact["source_sheet"]]["B" + row] = None
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


@pytest.mark.parametrize("component,disagrees", [(None, False), (250, False), (200, True)])
def test_native_subtotal_inputs_are_preserved_and_verified(tmp_path, component, disagrees):
    """A typed subtotal remains canonical and is checked even without its breakdown."""
    from openpyxl import Workbook, load_workbook
    from concept_model.bootstrap import _import_one
    from concept_model.prior_year import validate_export_source, verify_preservation
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
    members = ["IssuedCapitalMember", "OtherReservesMember", "CapitalReserveMember"]
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
    ws["E13"], ws["F13"], ws["G13"] = 1000, 250, component
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

    assert summary["inputs"] == 2 + (component is not None)
    assert summary["aggregate_inputs"] == 1
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT value,children_status,evidence FROM run_concept_facts WHERE run_id=? AND source=?",
            (run, SOURCE),
        ).fetchall()
        subtotal = next(r for r in rows if json.loads(r[2])["cell"] == "F13")
        assert subtotal[:2] == (250, "aggregate_only")
        conflicts = conn.execute(
            "SELECT residual FROM run_concept_conflicts WHERE run_id=? AND period='PY' "
            "AND kind='parent_child_disagree' AND status='open'", (run,),
        ).fetchall()
        assert conflicts == ([(50,)] if disagrees else [])
        validate_export_source(conn, run, workbook)
        assert verify_preservation(conn, run, workbook, workbook)["preserved_inputs"] == len(rows)

        wb = load_workbook(workbook)
        wb["SOCIE"]["F13"] = 999
        changed = tmp_path / "changed.xlsx"
        wb.save(changed)
        wb.close()
        with pytest.raises(ValueError, match="prior-year inputs differ"):
            validate_export_source(conn, run, changed)
        with pytest.raises(ValueError, match="values changed during preparation"):
            verify_preservation(conn, run, workbook, changed)

    with sqlite3.connect(db) as conn:
        template_id = conn.execute(
            "SELECT n.template_id FROM run_concept_facts f JOIN concept_nodes n USING(concept_uuid) "
            "WHERE f.run_id=? AND f.source=? LIMIT 1", (run, SOURCE),
        ).fetchone()[0]
        if disagrees:
            # Warnings for other instances still belong to the discarded attempt.
            for period, scope, dims in (("CY", "Company", ""), ("PY", "Group", ""), ("PY", "Company", '{"axis":"other"}')):
                conn.execute(
                    "INSERT INTO run_concept_conflicts(run_id,concept_uuid,period,entity_scope,dimension_key,kind,residual,detail,status,created_at) "
                    "SELECT run_id,concept_uuid,?,?,?,kind,residual,detail,status,created_at "
                    "FROM run_concept_conflicts WHERE run_id=? AND kind='parent_child_disagree' AND period='PY' "
                    "AND entity_scope='Company' AND dimension_key='' LIMIT 1",
                    (period, scope, dims, run),
                )
    clear_facts_for_template(db, run, template_id)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT residual FROM run_concept_conflicts WHERE run_id=? AND period='PY' "
            "AND kind='parent_child_disagree' AND status='open'", (run,),
        ).fetchall() == ([(50,)] if disagrees else [])
        assert conn.execute(
            "SELECT COUNT(*) FROM run_concept_conflicts WHERE run_id=? AND kind='parent_child_disagree'",
            (run,),
        ).fetchone()[0] == int(disagrees)


@pytest.mark.parametrize("period", ["CY", "PY"])
@pytest.mark.parametrize("with_components", [False, True])
def test_typed_linear_subtotals_are_inputs_not_formula_results(tmp_path, period, with_components):
    from openpyxl import load_workbook
    from concept_model.prior_year import validate_export_source, verify_preservation

    db, run, session_dir, config, workbook, _ = setup_file(tmp_path)
    import_workbook(db, run, session_dir, config)
    with sqlite3.connect(db) as conn:
        uid, amount, sheet, row, col = conn.execute(
            "SELECT f.concept_uuid,f.value,t.target_sheet,t.target_row,t.target_col "
            "FROM run_concept_facts f JOIN concept_nodes n USING(concept_uuid) "
            "JOIN concept_targets t USING(concept_uuid) "
            "WHERE f.run_id=? AND f.period='PY' AND f.source='cascade' AND f.value!=0 "
            "AND n.kind='COMPUTED' AND t.period=? AND t.entity_scope='Company' LIMIT 1",
            (run, period),
        ).fetchone()
    typed = amount + 17
    wb = load_workbook(workbook)
    if not with_components:
        with sqlite3.connect(db) as conn:
            for source_sheet, source_row, source_col in conn.execute(
                "SELECT t.target_sheet,t.target_row,t.target_col FROM concept_targets t "
                "JOIN run_concept_facts f USING(concept_uuid,period,entity_scope) "
                "WHERE f.run_id=? AND f.source=?", (run, SOURCE),
            ):
                wb[source_sheet][f"{source_col}{source_row}"] = None
    wb[sheet][f"{col}{row}"] = typed
    workbook = tmp_path / "typed-total.xlsx"
    wb.save(workbook)
    wb.close()
    if period == "CY":
        with sqlite3.connect(db) as conn:
            if with_components:
                _, summary = inspect_workbook(conn, config, workbook, "units")
                assert summary["current_year_values"] == 1
            else:
                # Current-year values cannot stand in for missing comparatives.
                with pytest.raises(ValueError, match="No figures"):
                    inspect_workbook(conn, config, workbook, "units")
        return

    token = hashlib.sha256(workbook.read_bytes()).hexdigest()
    retained = session_dir / "prior_year" / f"{token}.xlsx"
    retained.write_bytes(workbook.read_bytes())
    retained.with_suffix(".json").write_text(json.dumps({"filename": workbook.name, "unit": "units"}))
    config["prior_year_workbook"] = token
    run = add_run(db, config, "running")
    import_workbook(db, run, session_dir, config)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT value,children_status,source FROM run_concept_facts "
            "WHERE run_id=? AND concept_uuid=? AND period='PY' AND entity_scope='Company' AND dimension_key=''",
            (run, uid),
        ).fetchone() == (typed, "aggregate_only", SOURCE)
        assert conn.execute(
            "SELECT residual FROM run_concept_conflicts WHERE run_id=? AND concept_uuid=? "
            "AND kind='parent_child_disagree'", (run, uid),
        ).fetchone() == ((17,) if with_components else None)
        validate_export_source(conn, run, workbook)
        assert verify_preservation(conn, run, workbook, workbook)["status"] == "ok"
    assert compare_value(db, run, SOFP, uid, typed, "PDF page 8: typed total") == "Comparative agrees."


def test_typed_current_year_subtotal_takes_the_runs_figure(tmp_path, monkeypatch):
    import server
    from concept_model.cascade import recompute_after_turn
    from openpyxl import load_workbook

    db, run, session_dir, config, workbook, facts = setup_file(tmp_path)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-test")
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    import_workbook(db, run, session_dir, config)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        apply_fact(conn, run, FactWrite(concept_uuid=facts[0][0], value=777))
        conn.execute("UPDATE runs SET status='completed',session_id='session' WHERE id=?", (run,))
    recompute_after_turn(db, run)
    with sqlite3.connect(db) as conn:
        total, sheet, row, col = conn.execute(
            "SELECT f.value,t.target_sheet,t.target_row,t.target_col FROM run_concept_facts f "
            "JOIN concept_nodes n USING(concept_uuid) JOIN concept_targets t USING(concept_uuid,period,entity_scope) "
            "WHERE f.run_id=? AND f.period='CY' AND f.source='cascade' AND n.kind='COMPUTED' AND f.value!=0 LIMIT 1",
            (run,),
        ).fetchone()
    wb = load_workbook(workbook)
    wb[sheet][f"{col}{row}"] = total + 5000
    typed = tmp_path / "typed-current-total.xlsx"
    wb.save(typed)
    wb.close()
    client = TestClient(server.app)
    result = client.post(
        f"/api/runs/{run}/mtool-fill/patch",
        files={"template": ("typed.xlsx", typed.read_bytes())},
        data={"fill_notes": "false"},
    )
    assert result.status_code == 200, result.text
    assert not result.json()["mismatches"]
    assert result.json()["prior_year_preservation"]["current_year_values_kept"] == []
    draft = tmp_path / "typed-result.xlsx"
    draft.write_bytes(client.get(
        result.json()["download_url"], params={"acknowledge_degraded": "Reviewed test fixture"},
    ).content)
    wb = load_workbook(draft)
    assert wb[sheet][f"{col}{row}"].value == total
    wb.close()


@pytest.mark.parametrize("standard", ["MFRS", "MPERS"])
@pytest.mark.parametrize("level", ["Company", "Group"])
@pytest.mark.parametrize("template", ["SOCF-Indirect", "SOCIE", "SOCI-NetOfTax", "Notes-IssuedCapital"])
def test_current_year_contents_never_change_what_each_template_imports(tmp_path, standard, level, template):
    from concept_model.importer import import_company_targets, import_template
    from concept_model.parser import parse_template
    from db.schema import init_db
    from eval.human_file import read_human_file, typed_current_year_cells
    from openpyxl import load_workbook

    path = next(Path(f"XBRL-template-{standard}/{level}").glob(f"*-{template}.xlsx"))
    db = tmp_path / "audit.db"
    init_db(db)
    tree = parse_template(str(path))
    payload = tmp_path / "tree.json"
    payload.write_text(json.dumps(tree.to_json()), encoding="utf-8")
    import_template(db, payload)
    import_company_targets(db, tree.template_id)
    shape = {"standard": standard.lower(), "level": level.lower(), "statements": {tree.template_id: path.stem},
             "notes_templates": set(), "denomination": "units", "first_financial_statements": False}
    wb = load_workbook(path)
    with sqlite3.connect(db) as conn:
        targets = conn.execute(
            "SELECT t.target_sheet,t.target_col,t.target_row,t.period FROM concept_targets t "
            "JOIN concept_nodes n USING(concept_uuid) WHERE n.template_id=? ORDER BY t.target_row",
            (tree.template_id,),
        ).fetchall()
        empty = [(s, f"{c}{r}", p) for s, c, r, p in targets if wb[s][f"{c}{r}"].value is None]
        opening, zero = [(s, a) for s, a, p in empty if p == "CY"][:2]
        comparative = next((s, a) for s, a, p in empty if p == "PY")
        wb[opening[0]][opening[1]] = 124765
        wb[zero[0]][zero[1]] = 0
        wb[comparative[0]][comparative[1]] = 1000
        rolled = tmp_path / "rolled.xlsx"
        wb.save(rolled)
        wb.close()
        conn.row_factory = sqlite3.Row
        read = read_human_file(conn, 0, rolled, "units", shape=shape, periods=("PY",),
                               include_notes=False, include_typed_totals=True)
        assert {f["period"] for f in read.facts} == {"PY"}
        assert any(f["value"] == 1000 for f in read.facts)
        found = {(c["sheet"], c["cell"]): c["value"] for c in typed_current_year_cells(conn, rolled, shape)}
    assert found == {opening: 124765, zero: 0}


def test_sign_only_comparative_difference_names_the_overall_sign(tmp_path):
    """Run 211: workbook +70 against a submitted -70 on a row subtracted overall."""
    from correction.reviewer_agent import _format_trace, trace_cascade_source

    db = make_sofp_db(tmp_path)
    socf = import_template_file(db, tmp_path, "07-SOCF-Indirect.xlsx")
    run = add_run(db, {**sofp_config(), "prior_year_workbook": "a" * 64}, "running")
    label = "(Gain) loss on disposal of property, plant and equipment"
    with sqlite3.connect(db) as conn:
        uid = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE template_id=? AND canonical_label=?",
            (socf, label),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO run_concept_facts(run_id,concept_uuid,period,entity_scope,dimension_key,value,value_status,source) "
            "VALUES (?,?,'PY','Company','',70,'observed',?)",
            (run, uid, SOURCE),
        )
    wrong = compare_value(db, run, socf, uid, -70, "PDF page 11: gain (70)")
    assert "Same amount, opposite sign" in wrong
    assert "SUBTRACTED overall (coefficient -1)" in wrong
    assert "effect of -70" in wrong
    failed = [r for r in check_results(db, run) if r.status == "failed"]
    assert len(failed) == 1 and "Same amount, opposite sign" in failed[0].message
    assert "prior_year_comparatives_differ" in preflight_codes(db, run)
    # A different amount is an ordinary difference, not a sign question.
    assert "opposite sign" not in compare_value(db, run, socf, uid, -71, "PDF page 11: (71)")
    # Back to the sign-only case: resubmitting the workbook's figure records the
    # reasoning but cannot clear it, so a real sign error stays in front of a person.
    # A different figure in between does not erase the dispute.
    assert "stays open until a person decides" in compare_value(db, run, socf, uid, 70, "Reread")
    compare_value(db, run, socf, uid, -70, "PDF page 11: gain (70)")
    kept = compare_value(db, run, socf, uid, 70, "Subtracted overall, so +70 deducts the gain")
    assert "stays open until a person decides" in kept
    assert "prior_year_comparatives_differ" in preflight_codes(db, run)
    failed = [r for r in check_results(db, run) if r.status == "failed"]
    assert failed[0].actual == -70 and "PDF page 11: gain (70)" in failed[0].message
    assert "Subtracted overall, so +70 deducts the gain" in failed[0].message
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=?", (run, uid)
        ).fetchone()[0] == 70
        conn.execute(
            "UPDATE run_concept_conflicts SET status='dismissed' WHERE run_id=? AND kind='prior_year_comparative'",
            (run,),
        )
    assert "prior_year_comparatives_differ" not in preflight_codes(db, run)
    # The reviewer sees the same fact when it traces the row, which has no children.
    trace = _format_trace(trace_cascade_source(db, run, concept_uuid=uid, period="PY"))
    assert "SUBTRACTED overall (coefficient -1)" in trace
    # The overall sign agrees with the template formula walk the guidance uses.
    from prompts._sign_conventions import _overall_coefficients, _template_sign_metadata
    from tests._human_file_fixture import TEMPLATES

    sheet = _template_sign_metadata(TEMPLATES / "07-SOCF-Indirect.xlsx").sheets[0]
    parent_rows = dict(sheet.parent_rows)
    with sqlite3.connect(db) as conn:
        for row, concept in conn.execute(
            "SELECT render_row,concept_uuid FROM concept_nodes WHERE template_id=? AND kind='LEAF'", (socf,),
        ).fetchall():
            tops, _ = overall_effects(conn, concept)
            assert {sign for sign, _label in tops} == _overall_coefficients(parent_rows, row), row


def test_overall_effect_names_every_total_and_keeps_large_figures_exact(tmp_path):
    db = make_sofp_db(tmp_path)
    socie = import_template_file(db, tmp_path, "09-SOCIE.xlsx")
    socf = import_template_file(db, tmp_path, "07-SOCF-Indirect.xlsx")
    with sqlite3.connect(db) as conn:
        dividends = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE template_id=? AND canonical_label='Dividends paid'",
            (socie,),
        ).fetchall()
        texts = [describe_overall_effect(conn, uid) for uid, in dividends]
        assert any("ADDED into `" in text and "SUBTRACTED from `" in text for text in texts)
        gain = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE template_id=? AND canonical_label LIKE '(Gain) loss on disposal of property%'",
            (socf,),
        ).fetchone()[0]
        assert "A stored 1,234,567 has an effect of -1,234,567" in describe_overall_effect(conn, gain, 1234567)
