"""Operator resolution must remain bound to the current fact and workbook."""
import copy

import pytest
from openpyxl import load_workbook

from test_mtool_template_map import _save_semantic_marker_workbook
from mtool.template_map import resolve_filing_doc


@pytest.mark.parametrize('first_time', [False, True])
def test_native_group_scope_and_periods_remain_distinct(tmp_path, first_time):
    """Native consolidation markers must place both scopes without guessing PY."""
    from pathlib import Path
    from mtool.offline_fill import fill_workbook, load_workbook_entries
    from mtool.template_map import index_workbook
    root = Path(__file__).resolve().parents[1]
    source = next((root / 'data').glob(
        '*Group_FirstTime*.xlsx' if first_time else '*Group_SOFP*.xlsx'))
    writes = []
    for period in ('CY', 'PY'):
        for scope in ('Group', 'Company'):
            for sheet, primary, label, dimensions in [
                ('SOFP-CuNonCu', 'ifrs-full_DeferredTaxAssets', 'Deferred tax assets', {}),
                ('SOCIE', 'ifrs-full_Equity', '*Equity at beginning of period', {
                    'ifrs-full_ComponentsOfEquityAxis': 'ifrs-full_RetainedEarningsMember'}),
                ('SOCIE', 'ifrs-full_Equity', '*Equity at beginning of period', {
                    'ifrs-full_ComponentsOfEquityAxis': 'ifrs-full_EquityMember'}),
            ]:
                writes.append({'sheet': sheet, 'label': label, 'value': 100 + len(writes),
                               'period': period, 'entity_scope': scope, 'kind': 'MATRIX_CELL' if dimensions else 'LEAF',
                               'column_role': scope.lower() + ('_current_year' if period == 'CY' else '_prior_year'),
                               'semantic_address': {'primary_concept': primary, 'dimensions': dimensions}})
    doc = {'meta': {'filing_standard': 'mfrs', 'filing_level': 'group'},
           'sheets': {sheet: {'columns': {w['column_role']: None for w in writes if w['sheet'] == sheet}}
                      for sheet in ('SOFP-CuNonCu', 'SOCIE')}, 'writes': writes}
    ready, coverage = resolve_filing_doc(str(source), doc)
    expected = 6 if first_time else 12
    assert coverage['mapped'] == expected, coverage
    assert len({(w['sheet'], w['cell']) for w in ready['writes']}) == expected
    if first_time:
        assert all(w['period'] == 'CY' for w in ready['writes'])
        assert coverage['unmapped'] == 6
    else:
        assert coverage['unmapped'] == coverage['ambiguous'] == 0
    output = tmp_path / 'native-filled.xlsx'
    report = fill_workbook(str(source), ready, str(output))
    assert len(report['written']) == expected
    _, entries, _ = load_workbook_entries(str(output))
    _, cells = index_workbook(entries)
    from mtool.offline_fill import split_ref
    for write in ready['writes']:
        col, row = split_ref(write['cell'])
        assert float(cells[write['sheet']][row][col][1]) == write['value']


def _doc():
    return {"sheets": {"SOCIE": {"columns": {}}}, "writes": [{
        "concept_uuid": "fact-1", "sheet": "SOCIE", "label": "Profit or loss",
        "value": 100, "period": "CY", "entity_scope": "Company",
        "column_role": "current_year", "semantic_address": {
            "primary_concept": "ifrs-full_ProfitLoss", "dimensions": {},
        },
    }]}


def test_missing_category_can_be_explicitly_resolved_and_audited(tmp_path):
    path = tmp_path / "mtool.xlsx"
    _save_semantic_marker_workbook(path, two_periods=True)
    doc = _doc()
    _, report = resolve_filing_doc(str(path), doc)
    issue = report["unresolved_writes"][0]
    assert issue["period"] == "CY"
    assert issue["entity_scope"] == "Company"
    assert [o["cell"] for o in issue["resolution_options"]] == ["SOCIE!E5"]
    selection = {issue["resolution_key"]: "SOCIE!E5"}
    ready, report = resolve_filing_doc(str(path), doc, filing_targets=selection)
    assert ready["writes"][0]["cell"] == "E5"
    assert report["status"] == "attention"
    assert report["operator_resolutions"][0]["dimensions"] == {
        "ifrs-full_ComponentsOfEquityAxis": "ifrs-full_IssuedCapitalMember",
    }


