"""Report filing boundaries: typed canonical values, review edits and native patching."""
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Protection

from db import repository as repo
from db.schema import init_db
from reports.registry import REPORTS, fields, import_templates, template_path
from reports.repository import save_field, snapshot, StaleReportEdit
from reports.exporter import resolve_reports
from mtool.offline_fill import fill_report_fields, load_workbook_entries, format_value
from mtool.notes_exporter import build_notes_fill_doc


@pytest.fixture
def report_run(tmp_path):
    db = tmp_path / "audit.db"
    init_db(db)
    import_templates(db)
    with repo.db_session(db) as conn:
        rid = repo.create_run(conn, "source.pdf", status="completed", config={
            "filing_standard": "mfrs", "filing_level": "company",
            "reports_to_run": list(REPORTS), "denomination": "units"})
    return db, rid


@pytest.mark.parametrize("standard", ["mfrs", "mpers"])
@pytest.mark.parametrize("level", ["company", "group"])
def test_report_catalogue_is_writable_in_each_exact_filing_family(report_run, standard, level):
    from concept_model.filing_targets import targets_for_template
    for report, count in (("DIRECTORS_REPORT", 22), ("AUDITORS_REPORT", 13)):
        _, targets = targets_for_template(template_path(report, standard, level))
        writable = {t.taxonomy_element_id: t for t in targets if t.writable}
        assert len(writable) == count
        for field in fields(report, standard):
            assert writable[field.concept].value_kind == field.kind
        assert sum(f.kind == "html" for f in fields(report, standard)) == 1


def test_report_values_round_trip_edits_clear_and_stale_saves(report_run):
    db, rid = report_run
    report = "AUDITORS_REPORT"
    identifier = next(f for f in fields(report) if f.concept.endswith("LicenseNumberOfAuditor"))
    narrative = next(f for f in fields(report) if f.kind == "html")
    with repo.db_session(db) as conn:
        save_field(conn, rid, report, identifier.concept, "0012", "observed", "Licence 0012", [3])
        save_field(conn, rid, report, narrative.concept, "<h3>Opinion</h3><p>Full report.</p>", "observed", "Opinion", [2, 3])
    with repo.db_session(db) as conn:
        before = snapshot(conn, rid)
        view = next(r for r in before["reports"] if r["type"] == report)
        assert next(f for f in view["fields"] if f["concept"] == identifier.concept)["value"] == "0012"
        assert repo.list_notes_cells_for_run(conn, rid) == []  # reports cannot become financial notes
        save_field(conn, rid, report, identifier.concept, None, "not_disclosed", "Cleared", [], expected_revision=1)
        with pytest.raises(StaleReportEdit):
            save_field(conn, rid, report, identifier.concept, "9999", "user_override", "", [], expected_revision=1)
        assert snapshot(conn, rid)["digest"] != before["digest"]
    exported = build_notes_fill_doc(db, rid, decorate=False)
    assert len(exported["footnotes"]) == 1
    assert exported["footnotes"][0]["html"] == "<h3>Opinion</h3><p>Full report.</p>"


@pytest.mark.parametrize("kind,value", [("date", "17/10/2026"), ("choice", "clean")])
def test_invalid_scalar_type_or_selection_never_reaches_canonical_values(report_run, kind, value):
    db, rid = report_run
    field = next(f for f in fields("AUDITORS_REPORT") if f.kind == kind)
    with repo.db_session(db) as conn:
        with pytest.raises(ValueError):
            save_field(conn, rid, "AUDITORS_REPORT", field.concept, value, "observed", "source", [1])
        assert conn.execute("SELECT count(*) FROM report_fields").fetchone()[0] == 0
    with pytest.raises(ValueError):
        format_value("0012")  # numeric patching keeps its original contract


