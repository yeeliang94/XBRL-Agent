"""Category identities must survive writes, review and filing snapshots."""
import sqlite3
from dataclasses import replace

import pytest

from concept_model.dimensions import dimension_key
from concept_model.facts_api import FactWrite, write_fact
from concept_model.versioning import snapshot_facts, compute_review_diff, revert_to_original
from test_facts_api_inprocess import db_and_run


@pytest.mark.parametrize('separate_calls', [False, True])
def test_numeric_note_payloads_reach_distinct_native_categories(tmp_path, separate_calls):
    from openpyxl import Workbook, load_workbook
    from notes.payload import NotesPayload
    from notes.agent import NotesDeps, _write_payloads_through_writer_impl
    from token_tracker import TokenReport
    from notes_types import NotesTemplateType, notes_template_path
    from concept_model.cell_resolver import project_writes
    from db.schema import init_db
    from mtool.exporter import build_fill_doc
    from mtool.template_map import resolve_filing_doc
    from mtool.offline_fill import fill_workbook, verify_numeric_snapshot
    from test_mtool_exporter import _import, _init_run

    db = tmp_path / 'audit.db'
    init_db(db)
    template = notes_template_path(NotesTemplateType.ISSUED_CAPITAL, level='company')
    tid = _import(db, template)
    run = _init_run(db)
    axis = 'ifrs-full_ClassesOfShareCapitalAxis'
    members = ['ifrs-full_OrdinarySharesMember', 'ssmt-mfrs_RedeemablePreferenceSharesMember']
    payloads = [NotesPayload(
        chosen_row_label='*Number of shares issued and fully paid', content='',
        evidence='Page 4: synthetic share register, class ' + member,
        parent_note={'number': '4', 'title': 'Share capital'},
        numeric_values={'cy': value, 'py': value - 5}, dimensions={axis: member},
    ) for member, value in zip(members, [100, 20])]
    deps = NotesDeps(
        pdf_path=str(tmp_path / 'source.pdf'), template_path=str(template),
        model=None, output_dir=str(tmp_path), token_report=TokenReport(model='test'),
        template_type=NotesTemplateType.ISSUED_CAPITAL, sheet_name='Notes-Issuedcapital',
        filing_level='company', filled_filename='diagnostic.xlsx',
    )
    # Separate calls also replace an earlier reading of the same class without
    # replacing the other class at the same worksheet coordinates.
    batches = [[replace(payloads[0], numeric_values={'cy': 99, 'py': 94})],
               [payloads[1]], [payloads[0]]] if separate_calls else [payloads]
    for batch in batches:
        result = _write_payloads_through_writer_impl(deps, batch)
        assert result.success, result.errors
    projected = project_writes(db, run, tid, deps.numeric_cells)
    assert projected.projected == 4
    from api.notes import _numeric_sheet_rows
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = _numeric_sheet_rows(conn, run, tid, 'Notes-Issuedcapital', 'company')
    row = next(r for r in rows if r.get('categories'))
    assert sorted(c['values']['cy'] for c in row['categories']) == [20, 100]
    assert sorted(c['values']['py'] for c in row['categories']) == [15, 95]
    doc = build_fill_doc(db, run, filing_standard='mfrs', filing_level='company')
    assert len(doc['writes']) == 4
    assert len({w['dimension_key'] for w in doc['writes']}) == 2

    wb = Workbook(); ws = wb.active; ws.title = 'Notes-Issuedcapital'
    prefix = 'synthetic.xsd#'
    ws['C3'] = '#DOM#'; ws['D3'] = '#PRIM#'; ws['C5'] = '#ENDT#'
    for col, member in zip('EF', members):
        ws[f'{col}2'] = '::'.join(prefix + item for item in ['ifrs-full_DisclosureOfClassesOfShareCapitalTable', axis, member])
        ws[f'{col}5'] = '31/12/2026'
    ws['A7'] = prefix + doc['writes'][0]['semantic_address']['primary_concept']
    ws['D7'] = payloads[0].chosen_row_label
    native = tmp_path / 'native.xlsx'; wb.save(native); wb.close()
    ready, coverage = resolve_filing_doc(str(native), doc)
    assert coverage['unmapped'] == 2  # Comparatives persist; no PY section.
    assert all(w['period'] == 'CY' for w in ready['writes'])
    output = tmp_path / 'filled.xlsx'
    assert fill_workbook(str(native), ready, str(output))['status'] == 'ok'
    assert verify_numeric_snapshot(str(output), ready)['status'] == 'ok'
    filled = load_workbook(output)
    assert [filled.active[f'{c}7'].value for c in 'EF'] == [100, 20]
    filled.close()
    repeated = tmp_path / 'repeated.xlsx'
    assert fill_workbook(str(output), ready, str(repeated))['status'] == 'ok'
    assert repeated.read_bytes() == output.read_bytes()
    # A template explicitly declaring a comparative block gets both periods;
    # the current-only template above never receives PY figures in CY cells.
    comparative = load_workbook(native)
    ws = comparative.active
    ws['C12'] = '#DOM#'; ws['D12'] = '#PRIM#'; ws['C14'] = '#ENDT#'
    for col in 'EF':
        ws[f'{col}11'] = ws[f'{col}2'].value
        ws[f'{col}14'] = '31/12/2025'
    ws['A16'] = ws['A7'].value
    ws['D16'] = ws['D7'].value
    comparative_path = tmp_path / 'comparative.xlsx'
    comparative.save(comparative_path); comparative.close()
    ready, coverage = resolve_filing_doc(str(comparative_path), doc)
    assert coverage['unmapped'] == coverage['ambiguous'] == 0, coverage
    complete = tmp_path / 'both-periods.xlsx'
    assert fill_workbook(str(comparative_path), ready, str(complete))['status'] == 'ok'
    assert verify_numeric_snapshot(str(complete), ready)['status'] == 'ok'
    filled = load_workbook(complete)
    assert [filled.active[ref].value for ref in ['E7', 'F7', 'E16', 'F16']] == [100, 20, 95, 15]
    filled.close()


