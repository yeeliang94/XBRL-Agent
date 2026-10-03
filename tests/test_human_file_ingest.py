"""Reading a human-filled mTool file against one run (eval/human_file.py).

Figures are proven with a real fill -> read round trip on a repository
template: whatever the mTool fill writes, the reader must read back exactly,
including concepts that share a label on one sheet (the Step 1 failure of the
old label reader).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.workbook.defined_name import DefinedName

from db.schema import init_db
from mtool.offline_fill import wrap_footnote_html
from eval.human_compare import load_comparison
from eval.human_file import (
    HumanFileError,
    ingest_human_file,
    load_human_file,
    read_human_file,
)
from tests._human_file_fixture import (
    SOFP,
    add_run,
    filled_file,
    import_template_file,
    leaf_facts,
    make_sofp_db,
    sofp_config,
)


@pytest.fixture
def sofp_db(tmp_path):
    return make_sofp_db(tmp_path)


def _typed(read) -> dict[tuple[str, str], float]:
    return {(f["concept_uuid"], f["period"]): f["value"]
            for f in read.facts if f["value"] is not None}


@pytest.mark.parametrize('standard', ['mpers','clbg'])
def test_native_note_alias_keeps_category_identity_on_read(tmp_path, standard):
    """Category expansion must inspect the verified physical sheet before reading."""
    from concept_model.bootstrap import _import_one
    from concept_model.dimensions import dimension_key
    from notes_types import NotesTemplateType, notes_template_path
    db = tmp_path/'categories.db'
    init_db(db)
    note = NotesTemplateType.ISSUED_CAPITAL if standard == 'mpers' else NotesTemplateType.RELATED_PARTY
    tid = _import_one(db,notes_template_path(note,standard=standard),'company')
    run = add_run(db,{'filing_standard':standard,'filing_level':'company','denomination':'units',
                     'notes_to_run':[note.value]})
    label = 'Number of shares issued and fully paid' if standard == 'mpers' else 'Donation income'
    with sqlite3.connect(db) as conn:
        uuid, primary = conn.execute('SELECT n.concept_uuid,sa.primary_concept FROM concept_nodes n '
            'JOIN concept_semantic_addresses sa USING(concept_uuid) WHERE n.template_id=? '
            "AND n.kind='LEAF' AND ltrim(n.canonical_label,'* ')=?",(tid,label)).fetchone()
    axis, member, table = (
        ('ifrs-smes_ClassesOfShareCapitalAxis','ssmt-mpers_OrdinarySharesMember','ifrs-smes_DisclosureOfClassesOfShareCapitalTable')
        if standard == 'mpers' else
        ('ifrs-full_CategoriesOfRelatedPartiesAxis','ifrs-full_ParentMember','ifrs-full_DisclosureOfTransactionsBetweenRelatedPartiesTable'))
    wb = Workbook(); ws = wb.active
    ws.title = 'Notes-IssuedCap' if standard == 'mpers' else 'Notes-Relatedpartytransactions'
    if standard == 'clbg':
        ws['A1'] = 'http://xbrl.ssm.com.my/role/ssm/rol_ssmt-fs-clbg_2022-12-31/ssmt-fs-clbg_2022-12-31_role-640000'
        ws['B1'] = 'native.xsd#ssmt-mfrs_DisclosureOnRelatedPartyTransactionsAbstract'
    ws['E2'] = '::'.join('native.xsd#'+identifier for identifier in (table,axis,member))
    ws['B3'] = 'native.xsd#'+axis; ws['C3'] = '#DOM#'; ws['D3'] = '#PRIM#'
    ws['E3'] = 'Ordinary shares' if standard == 'mpers' else 'Parent'
    ws['C4'] = '#ENDT#'; ws['E4'] = '31/12/2026'
    ws['A7'] = 'native.xsd#'+primary; ws['D7'] = label; ws['E7'] = 123
    path = tmp_path/'native-note.xlsx'; wb.save(path); wb.close()
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        read = read_human_file(conn,run,path,'units')
    values = [f for f in read.facts if f['concept_uuid'] == uuid and f['value'] is not None]
    assert len(values) == 1
    assert values[0]['value'] == 123
    assert values[0]['dimension_key'] == dimension_key({axis:member})


@pytest.mark.parametrize("historical_auto_variant", [False, True])
def test_round_trip_reads_every_filled_value_including_same_label_fields(
    sofp_db, tmp_path, historical_auto_variant,
):
    config = sofp_config()
    if historical_auto_variant:
        config["variants"] = {}
    run_id = add_run(sofp_db, config)
    if historical_auto_variant:
        with sqlite3.connect(sofp_db) as conn:
            conn.execute(
                "INSERT INTO run_agents(run_id, statement_type, variant, status, started_at) "
                "VALUES (?, 'SOFP', 'CuNonCu', 'succeeded', '2026-10-03')",
                (run_id,),
            )
    facts = leaf_facts(sofp_db)
    path = filled_file(sofp_db, run_id, facts, tmp_path)

    with sqlite3.connect(sofp_db) as conn:
        read = read_human_file(conn, run_id, path, "units")

    assert _typed(read) == {(u, p): v for u, p, v in facts}
    assert read.unmatched == []
    assert read.not_compared == []
    assert read.summary["typed_values"] == len(facts)
    assert read.summary["magnitude_warning"] is None


def test_unaddressed_typed_row_is_unmatched_and_formula_cell_is_calculated(
    sofp_db, tmp_path,
):
    run_id = add_run(sofp_db, sofp_config())
    facts = leaf_facts(sofp_db)
    path = filled_file(sofp_db, run_id, facts, tmp_path)
    with sqlite3.connect(sofp_db) as conn:
        header_row, header_label = conn.execute(
            "SELECT render_row, canonical_label FROM concept_nodes "
            "WHERE template_id = ? AND kind = 'ABSTRACT' "
            "AND render_sheet = 'SOFP-CuNonCu' ORDER BY render_row LIMIT 1",
            (SOFP,),
        ).fetchone()
        formula_uuid, formula_row = conn.execute(
            "SELECT n.concept_uuid, t.target_row FROM concept_nodes n "
            "JOIN concept_targets t USING(concept_uuid) "
            "WHERE n.concept_uuid = ? AND t.period = 'CY'", (facts[-1][0],),
        ).fetchone()
    wb = load_workbook(path)
    wb["SOFP-CuNonCu"][f"B{header_row}"] = 77
    wb["SOFP-CuNonCu"][f"B{formula_row}"] = "=1+1"
    wb.save(path)

    with sqlite3.connect(sofp_db) as conn:
        read = read_human_file(conn, run_id, path, "units")

    assert [(u["sheet"], u["row"], u["label"], u["values"]) for u in read.unmatched] == [
        ("SOFP-CuNonCu", header_row, header_label, {"B": 77.0})]
    # The formula cell and the run's totals are calculated; no formula
    # result is read, since a patched file carries stale cached values.
    calculated = {(f["concept_uuid"], f["period"]): f["value"]
                  for f in read.facts if f["calculated"]}
    assert calculated[(formula_uuid, "CY")] is None
    assert set(calculated.values()) == {None}
    assert (formula_uuid, "CY") not in _typed(read)
    assert read.summary["typed_values"] == len(facts) - 1


@pytest.mark.parametrize("historical_auto_variant", [False, True])
def test_statement_filled_in_another_variant_is_not_compared(
    sofp_db, tmp_path, historical_auto_variant,
):
    import_template_file(sofp_db, tmp_path, "02-SOFP-OrderOfLiquidity.xlsx")
    source_run = add_run(sofp_db, sofp_config())
    path = filled_file(sofp_db, source_run, leaf_facts(sofp_db), tmp_path)
    config = sofp_config(variant="OrderOfLiquidity")
    if historical_auto_variant:
        config["variants"] = {}
    run_id = add_run(sofp_db, config)
    if historical_auto_variant:
        with sqlite3.connect(sofp_db) as conn:
            conn.execute(
                "INSERT INTO run_agents(run_id, statement_type, variant, status, started_at) "
                "VALUES (?, 'SOFP', 'OrderOfLiquidity', 'succeeded', '2026-10-03')",
                (run_id,),
            )

    with sqlite3.connect(sofp_db) as conn, pytest.raises(
        HumanFileError, match=r"different statement layout: SOFP \(CuNonCu\)"
    ):
        read_human_file(conn, run_id, path, "units")


def test_unknown_automatic_variant_does_not_guess_from_scout(sofp_db):
    from eval.human_file import run_filing_shape

    config = sofp_config()
    config["variants"] = {}
    config["infopack"] = {"statements": {"SOFP": {"variant_suggestion": "CuNonCu"}}}
    run_id = add_run(sofp_db, config)
    with sqlite3.connect(sofp_db) as conn:
        assert run_filing_shape(conn, run_id)["statements"] == {}


def test_unit_converts_to_run_denomination_and_warns_on_1000x(sofp_db, tmp_path):
    run_id = add_run(sofp_db, sofp_config(denomination="thousands"))
    facts = leaf_facts(sofp_db)
    path = filled_file(sofp_db, run_id, facts, tmp_path)

    with sqlite3.connect(sofp_db) as conn:
        read = read_human_file(conn, run_id, path, "units")

    assert _typed(read) == {(u, p): v / 1000 for u, p, v in facts}
    assert "1,000 times smaller" in read.summary["magnitude_warning"]


def test_unit_conversion_leaves_share_counts_unchanged(sofp_db, tmp_path, monkeypatch):
    from mtool.units import MONETARY, SHARES

    run_id = add_run(sofp_db, sofp_config(denomination="thousands"))
    facts = leaf_facts(sofp_db)
    path = filled_file(sofp_db, run_id, facts, tmp_path)
    with sqlite3.connect(sofp_db) as conn:
        share_label = conn.execute(
            "SELECT canonical_label FROM concept_nodes WHERE concept_uuid = ?",
            (facts[0][0],),
        ).fetchone()[0]
    monkeypatch.setattr(
        "eval.human_file.unit_class_for_label",
        lambda label, standard: SHARES if label == share_label else MONETARY,
    )

    with sqlite3.connect(sofp_db) as conn:
        read = read_human_file(conn, run_id, path, "units")

    typed = _typed(read)
    assert typed[(facts[0][0], "CY")] == facts[0][2]
    assert typed[(facts[-1][0], "PY")] == facts[-1][2] / 1000


def test_unmatched_number_alone_does_not_compare_statement(sofp_db, tmp_path):
    run_id = add_run(sofp_db, sofp_config())
    facts = leaf_facts(sofp_db)
    path = filled_file(sofp_db, run_id, facts, tmp_path)
    wb = load_workbook(path)
    with sqlite3.connect(sofp_db) as conn:
        targets = conn.execute(
            "SELECT target_sheet, target_row, target_col FROM concept_targets "
            f"WHERE concept_uuid IN ({','.join('?' * len(facts))})",
            [uuid for uuid, _, _ in facts],
        ).fetchall()
        header_row = conn.execute(
            "SELECT render_row FROM concept_nodes WHERE template_id = ? "
            "AND kind = 'ABSTRACT' AND render_sheet = 'SOFP-CuNonCu' "
            "ORDER BY render_row LIMIT 1", (SOFP,),
        ).fetchone()[0]
    for sheet, row, col in targets:
        wb[sheet][f"{col}{row}"] = None
    wb["SOFP-CuNonCu"][f"B{header_row}"] = 77
    wb.save(path)

    with sqlite3.connect(sofp_db) as conn, pytest.raises(
        HumanFileError, match="No figures or notes in this file match"
    ):
        read_human_file(conn, run_id, path, "units")


def _notes_workbook(tmp_path: Path) -> Path:
    wb = Workbook()
    visible = wb.active
    visible.title = "Notes-CI"
    visible["A12"] = ("ssmt-mfrs-cor_2022-12-31.xsd#ssmt-mfrs_"
                      "DisclosureOfCorporateInformationExplanatory@http://x/role")
    visible["D12"] = "*Disclosure of corporate information"
    visible["E12"] = "[Text block added]"
    visible["A13"] = "ssmt-mfrs-cor_2022-12-31.xsd#ssmt-mfrs_UnknownExplanatory"
    visible["D13"] = "Unknown note"
    visible["E13"] = "[Text block added]"
    footnotes = wb.create_sheet("+FootnoteTexts")
    for row, key in ((1, "fn_1"), (2, "fn_2")):
        footnotes[f"A{row}"] = key
        footnotes[f"B{row}"] = "Notes-CI"
        # The mTool XHTML text-block shell, with Excel's _x000D_ line breaks.
        footnotes[f"C{row}"] = wrap_footnote_html(
            f'<p onclick="x()">Human note {row}</p><script>alert(1)</script>')
    wb.defined_names["fn_1"] = DefinedName("fn_1", attr_text="'Notes-CI'!$E$12")
    wb.defined_names["fn_2"] = DefinedName("fn_2", attr_text="'Notes-CI'!$E$13")
    path = tmp_path / "notes.xlsx"
    wb.save(path)
    return path


def test_note_maps_to_field_by_taxonomy_element_id_and_is_unwrapped(tmp_path):
    db = tmp_path / "audit.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO template_slots(target_id, canonical_target_id, "
            "template_id, sheet, row, col, label, slot_role, value_kind, "
            "taxonomy_element_id, mapping_source, manifest_version, "
            "workbook_fingerprint, validation_status) VALUES ('t1', 'ci-field', "
            "'mfrs-company-notes-corporateinfo-v1', 'Notes-CI', 5, 'B', "
            "'*Disclosure of corporate information', 'INPUT', 'html', "
            "'ssmt-mfrs_DisclosureOfCorporateInformationExplanatory', "
            "'presentation_linkbase', 'v1', 'fp', 'writable')")
    run_id = add_run(db, {"filing_standard": "mfrs", "filing_level": "company",
                       "statements": [], "notes_to_run": ["CORP_INFO"]})

    with sqlite3.connect(db) as conn:
        read = read_human_file(conn, run_id, _notes_workbook(tmp_path), "units")

    assert read.notes == [{"concept_uuid": "ci-field", "note_key": "fn_1",
                           "html": "<p>Human note 1</p>"}]
    assert [(u["kind"], u["row"], u["label"]) for u in read.unmatched] == [
        ("note", 13, "Unknown note")]


def test_empty_note_markup_is_not_a_filled_human_field(tmp_path):
    db = tmp_path / "audit.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO template_slots(target_id, canonical_target_id, "
            "template_id, sheet, row, col, label, slot_role, value_kind, "
            "taxonomy_element_id, mapping_source, manifest_version, "
            "workbook_fingerprint, validation_status) VALUES ('t1', 'ci-field', "
            "'mfrs-company-notes-corporateinfo-v1', 'Notes-CI', 5, 'B', "
            "'*Disclosure of corporate information', 'INPUT', 'html', "
            "'ssmt-mfrs_DisclosureOfCorporateInformationExplanatory', "
            "'presentation_linkbase', 'v1', 'fp', 'writable')")
    run_id = add_run(db, {"filing_standard": "mfrs", "filing_level": "company",
                           "statements": [], "notes_to_run": ["CORP_INFO"]})
    path = _notes_workbook(tmp_path)
    wb = load_workbook(path)
    wb["+FootnoteTexts"]["C1"] = wrap_footnote_html("<p></p>")
    wb.save(path)

    with sqlite3.connect(db) as conn, pytest.raises(
        HumanFileError, match="No figures or notes in this file match"
    ):
        read_human_file(conn, run_id, path, "units")


def test_replacing_a_file_keeps_one_record_and_refuses_unfinished_runs(
    sofp_db, tmp_path,
):
    run_id = add_run(sofp_db, sofp_config())
    facts = leaf_facts(sofp_db)
    path = filled_file(sofp_db, run_id, facts, tmp_path)

    with sqlite3.connect(sofp_db) as conn:
        for name in ("first.xlsx", "second.xlsx"):
            record = ingest_human_file(conn, run_id, path, filename=name,
                                       unit="units", uploaded_by="a@b.c")
        assert record["filename"] == "second.xlsx"
        assert conn.execute(
            "SELECT COUNT(*) FROM human_file_facts WHERE run_id = ? "
            "AND value IS NOT NULL", (run_id,)).fetchone()[0] == len(facts)
        assert load_human_file(conn, run_id)["summary"]["typed_values"] == len(facts)

        draft = add_run(sofp_db, sofp_config(), status="draft")
        with pytest.raises(HumanFileError, match="completed run"):
            read_human_file(conn, draft, path, "units")


def test_statement_marked_not_in_run_never_hides_the_run_values(sofp_db, tmp_path):
    # mTool lets a preparer attach a footnote to any figure. One on a face
    # statement row was stored as the statement being "not in run", which
    # dropped every run value on it: identical figures read as missed.
    run_id = add_run(sofp_db, sofp_config())
    facts = leaf_facts(sofp_db)
    path = filled_file(sofp_db, run_id, facts, tmp_path)
    with sqlite3.connect(sofp_db) as conn:
        ingest_human_file(conn, run_id, path, filename="h.xlsx", unit="units",
                          uploaded_by=None)
        conn.execute(
            "UPDATE human_files SET not_compared_json = ? WHERE run_id = ?",
            (json.dumps([{"template_id": SOFP, "statement": SOFP,
                          "reason": "not_in_run", "notes": 1}]), run_id))
        figures = load_comparison(conn, run_id)["figures"]

    assert {s["status"] for s in figures["slots"] if not s.get("calculated")} == {"agree"}
    assert figures["totals"]["Company"]["same_value"] == len(facts)


@pytest.mark.parametrize("prior_total,total_date", [
    (None, None), (900, "31/12/2024"), (900, "31/12/2025"),
])
def test_category_total_column_is_the_field_without_a_category(tmp_path, prior_total, total_date):
    # mTool's share-capital sheet has one column per share class plus a
    # Total column with no category member. A value typed there is the
    # field's own value, not an unmatched row.
    db = tmp_path / "audit.db"
    init_db(db)
    template_id = import_template_file(db, tmp_path, "13-Notes-IssuedCapital.xlsx")
    run_id = add_run(db, {"filing_standard": "mfrs", "filing_level": "company",
                          "statements": [], "notes_to_run": ["ISSUED_CAPITAL"],
                          "denomination": "units"})
    xsd = "full_ifrs-cor_2022-03-24.xsd#"
    wb = Workbook()
    ws = wb.active
    ws.title = "Notes-Issuedcapital"
    ws["E18"] = (f"{xsd}ifrs-full_DisclosureOfClassesOfShareCapitalTable::"
                 f"{xsd}ifrs-full_ClassesOfShareCapitalAxis::"
                 f"{xsd}ifrs-full_OrdinarySharesMember")
    ws["C19"], ws["D19"] = "#LAYOUTSCSR#", "#PRIM#"
    ws["C20"], ws["E20"], ws["F20"] = "#DOM#", "Ordinary shares", "Total"
    ws["C24"], ws["E24"], ws["F24"] = "#ENDT#", "31/12/2025", "31/12/2025"
    ws["A31"] = f"{xsd}ifrs-full_NumberOfSharesIssuedAndFullyPaid"
    ws["D31"] = "*Number of shares issued and fully paid"
    ws["E31"], ws["F31"] = 1000, 1000
    if prior_total is not None:
        ws["G18"] = ws["E18"].value
        ws["G20"], ws["H20"] = "Ordinary shares", "Total"
        ws["G24"], ws["H24"] = "31/12/2024", total_date
        ws["G31"], ws["H31"] = prior_total, prior_total
    path = tmp_path / "category.xlsx"
    wb.save(path)

    with sqlite3.connect(db) as conn:
        read = read_human_file(conn, run_id, path, "units")
        uuid = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
            "AND canonical_label = '*Number of shares issued and fully paid'",
            (template_id,)).fetchone()[0]

    ambiguous = total_date == "31/12/2025"
    if ambiguous:
        assert [u["values"] for u in read.unmatched] == [{"F": 1000.0, "H": 900.0}]
    else:
        assert read.unmatched == []
    ordinary = '{"ifrs-full_ClassesOfShareCapitalAxis":"ifrs-full_OrdinarySharesMember"}'
    expected = {("CY", ordinary, 1000.0)}
    if not ambiguous:
        expected.add(("CY", "", 1000.0))
    if prior_total is not None:
        expected.add(("PY", ordinary, float(prior_total)))
        if not ambiguous:
            expected.add(("PY", "", float(prior_total)))
    assert {(f["period"], f["dimension_key"], f["value"]) for f in read.facts
            if f["concept_uuid"] == uuid and f["value"] is not None} == expected