def _native_workbook(path, report, report_fields, *, protected=False):
    wb = Workbook(); ws = wb.active; ws.title = REPORTS[report][2]
    for row, field in enumerate(report_fields, 1):
        ws.cell(row, 1, field.concept)
        ws.cell(row, 2, field.label)
        ws.cell(row, 3, "old")
        wb.defined_names.add(DefinedName(field.concept, attr_text=f"'{ws.title}'!$C${row}"))
        if field.kind == "choice":
            dv = DataValidation(type="list", formula1='"' + ','.join(field.choices) + '"')
            ws.add_data_validation(dv); dv.add(ws.cell(row, 3))
        if field.kind == "date":
            ws.cell(row, 3).number_format = "yyyy-mm-dd"
            dv = DataValidation(type="date", operator="between", formula1="1", formula2="100000")
            ws.add_data_validation(dv); dv.add(ws.cell(row, 3))
        ws.cell(row, 3).protection = Protection(locked=protected)
    ws["F1"] = "=1+2"
    ws.protection.sheet = protected
    wb.save(path); wb.close()


def test_typed_report_export_preserves_ids_selections_dates_and_formula(report_run, tmp_path):
    db, rid = report_run
    report = "AUDITORS_REPORT"
    selected = [next(f for f in fields(report) if f.concept.endswith("LicenseNumberOfAuditor")),
                next(f for f in fields(report) if f.kind == "date"), next(f for f in fields(report) if f.kind == "choice")]
    values = ["0012", "2026-10-17", selected[-1].choices[-1]]
    with repo.db_session(db) as conn:
        for f, value in zip(selected, values):
            save_field(conn, rid, report, f.concept, value, "observed", "Source", [1])
        snap = snapshot(conn, rid)
    src, out = tmp_path / "native.xlsx", tmp_path / "filled.xlsx"
    _native_workbook(src, report, selected)
    _, data, _ = load_workbook_entries(str(src))
    resolved = resolve_reports(data, snap)
    assert len(resolved["writes"]) == 3
    result = fill_report_fields(str(src), resolved, str(out))
    assert not result["mismatches"]
    assert any(f["reason"] == "assessment_incomplete" for f in result["unresolved"])
    wb = load_workbook(out)
    try:
        ws = wb[REPORTS[report][2]]
        assert ws["C1"].value == "0012"
        assert ws["C1"].data_type == "s"
        assert ws["C2"].value.date().isoformat() == "2026-10-17"
        assert ws["C3"].value == values[-1]
        assert ws["F1"].value == "=1+2"
    finally:
        wb.close()
    # An explicit human clear must remove a previously filled identifier.
    with repo.db_session(db) as conn:
        save_field(conn, rid, report, selected[0].concept, None, "cleared", "Cleared", [], expected_revision=1)
        cleared = snapshot(conn, rid)
    _, filled_data, _ = load_workbook_entries(str(out))
    cleared_path = tmp_path / "cleared.xlsx"
    cleared_result = fill_report_fields(str(out), resolve_reports(filled_data, cleared), str(cleared_path))
    assert not cleared_result["mismatches"]
    wb = load_workbook(cleared_path)
    try:
        assert wb[REPORTS[report][2]]["C1"].value in {None, ""}
    finally:
        wb.close()