def test_dimension_order_is_not_identity():
    assert dimension_key({'b': 'two', 'a': 'one'}) == dimension_key({'a': 'one', 'b': 'two'})
    assert dimension_key({}) == ''
    with pytest.raises(ValueError):
        dimension_key({'': 'unknown'})


def test_numeric_category_replacement_uses_submission_order_in_both_outputs(tmp_path):
    from notes.payload import NotesPayload
    from notes.writer import write_notes_workbook
    from notes_types import NotesTemplateType, notes_template_path
    from openpyxl import load_workbook
    payloads = [NotesPayload(
        chosen_row_label='*Number of shares issued and fully paid', content='',
        parent_note={'number': '4', 'title': 'Share capital'}, source_pages=[page],
        evidence=f'Page {page}: synthetic share count',
        numeric_values={'cy': value, **({'py': 40} if page == 9 else {})},
        dimensions={'ifrs-full_ClassesOfShareCapitalAxis': 'ifrs-full_OrdinarySharesMember'},
    ) for page, value in [(9, 50), (3, 60)]]
    path = tmp_path / 'diagnostic.xlsx'
    result = write_notes_workbook(str(notes_template_path(NotesTemplateType.ISSUED_CAPITAL, level='company')),
                                 payloads, str(path), 'company', 'Notes-Issuedcapital')
    assert len(result.numeric_cells) == 2
    assert sorted(c['value'] for c in result.numeric_cells) == [40, 60]
    book = load_workbook(path)
    for cell in result.numeric_cells:
        assert book[cell['sheet']].cell(cell['row'], cell['col']).value == cell['value']
    book.close()