def test_arbitrary_or_wrong_period_destination_is_rejected(tmp_path):
    path = tmp_path / "mtool.xlsx"
    _save_semantic_marker_workbook(path, two_periods=True)
    doc = _doc()
    _, report = resolve_filing_doc(str(path), doc)
    key = report["unresolved_writes"][0]["resolution_key"]
    for cell in ("SOCIE!E13", "SOCIE!Z99"):
        with pytest.raises(ValueError, match="destination"):
            resolve_filing_doc(str(path), doc, filing_targets={key: cell})


def test_selection_is_invalidated_when_fact_changes(tmp_path):
    path = tmp_path / "mtool.xlsx"
    _save_semantic_marker_workbook(path)
    doc = _doc()
    _, report = resolve_filing_doc(str(path), doc)
    key = report["unresolved_writes"][0]["resolution_key"]
    changed = copy.deepcopy(doc)
    changed["writes"][0]["value"] = 200
    with pytest.raises(ValueError, match="stale"):
        resolve_filing_doc(str(path), changed, filing_targets={key: "SOCIE!E5"})


def test_selection_is_invalidated_when_workbook_changes(tmp_path):
    path = tmp_path / "mtool.xlsx"
    _save_semantic_marker_workbook(path)
    doc = _doc()
    _, report = resolve_filing_doc(str(path), doc)
    key = report["unresolved_writes"][0]["resolution_key"]
    wb = load_workbook(path)
    wb.active["D5"] = "Revised row label"
    wb.save(path)
    with pytest.raises(ValueError, match="stale"):
        resolve_filing_doc(str(path), doc, filing_targets={key: "SOCIE!E5"})


def test_formula_destination_is_never_offered(tmp_path):
    path = tmp_path / "mtool.xlsx"
    _save_semantic_marker_workbook(path)
    wb = load_workbook(path)
    wb.active["E5"] = "=SUM(E6:E10)"
    wb.save(path)
    _, report = resolve_filing_doc(str(path), _doc())
    assert report["status"] == "partial"
    assert report["unresolved_writes"][0]["resolution_options"] == []


def test_two_facts_cannot_be_placed_in_one_cell(tmp_path):
    path = tmp_path / "mtool.xlsx"
    _save_semantic_marker_workbook(path)
    doc = _doc()
    other = copy.deepcopy(doc["writes"][0])
    other["concept_uuid"] = "fact-2"
    doc["writes"].append(other)
    _, report = resolve_filing_doc(str(path), doc)
    selections = {i["resolution_key"]: "SOCIE!E5" for i in report["unresolved_writes"]}
    ready, report = resolve_filing_doc(str(path), doc, filing_targets=selections)
    assert ready["writes"] == []
    assert report["status"] == "partial"
    assert report["unmapped"] == 2
    for issue in report["unresolved_writes"]:
        assert issue["reason_code"] == "destination_collision"
        assert issue["concept_uuid"] in {"fact-1", "fact-2"}
        assert issue["resolution_options"]