def test_column_tagged_report_table_fills_its_single_typing_row(report_run, tmp_path):
    # Native auditor details tag each column once and take values in a typing
    # row lower in the table (observed in a real mTool AuditReport sheet).
    db, rid = report_run
    report = "AUDITORS_REPORT"
    firm, reg, state = ("ssmt_NameOfAuditFirm", "ssmt_RegistrationNumberOfAuditFirm",
                        "ssmt_StateWhereAuditFirmIsLocated")
    with repo.db_session(db) as conn:
        for concept, value in ((firm, "Example & Co."), (reg, "AF 0001"), (state, "Selangor")):
            save_field(conn, rid, report, concept, value, "observed", "Source", [1])
        snap = snapshot(conn, rid)

    def build(path, typing_rows):
        wb = Workbook()
        ws = wb.active
        ws.title = REPORTS[report][2]
        for col, concept in zip("EFG", (firm, reg, state)):
            ws[f"{col}3"] = f"ssmt-cor_2022-12-31.xsd#{concept}"
        for row in typing_rows:
            for col in "EF":  # State stays locked, as mTool's picker cells do.
                ws[f"{col}{row}"].protection = Protection(locked=False)
            ws[f"G{row}"] = None
        ws["C7"] = "#LAYOUTSCER#"
        ws["E9"].protection = Protection(locked=False)  # outside the table
        ws.protection.sheet = True
        wb.save(path); wb.close()

    src, out = tmp_path / "table.xlsx", tmp_path / "filled.xlsx"
    build(src, [6])
    _, data, _ = load_workbook_entries(str(src))
    resolved = resolve_reports(data, snap)
    assert {(w["concept"], w["cell"]) for w in resolved["writes"]} == {(firm, "E6"), (reg, "F6")}
    assert any(u.get("concept") == state and u["reason"] == "missing_or_ambiguous_report_destination"
               for u in resolved["unresolved"])
    result = fill_report_fields(str(src), resolved, str(out))
    assert not result["mismatches"]
    wb = load_workbook(out)
    try:
        ws = wb[REPORTS[report][2]]
        assert (ws["E6"].value, ws["F6"].value, ws["E9"].value) == ("Example & Co.", "AF 0001", None)
    finally:
        wb.close()

    # Two typing rows (for example two auditors) never pick one.
    two = tmp_path / "two.xlsx"
    build(two, [5, 6])
    _, data, _ = load_workbook_entries(str(two))
    assert not resolve_reports(data, snap)["writes"]


def test_protected_or_unknown_report_destination_is_never_guessed(report_run, tmp_path):
    db, rid = report_run
    report = "AUDITORS_REPORT"
    field = next(f for f in fields(report) if f.kind == "text")
    with repo.db_session(db) as conn:
        save_field(conn, rid, report, field.concept, "Source auditor", "observed", "Source", [1])
        snap = snapshot(conn, rid)
    src, out = tmp_path / "locked.xlsx", tmp_path / "filled.xlsx"
    _native_workbook(src, report, [field], protected=True)
    _, data, _ = load_workbook_entries(str(src))
    doc = resolve_reports(data, snap)
    result = fill_report_fields(str(src), doc, str(out))
    assert not result["written"]
    assert any(e["reason"] == "protected_or_missing_cell" for e in result["unresolved"])
    with pytest.raises(ValueError, match="overlaps"):
        resolve_reports(data, snap, occupied=[(REPORTS[report][2], "C1")])
    # Local and workbook names may share a taxonomy name. Both destinations
    # must remain visible to ambiguity detection rather than last-name wins.
    import xml.etree.ElementTree as ET
    root = ET.fromstring(data["xl/workbook.xml"])
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    defined = root.find(f"{ns}definedNames")
    ET.SubElement(defined, f"{ns}definedName", name=field.concept, localSheetId="0").text = f"'{REPORTS[report][2]}'!$D$1"
    data["xl/workbook.xml"] = ET.tostring(root)
    ambiguous = resolve_reports(data, snap)
    assert not ambiguous["writes"]
    assert any(e["reason"] == "missing_or_ambiguous_report_destination" for e in ambiguous["unresolved"])
    # Generic taxonomy concepts in filing information do not identify an
    # auditor-report destination just because a workbook name points at them.
    _, data, _ = load_workbook_entries(str(src))
    data["xl/workbook.xml"] = data["xl/workbook.xml"].replace(b"Report-Auditors", b"Filing-Information")
    unrelated = resolve_reports(data, snap)
    assert not unrelated["writes"]