@pytest.mark.parametrize('level', ['company', 'group'])
def test_categories_coexist_and_revert_independently(tmp_path, level):
    from notes_types import NotesTemplateType, notes_template_path
    from db.schema import init_db
    from test_mtool_exporter import _import, _init_run
    db = tmp_path / 'facts.db'
    init_db(db)
    tid = _import(db, notes_template_path(NotesTemplateType.RELATED_PARTY, level=level), group=level == 'group')
    run = _init_run(db)
    with sqlite3.connect(db) as conn:
        uuid = conn.execute("SELECT concept_uuid FROM concept_nodes WHERE template_id=? AND kind='LEAF' AND canonical_label NOT LIKE '%Disclosure%' ORDER BY render_row LIMIT 1", (tid,)).fetchone()[0]
    axis = 'ifrs-full_CategoriesOfRelatedPartiesAxis'
    members = ['ifrs-full_ParentMember', 'ifrs-full_AssociatesMember']
    scope = 'Group' if level == 'group' else 'Company'
    for category, value in zip(members, [100, 20]):
        write_fact(db, run, FactWrite(concept_uuid=uuid, dimensions={axis: category}, entity_scope=scope, value=value))
    snapshot_facts(db, run)
    write_fact(db, run, FactWrite(concept_uuid=uuid, dimensions={axis: members[0]}, entity_scope=scope, value=110))
    changes = compute_review_diff(db, run)
    assert len(changes) == 1
    assert changes[0]['original'] == 100
    assert changes[0]['current'] == 110
    revert_to_original(db, run)
    with sqlite3.connect(db) as conn:
        values = conn.execute('SELECT dimension_key,value FROM run_concept_facts WHERE concept_uuid=? ORDER BY dimension_key', (uuid,)).fetchall()
    assert values == sorted([(dimension_key({axis: member}), value) for member, value in zip(members, [100, 20])])


def test_face_fact_cannot_acquire_an_unsupported_category(db_and_run):
    from fastapi import HTTPException
    db, run, uuid = db_and_run
    with pytest.raises(HTTPException, match='additional category axis'):
        write_fact(db, run, FactWrite(concept_uuid=uuid, dimensions={'ClassAxis': 'Ordinary'}, value=1))


@pytest.fixture
def unresolved_category(tmp_path):
    """A source value whose category has deliberately not been guessed."""
    from db.schema import init_db
    from notes_types import NotesTemplateType, notes_template_path
    from test_mtool_exporter import _import, _init_run
    db = tmp_path / 'category.db'
    init_db(db)
    tid = _import(db, notes_template_path(NotesTemplateType.ISSUED_CAPITAL, level='company'))
    run = _init_run(db)
    with sqlite3.connect(db) as conn:
        uuid = conn.execute("SELECT concept_uuid FROM concept_nodes WHERE template_id=? "
                            "AND canonical_label='*Number of shares issued and fully paid'", (tid,)).fetchone()[0]
    write_fact(db, run, FactWrite(concept_uuid=uuid, value=100, evidence='Page 4: share register'))
    write_fact(db, run, FactWrite(concept_uuid=uuid, period='PY', value=90))
    return db, run, uuid, tid


def _resolution_request(db, run, uuid, **changes):
    from concept_model.facts_api import CategoryResolution, category_resolution_token
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        token = category_resolution_token(conn, run, uuid, 'CY', 'Company')
    values = dict(dimensions={'ifrs-full_ClassesOfShareCapitalAxis': 'ifrs-full_OrdinarySharesMember'},
                  expected_token=token, evidence='Page 4: source identifies ordinary shares')
    values.update(changes)
    return CategoryResolution(**values)