def test_category_periods_in_adjacent_columns_do_not_share_a_destination(tmp_path):
    path = tmp_path / "mtool.xlsx"
    _save_semantic_marker_workbook(path)
    wb = load_workbook(path)
    ws = wb.active
    ws["F2"] = ws["E2"].value
    ws["F4"] = "31/12/2023"
    wb.save(path)
    doc = _doc()
    prior = copy.deepcopy(doc["writes"][0])
    prior.update(period="PY", column_role="prior_year")
    doc["writes"].append(prior)
    _, report = resolve_filing_doc(str(path), doc)
    issues = report["unresolved_writes"]
    assert [o["cell"] for o in issues[0]["resolution_options"]] == ["SOCIE!E5"]
    assert [o["cell"] for o in issues[1]["resolution_options"]] == ["SOCIE!F5"]
    choices = {i["resolution_key"]: i["resolution_options"][0]["cell"] for i in issues}
    ready, report = resolve_filing_doc(str(path), doc, filing_targets=choices)
    assert [w["cell"] for w in ready["writes"]] == ["E5", "F5"]
    assert report["status"] == "attention"
    for write in doc["writes"]:
        write["semantic_address"]["dimensions"] = {
            "ifrs-full_ComponentsOfEquityAxis": "ifrs-full_IssuedCapitalMember",
        }
    ready, report = resolve_filing_doc(str(path), doc)
    assert [w["cell"] for w in ready["writes"]] == ["E5", "F5"]
    assert report["unmapped"] == report["ambiguous"] == 0


def test_restated_marker_does_not_split_prior_year_category_block(tmp_path):
    """FINCO's PY block has a second #DOM# for restatement after its members."""
    path = tmp_path / "restated-socie.xlsx"
    _save_semantic_marker_workbook(path, two_periods=True)
    wb = load_workbook(path)
    ws = wb.active
    ws.insert_rows(12)
    ws["B12"] = "abc::abc"
    ws["C12"] = "#DOM#"
    ws["E12"] = "Restated"
    wb.save(path)
    wb.close()
    doc = _doc()
    doc["writes"][0].update(period="PY", column_role="prior_year")
    doc["writes"][0]["semantic_address"]["dimensions"] = {
        "ifrs-full_ComponentsOfEquityAxis": "ifrs-full_IssuedCapitalMember",
    }
    ready, report = resolve_filing_doc(str(path), doc)
    assert report["unmapped"] == report["ambiguous"] == 0
    assert ready["writes"][0]["cell"] == "E14"


def test_repeated_primary_is_resolved_by_exact_row_label_within_taxonomy_candidates(tmp_path):
    path = tmp_path / "mtool.xlsx"
    _save_semantic_marker_workbook(path)
    wb = load_workbook(path)
    ws = wb.active
    ws["A6"] = ws["A5"].value
    ws["D5"] = "Opening balance"
    ws["D6"] = "Closing balance"
    wb.save(path)
    doc = _doc()
    doc["writes"][0]["label"] = "*Opening balance"
    doc["writes"][0]["semantic_address"]["dimensions"] = {
        "ifrs-full_ComponentsOfEquityAxis": "ifrs-full_IssuedCapitalMember",
    }
    ready, report = resolve_filing_doc(str(path), doc)
    assert report["ambiguous"] == 0
    assert ready["writes"][0]["cell"] == "E5"


def test_duplicate_labels_still_require_operator_resolution(tmp_path):
    path = tmp_path / "mtool.xlsx"
    _save_semantic_marker_workbook(path)
    wb = load_workbook(path)
    ws = wb.active
    ws["A6"] = ws["A5"].value
    ws["D5"] = ws["D6"] = "Profit or loss"
    wb.save(path)
    doc = _doc()
    doc["writes"][0]["semantic_address"]["dimensions"] = {
        "ifrs-full_ComponentsOfEquityAxis": "ifrs-full_IssuedCapitalMember",
    }
    _, report = resolve_filing_doc(str(path), doc)
    assert report["ambiguous"] == 1
    issue = report["ambiguous_writes"][0]
    ready, report = resolve_filing_doc(str(path), doc,
        filing_targets={issue["resolution_key"]: "SOCIE!E6"})
    assert ready["writes"][0]["cell"] == "E6"
    assert report["operator_resolutions"]