def test_report_review_api_rejects_stale_edits_and_edits_during_extraction(report_run, monkeypatch):
    import server
    db, rid = report_run
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    client = TestClient(server.app)
    field = next(f for f in fields("AUDITORS_REPORT") if f.kind == "text")
    url = f"/api/runs/{rid}/reports/AUDITORS_REPORT/{field.concept}"
    response = client.patch(url, json={"value": "0012", "revision": 0})
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "0012"
    assert client.patch(url, json={"value": "0001", "revision": 0}).status_code == 409
    assert client.patch(url, json={"value": None, "revision": 1}).json()["value"] is None
    with repo.db_session(db) as conn:
        conn.execute("UPDATE runs SET status='running' WHERE id=?", (rid,))
    assert client.patch(url, json={"value": "0012", "revision": 2}).status_code == 409
    assert client.get("/api/runs/99999/reports").status_code == 404


def test_report_only_workbook_preparation_exposes_unresolved_fields_and_stale_download(report_run, monkeypatch, tmp_path):
    import server
    db, rid = report_run
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    report = "AUDITORS_REPORT"
    field = next(f for f in fields(report) if f.kind == "text")
    with repo.db_session(db) as conn:
        save_field(conn, rid, report, field.concept, "0012", "observed", "Source", [1])
    src = tmp_path / "native.xlsx"
    _native_workbook(src, report, [field])
    client = TestClient(server.app)
    response = client.post(f"/api/runs/{rid}/mtool-fill/patch", files={"template": (src.name, src.read_bytes())})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "degraded"
    assert result["reports"]["written"][0]["value"] == "0012"
    assert result["reports"]["unresolved"]
    with repo.db_session(db) as conn:
        save_field(conn, rid, report, field.concept, "0013", "user_override", "Correction", [], expected_revision=1)
    download = client.get(result["download_url"], params={"acknowledge_degraded": "Reviewed"})
    assert download.status_code == 409
    assert "Reports changed" in download.text