def test_confirmed_category_survives_review_export_and_revert(unresolved_category):
    from api.notes import _numeric_sheet_rows
    from concept_model.facts_api import register_facts_routes
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mtool.exporter import build_fill_doc
    db, run, uuid, tid = unresolved_category
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO fact_source_receipts(run_id,concept_uuid,period,entity_scope,"
                     "transform,arithmetic_status,semantic_status,created_at) VALUES(?,?,'CY','Company',"
                     "'literal','passed','matched','2026-10-03')", (run, uuid))
        conn.row_factory = sqlite3.Row
        row = next(r for r in _numeric_sheet_rows(conn, run, tid, 'Notes-Issuedcapital', 'company')
                   if r.get('concept_uuid') == uuid)
    request = _resolution_request(db, run, uuid)
    assert row['categories'][0]['resolution_tokens']['cy'] == request.expected_token
    assert request.dimensions in [option['dimensions'] for option in row['category_options']]
    app = FastAPI()
    register_facts_routes(app, lambda: db)
    with TestClient(app) as client:
        response = client.post(f'/api/runs/{run}/facts/{uuid}/category', json=request.model_dump())
    assert response.status_code == 200
    result = response.json()
    assert result['value'] == 100
    doc = build_fill_doc(db, run, filing_standard='mfrs', filing_level='company')
    cy = [w for w in doc['writes'] if w['concept_uuid'] == uuid and w['period'] == 'CY']
    assert len(cy) == 1 and cy[0]['dimension_key'] == dimension_key(request.dimensions)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT period,dimension_key,value FROM run_concept_facts WHERE concept_uuid=? "
                            "ORDER BY period", (uuid,)).fetchall() == [
            ('CY', dimension_key(request.dimensions), 100), ('PY', '', 90)]
        assert conn.execute("SELECT COUNT(*) FROM concept_fact_events WHERE concept_uuid=? "
                            "AND after_json IS NULL AND dimension_key=''", (uuid,)).fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM fact_source_receipts WHERE run_id=?', (run,)).fetchone()[0] == 0
        assert conn.execute("SELECT evidence FROM run_concept_facts WHERE concept_uuid=? AND period='CY'",
                            (uuid,)).fetchone()[0] == 'Page 4: share register\n' + request.evidence
    assert len(compute_review_diff(db, run)) == 2  # Removal and source-confirmed addition.
    assert revert_to_original(db, run)['reverted']
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT period,dimension_key,value FROM run_concept_facts WHERE concept_uuid=? "
                            "ORDER BY period", (uuid,)).fetchall() == [('CY', '', 100), ('PY', '', 90)]


@pytest.mark.parametrize('boundary', ['stale', 'occupied', 'wrong_axis', 'wrong_family',
                                     'wrong_scope', 'first_time_py', 'conflict', 'blank_evidence'])
def test_category_resolution_refuses_unsafe_moves_without_partial_writes(unresolved_category, boundary):
    import json
    from fastapi import HTTPException
    from concept_model.facts_api import resolve_fact_category
    db, run, uuid, _ = unresolved_category
    request = _resolution_request(db, run, uuid)
    code = 400
    if boundary == 'stale':
        write_fact(db, run, FactWrite(concept_uuid=uuid, value=101))
        code = 409
    elif boundary == 'occupied':
        write_fact(db, run, FactWrite(concept_uuid=uuid, dimensions=request.dimensions, value=12))
        code = 409
    elif boundary == 'wrong_axis':
        request.dimensions = {'ifrs-full_CategoriesOfRelatedPartiesAxis': 'ifrs-full_ParentMember'}
    elif boundary == 'wrong_family':
        with sqlite3.connect(db) as conn:
            conn.execute('UPDATE runs SET run_config_json=? WHERE id=?',
                         (json.dumps({'filing_standard': 'mpers'}), run))
    elif boundary == 'wrong_scope':
        request.entity_scope = 'Group'
    elif boundary == 'first_time_py':
        with sqlite3.connect(db) as conn:
            conn.execute('UPDATE runs SET run_config_json=? WHERE id=?',
                         (json.dumps({'first_financial_statements': True}), run))
        request.period = 'PY'
    elif boundary == 'conflict':
        write_fact(db, run, FactWrite(concept_uuid=uuid, value=100, value_status='conflict'))
        request = _resolution_request(db, run, uuid)
        code = 409
    elif boundary == 'blank_evidence':
        request.evidence = ' '
    with sqlite3.connect(db) as conn:
        before = conn.execute('SELECT * FROM run_concept_facts ORDER BY dimension_key').fetchall()
        events = conn.execute('SELECT COUNT(*) FROM concept_fact_events').fetchone()[0]
    with pytest.raises(HTTPException) as exc:
        resolve_fact_category(db, run, uuid, request)
    assert exc.value.status_code == code
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT * FROM run_concept_facts ORDER BY dimension_key').fetchall() == before
        assert conn.execute('SELECT COUNT(*) FROM concept_fact_events').fetchone()[0] == events
        assert conn.execute('SELECT COUNT(*) FROM run_fact_snapshots').fetchone()[0] == 0