def test_matrix_exact_label_prefers_unique_input_over_formula(tmp_path):
    path = tmp_path / "mtool.xlsx"
    _save_semantic_marker_workbook(path)
    wb = load_workbook(path)
    ws = wb.active
    ws["A6"] = ws["A5"].value
    ws["D5"] = ws["D6"] = "Profit or loss"
    ws["E6"] = "=E5"
    wb.save(path)
    wb.close()
    doc = _doc()
    doc["writes"][0]["kind"] = "MATRIX_CELL"
    doc["writes"][0]["semantic_address"]["dimensions"] = {
        "ifrs-full_ComponentsOfEquityAxis": "ifrs-full_IssuedCapitalMember",
    }
    ready, report = resolve_filing_doc(str(path), doc)
    assert report["ambiguous"] == 0, report
    assert ready["writes"][0]["cell"] == "E5"
    assert not ready["writes"][0].get("reconcile_formula")


@pytest.mark.parametrize('prefix', ['ifrs-full','ifrs-smes'])
@pytest.mark.parametrize('family', ['capital','related'])
@pytest.mark.parametrize('verified_total', [True,False])
def test_category_note_zero_total_requires_exact_table_axis_and_total_label(tmp_path, prefix, family, verified_total):
    """Native totals must preserve their disclosed aggregate category identity."""
    from openpyxl import Workbook
    table, axis, component, total = (
        ('DisclosureOfClassesOfShareCapitalTable','ClassesOfShareCapitalAxis','OrdinarySharesMember','ClassesOfShareCapitalMember')
        if family == 'capital' else
        ('DisclosureOfTransactionsBetweenRelatedPartiesTable','CategoriesOfRelatedPartiesAxis','ParentMember','EntitysTotalForRelatedPartiesMember'))
    book = Workbook(); sheet = book.active; sheet.title = 'Notes-Issuedcapital' if family == 'capital' else 'Notes-RelatedPartytran'
    href = lambda identifier: 'native.xsd#'+prefix+'_'+identifier
    sheet['E2'] = '::'.join(map(href,[table,axis,component]))
    sheet['F2'] = 0
    sheet['C3'] = '#DOM#'; sheet['D3'] = '#PRIM#'
    sheet['B3'] = '::'.join(map(href,[table,axis]))
    sheet['E3'] = 'Ordinary shares' if family == 'capital' else 'Parent'
    sheet['F3'] = 'Total' if verified_total else 'Unknown category'
    sheet['C4'] = '#ENDT#'; sheet['E4'] = sheet['F4'] = '31/12/2026'
    sheet['A6'] = href('Revenue'); sheet['D6'] = 'Source amount'
    path = tmp_path/'native.xlsx'; book.save(path); book.close()
    write = {'sheet':sheet.title,'label':'Source amount','value':125,'kind':'LEAF','period':'CY',
             'entity_scope':'Company','column_role':'company_current_year','semantic_address':{
                 'primary_concept':prefix+'_Revenue','dimensions':{prefix+'_'+axis:prefix+'_'+total}}}
    ready, report = resolve_filing_doc(str(path),{'meta':{'filing_standard':'mfrs' if prefix=='ifrs-full' else 'mpers',
                                                        'filing_level':'company'},'writes':[write], 'checks':[],
                                                'sheets':{sheet.title:{'columns':{'company_current_year':None}}}})
    assert report['mapped'] == int(verified_total), report
    assert report['unmapped'] == int(not verified_total), report
    if verified_total:
        assert ready['writes'][0]['cell'] == 'F6'


@pytest.mark.parametrize('clbg_role', [True,False])
def test_clbg_related_party_native_alias_requires_exact_role(clbg_role):
    from mtool.offline_fill import resolve_sheet_name
    cells = {'Notes-Relatedpartytransactions':{1:{'A':('S',
        'http://xbrl.ssm.com.my/role/ssm/rol_ssmt-fs-clbg_2022-12-31/ssmt-fs-clbg_2022-12-31_role-640000'
        if clbg_role else 'http://xbrl.ssm.com.my/role/ssm/rol_ssmt-fs-mfrs_2022-12-31/ssmt-fs-mfrs_2022-12-31_role-750000')},
        2:{'B':('S','native.xsd#ssmt-mfrs_DisclosureOnRelatedPartyTransactionsAbstract'),
           'E':('S','native.xsd#ifrs-full_CategoriesOfRelatedPartiesAxis')}}}
    assert resolve_sheet_name('Notes-RelatedPartytran',cells) == ('Notes-Relatedpartytransactions' if clbg_role else None)