@pytest.mark.parametrize("cleared", [False, True])
def test_reports_export_narratives_independently_of_financial_notes(report_run, monkeypatch, tmp_path, cleared):
    from test_mtool_offline_fill import footnote_template
    from zipfile import ZipFile
    import server
    db, rid = report_run
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    report = "AUDITORS_REPORT"
    narrative = next(f for f in fields(report) if f.kind == "html")
    with repo.db_session(db) as conn:
        save_field(conn, rid, report, narrative.concept, "<p>Opinion and complete report.</p>", "observed", "Opinion", [1])
        if cleared:
            save_field(conn, rid, report, narrative.concept, None, "cleared", "Cleared", [], expected_revision=1)
    source = footnote_template.__wrapped__(tmp_path)
    renamed = tmp_path / "report-popup.xlsx"
    with ZipFile(source) as original, ZipFile(renamed, "w") as out:
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename.endswith(".xml"):
                data = data.replace(b"Notes-Listofnotes", b"Report-Auditors")
                data = data.replace(b"Property, plant and equipment", narrative.label.encode())
                if item.filename == "xl/worksheets/sheet1.xml":
                    marker = f'<c r="A132" t="inlineStr"><is><t>fields.xsd#{narrative.concept}</t></is></c>'.encode()
                    data = data.replace(b'<row r="132">', b'<row r="132">' + marker)
            out.writestr(item, data)
    client = TestClient(server.app)
    response = client.post(f"/api/runs/{rid}/mtool-fill/patch", files={"template": (renamed.name, renamed.read_bytes())}, data={"fill_notes": "false"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["notes"]["counts"]["written"] == 1, result["notes"]
    download = client.get(result["download_url"], params={"acknowledge_degraded": "Reviewed"})
    assert download.status_code == 200
    filled = tmp_path / "downloaded.xlsx"; filled.write_bytes(download.content)
    from mtool.offline_fill import get_shared_strings
    _, data, _ = load_workbook_entries(str(filled))
    texts = get_shared_strings(data)
    assert not any("Existing PPE note" in text for text in texts)
    if cleared:
        assert not any("Opinion and complete report." in text for text in texts)
        assert not any(e["reason"] == "report_narrative_missing" and e.get("sheet") == REPORTS[report][2]
                       for e in result["reports"]["unresolved"])
    else:
        assert any("Opinion and complete report." in text for text in texts)


@pytest.mark.parametrize("kind,value", [("text", "0012"), ("text", "=1+1"), ("html", "=1+1"),
                                       ("text", "#REF!"), ("html", "#REF!")])
def test_report_only_diagnostic_download_rebuilds_current_canonical_edits(report_run, monkeypatch, tmp_path, kind, value):
    import server
    db, rid = report_run
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    field = next(f for f in fields("AUDITORS_REPORT") if f.kind == kind)
    with repo.db_session(db) as conn:
        conn.execute("UPDATE runs SET merged_workbook_path=? WHERE id=?", (str(tmp_path / "merged.xlsx"), rid))
        save_field(conn, rid, "AUDITORS_REPORT", field.concept, f"<p>{value}</p>" if kind == "html" else value,
                   "user_override", "Correction", [])
    rebuilt = server._reexport_and_remerge_from_facts(rid)
    assert rebuilt is not None
    try:
        wb = load_workbook(rebuilt)
        try:
            cell = wb[REPORTS["AUDITORS_REPORT"][2]].cell(field.row, 2)
            assert cell.value == value
            assert cell.data_type == "s"
        finally:
            wb.close()
    finally:
        rebuilt.unlink(missing_ok=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("crashed", [False, True])
async def test_report_worker_distinguishes_incomplete_assessment_from_crash(report_run, monkeypatch, tmp_path, crashed):
    import asyncio
    from types import SimpleNamespace
    from pydantic_ai.models.test import TestModel
    from reports import agent

    db, rid = report_run
    report = "AUDITORS_REPORT"
    with repo.db_session(db) as conn:
        rai = repo.create_run_agent(conn, rid, statement_type=report)

    def page_count(_path):
        if crashed:
            raise RuntimeError("PDF read failed")
        return 1

    monkeypatch.setattr(agent, "count_pdf_pages", page_count)
    config = SimpleNamespace(db_path=str(db), run_id=rid, pdf_path="unused.pdf", output_dir=str(tmp_path),
                             model=TestModel(call_tools=[], custom_output_text="Assessment ended."), filing_standard="mfrs")
    results = await agent.run_reports(config, {report}, {}, {report: rai}, asyncio.Queue(), "report-outcome-test")
    assert results[0].status == "failed"
    with repo.db_session(db) as conn:
        saved = conn.execute("SELECT status,error_type,error_message,workbook_path FROM run_agents WHERE id=?", (rai,)).fetchone()
    assert saved[0] == "failed"
    assert saved[1] == (None if crashed else "report_incomplete")
    assert saved[2] == ("Report extraction failed. Captured values are retained for review." if crashed
                        else "Report assessment incomplete. Review missing or uncertain fields.")
    assert bool(saved[3]) is not crashed


@pytest.mark.asyncio
async def test_report_tasks_cancelled_before_start_still_finish_audit_rows(report_run, monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from reports.agent import run_reports
    import task_registry
    db, rid = report_run
    with repo.db_session(db) as conn:
        ids = {r: repo.create_run_agent(conn, rid, statement_type=r) for r in REPORTS}
    monkeypatch.setattr(task_registry, "register", lambda session, role, task: task.cancel(task_registry.USER_ABORT_REASON))
    config = SimpleNamespace(db_path=str(db), run_id=rid, pdf_path="unused.pdf", output_dir=str(tmp_path), model="unused", filing_standard="mfrs")
    results = await run_reports(config, set(REPORTS), {}, ids, asyncio.Queue(), "cancelled-report-test")
    assert [r.status for r in results] == ["cancelled", "cancelled"]
    with repo.db_session(db) as conn:
        assert {r[0] for r in conn.execute("SELECT status FROM run_agents WHERE run_id=?", (rid,))} == {"cancelled"}