@pytest.mark.parametrize('standard,level', [('mfrs', 'group'), ('mpers', 'company'),
                                           ('mpers', 'group')])
def test_category_resolution_uses_exact_run_family_and_scope(tmp_path, standard, level):
    import json
    from db.schema import init_db
    from notes_types import NotesTemplateType, notes_template_path
    from test_mtool_exporter import _import, _init_run
    from concept_model.dimensions import numeric_category_catalog
    from concept_model.facts_api import resolve_fact_category, CategoryResolution, category_resolution_token
    from mtool.exporter import build_fill_doc
    db = tmp_path / 'family.db'
    init_db(db)
    tid = _import(db, notes_template_path(NotesTemplateType.RELATED_PARTY, level=level,
                                        standard=standard), group=level == 'group')
    run = _init_run(db)
    scope = 'Group' if level == 'group' else 'Company'
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE runs SET run_config_json=? WHERE id=?',
                     (json.dumps({'filing_standard': standard, 'filing_level': level}), run))
        uuid = conn.execute("SELECT concept_uuid FROM concept_nodes WHERE template_id=? AND kind='LEAF' "
                            "AND canonical_label NOT LIKE '%Disclosure%' ORDER BY render_row LIMIT 1", (tid,)).fetchone()[0]
    write_fact(db, run, FactWrite(concept_uuid=uuid, entity_scope=scope, value=23))
    catalog = numeric_category_catalog(standard)
    axis = next(a for a in catalog if a.endswith('CategoriesOfRelatedPartiesAxis'))
    member = next(m for m in catalog[axis] if m.endswith('ParentMember'))
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        token = category_resolution_token(conn, run, uuid, 'CY', scope)
    resolve_fact_category(db, run, uuid, CategoryResolution(dimensions={axis: member}, entity_scope=scope,
                          expected_token=token, evidence='Page 8: transaction with parent'))
    writes = build_fill_doc(db, run, filing_standard=standard, filing_level=level)['writes']
    fact, = [w for w in writes if w['concept_uuid'] == uuid]
    assert fact['value'] == 23 and fact['entity_scope'] == scope
    assert fact['dimension_key'] == dimension_key({axis: member})


def test_category_move_rolls_back_target_audit_and_snapshot_on_storage_failure(unresolved_category):
    from concept_model.facts_api import resolve_fact_category
    db, run, uuid, _ = unresolved_category
    request = _resolution_request(db, run, uuid)
    with sqlite3.connect(db) as conn:
        before = conn.execute('SELECT * FROM run_concept_facts').fetchall()
        events = conn.execute('SELECT COUNT(*) FROM concept_fact_events').fetchone()[0]
        conn.execute("CREATE TRIGGER reject_category_removal BEFORE DELETE ON run_concept_facts "
                     "WHEN OLD.dimension_key='' BEGIN SELECT RAISE(ABORT,'storage failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='storage failure'):
        resolve_fact_category(db, run, uuid, request)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT * FROM run_concept_facts').fetchall() == before
        assert conn.execute('SELECT COUNT(*) FROM concept_fact_events').fetchone()[0] == events
        assert conn.execute('SELECT COUNT(*) FROM run_fact_snapshots').fetchone()[0] == 0


def test_first_time_notes_hide_retained_comparatives_without_deleting_them(unresolved_category):
    import json
    from api.notes import _numeric_sheet_rows
    db, run, uuid, tid = unresolved_category
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE runs SET run_config_json=? WHERE id=?',
                     (json.dumps({'first_financial_statements': True}), run))
        conn.row_factory = sqlite3.Row
        row = next(r for r in _numeric_sheet_rows(conn, run, tid, 'Notes-Issuedcapital', 'company')
                   if r.get('concept_uuid') == uuid)
        assert row['values'] == {'cy': 100}
        assert row['categories'][0]['values'] == {'cy': 100}
        assert set(row['categories'][0]['resolution_tokens']) == {'cy'}
        assert conn.execute("SELECT value FROM run_concept_facts WHERE concept_uuid=? AND period='PY'",
                            (uuid,)).fetchone()[0] == 90