@pytest.mark.parametrize('standard,pattern,minimum_inputs', [
    ('mfrs','mTool_MFRS_Company*NetOfTax*SOCIE_RM.xlsx',332),
    ('mpers','mTool_MPERS_Company*NetOfTax*Indirect*SOCIE_RM.xlsx',336),
    ('clbg','mTool_CLBG_Company*.xlsx',96),
])
def test_real_native_category_note_fields_round_trip_independent_xml(tmp_path, standard, pattern, minimum_inputs):
    """Every native category input retains independent raw-XML destination identity."""
    from pathlib import Path
    from scripts.audit_native_category_fields import run_workbook
    source = next((Path(__file__).resolve().parents[1]/'data').glob(pattern),None)
    if source is None:
        pytest.skip('optional native '+standard+' category workbook not present')
    result = run_workbook(source,tmp_path/'native-category-audit')
    assert result['outcomes'].get('mapped_correctly',0) >= minimum_inputs, result
    assert not any(result['outcomes'].get(k,0) for k in (
        'stored','wrong_native_destination','numeric_readback_failed','public_write_rejected')), result
    assert result['unresolved_reason_counts'] == {'template_period_section_missing':minimum_inputs}, result
    assert result['source_unchanged']
    assert not result['style_changes'] and not result['formula_changes']
    assert not result['reverse_issues'] and result['reverse_error'] is None, result
    assert all(n['outcome'] == 'rejected_as_designed' for n in result['negatives'])


@pytest.mark.parametrize('clbg_role,exact_marker', [(True,True),(False,True),(True,False)])
def test_clbg_sofp_sub_alias_requires_exact_role_and_marker(clbg_role, exact_marker):
    from mtool.offline_fill import resolve_sheet_name
    family = 'clbg' if clbg_role else 'mfrs'
    cells = {'SOFP-Sub':{1:{'A':('S',
        f'http://xbrl.ssm.com.my/role/ssm/rol_ssmt-fs-{family}_2022-12-31/ssmt-fs-{family}_2022-12-31_role-210100')},
        10:{'A':('S','native.xsd#'+('ssmt-mfrs_DisclosureOnSubclassificationOfAssetsLiabilitiesAndEquityAbstract'
                                   if exact_marker else 'ssmt-mfrs_DisclosureOnStatementOfFinancialPositionAbstract'))}}}
    assert resolve_sheet_name('SOFP-Sub-CuNonCu',cells) == ('SOFP-Sub' if clbg_role and exact_marker else None)


@pytest.mark.parametrize('clbg_role,exact_marker', [(True,True),(False,True),(True,False)])
def test_clbg_accounting_policies_alias_requires_exact_role_and_marker(clbg_role, exact_marker):
    from mtool.offline_fill import resolve_sheet_name
    family = 'clbg' if clbg_role else 'mfrs'
    cells = {'Notes-SummaryOfAcc':{1:{'A':('S',
        f'http://xbrl.ssm.com.my/role/ssm/rol_ssmt-fs-{family}_2022-12-31/ssmt-fs-{family}_2022-12-31_role-620000')},
        10:{'A':('S','native.xsd#'+('ssmt_DisclosureOnSummaryOfMaterialAccountingPoliciesAbstract'
                                   if exact_marker else 'ssmt-mfrs_DisclosureOnRelatedPartyTransactionsAbstract'))}}}
    assert resolve_sheet_name('Notes-SummaryOfAccPol',cells) == ('Notes-SummaryOfAcc' if clbg_role and exact_marker else None)
