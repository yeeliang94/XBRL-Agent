"""Deterministic category-note QA using independent native XML cell expectations.

Writes only isolated audit databases and copies through the existing stdlib patcher.
Native packages are read directly with zipfile/ElementTree; expected destinations
never come from the production filing resolver.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import zipfile
from collections import Counter
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
HREF = re.compile(r'[A-Za-z0-9_.-]+\.xsd#([A-Za-z_][A-Za-z0-9_.-]*)')


@lru_cache(maxsize=None)
def schema_elements(filename):
    path = next((ROOT/'SSMxT_2022v1.0').rglob(filename),None)
    if path is None:
        return {}
    return {element.attrib['id']:element.attrib for element in ET.parse(path).getroot()
            if element.tag == '{http://www.w3.org/2001/XMLSchema}element' and 'id' in element.attrib}


def raw_workbook(path):
    """Decode raw cells without calling mtool's parser or destination resolver."""
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    shared = []
    if 'xl/sharedStrings.xml' in entries:
        shared = [''.join(n.itertext()) for n in ET.fromstring(entries['xl/sharedStrings.xml']).findall(NS+'si')]
    rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(entries['xl/_rels/workbook.xml.rels'])}
    result = {}
    for sheet in ET.fromstring(entries['xl/workbook.xml']).find(NS+'sheets'):
        target = rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
        part = target.lstrip('/') if target.startswith('/') else 'xl/'+target
        cells = {}
        for cell in ET.fromstring(entries[part]).iter(NS+'c'):
            value = cell.find(NS+'v')
            text = value.text if value is not None else ''
            if cell.attrib.get('t') == 's':
                text = shared[int(text)] if text else ''
            elif cell.attrib.get('t') == 'inlineStr':
                text = ''.join(cell.find(NS+'is').itertext())
            match = re.fullmatch(r'([A-Z]+)(\d+)', cell.attrib['r'])
            cells[(int(match[2]), match[1])] = {'text': text or '', 'formula': cell.find(NS+'f') is not None,
                                               'style': cell.attrib.get('s', '0'), 'type': cell.attrib.get('t')}
        result[sheet.attrib['name']] = cells
    return entries, result


def header_dimensions(text):
    identifiers = HREF.findall(text)
    return {axis: identifiers[i+1] for i, axis in enumerate(identifiers[:-1])
            if axis.endswith('Axis') and identifiers[i+1].endswith(('Member', 'Domain'))}


def inventory(path):
    from concept_model.dimensions import numeric_category_catalog
    standard = 'mpers' if '_MPERS_' in path.name else 'clbg' if '_CLBG_' in path.name else 'mfrs'
    roots = numeric_category_catalog(standard, roots_only=True)
    _, sheets = raw_workbook(path)
    out = []
    for sheet, cells in sheets.items():
        if not sheet.startswith('Notes-'):
            continue
        headers = [(row, col, header_dimensions(cell['text'])) for (row, col), cell in cells.items()
                   if any(axis.endswith(('ClassesOfShareCapitalAxis', 'CategoriesOfRelatedPartiesAxis'))
                          for axis in header_dimensions(cell['text']))]
        for hrow in sorted({r for r, _, _ in headers}):
            axis = next(a for r, _, d in headers if r == hrow for a in d if a in roots)
            category_row = next((r for (r,c), v in cells.items() if r > hrow and c == 'B'
                                 and axis in HREF.findall(v['text'])), None)
            if category_row:
                for (r,col), v in cells.items():
                    if r == category_row and v['text'] == 'Total':
                        zero = cells.get((hrow,col),{}).get('text','')
                        scope_only = HREF.findall(zero)
                        if zero in {'0','0:::0','0:::abc::abc::abc','0:::0:::abc::abc::abc'} or (
                            zero.startswith('0::') and scope_only and all(
                                i.endswith(('ConsolidatedAndSeparateFinancialStatementsAxis','SeparateMember','ConsolidatedMember'))
                                for i in scope_only)):
                            headers.append((hrow,col,{axis:roots[axis][0]}))
        for (row, col), cell in sorted(cells.items()):
            if col != 'A' or not HREF.findall(cell['text']):
                continue
            preceding = [r for r, _, _ in headers if r < row]
            if not preceding:
                continue
            header_row = max(preceding)
            for hrow, hcol, dims in headers:
                if hrow != header_row:
                    continue
                target = cells.get((row, hcol), {})
                category = {a:m for a,m in dims.items() if not a.endswith('ConsolidatedAndSeparateFinancialStatementsAxis')}
                scope = next(('Group' if m.endswith('_ConsolidatedMember') else 'Company'
                              for a,m in dims.items() if a.endswith('ConsolidatedAndSeparateFinancialStatementsAxis')), None)
                scope_rows = [r for (r,c),v in cells.items() if c == 'B' and hrow < r < row
                              and any(i.endswith('ConsolidatedAndSeparateFinancialStatementsAxis') for i in HREF.findall(v['text']))]
                if scope_rows:
                    scope_label = cells.get((max(scope_rows),hcol),{}).get('text')
                    scope = {'Consolidated':'Group','Separate':'Company'}.get(scope_label)
                if '_Company_' in path.name:
                    scope = 'Company'
                dates = [(r,v['text']) for (r,c),v in cells.items() if c == hcol and hrow < r < row
                         and re.search(r'\d{2}/\d{2}/\d{4}',v['text'])]
                period_text = dates[-1][1] if dates else ''
                out.append({'sheet':sheet,'cell':f'{hcol}{row}','row':row,'col':hcol,
                            'primary':HREF.findall(cell['text'])[0], 'primary_raw':cell['text'],
                            'label':cells.get((row,'D'),{}).get('text',''), 'dimensions':category,
                            'scope':scope, 'header_row':header_row, 'period_text':period_text,
                            'formula':target.get('formula',False), 'style':target.get('style'),
                            'target_present':bool(target)})
    return out


def primary_inventory(path):
    """Inventory primary fields from raw date, entity and component headers."""
    _, sheets = raw_workbook(path)
    out = []
    for sheet, cells in sheets.items():
        if not (sheet.startswith(('SOFP','SOPL','SOCI','SOCF','SOIE')) or sheet in {'SoRE','StatementofRetainedEarnings'}):
            continue
        date_rows = sorted({r for (r,c),v in cells.items() if c == 'C'
                            and v['text'] in {'#ENDT#','#STDTENDTDATE#','#ENDTDATE#'}})
        for (row,col), cell in sorted(cells.items()):
            if col != 'A' or not HREF.findall(cell['text']):
                continue
            dates = [r for r in date_rows if r < row]
            if not dates:
                out.append({'sheet':sheet,'cell':f'A{row}','row':row,'col':'A','primary':HREF.findall(cell['text'])[0],
                            'primary_raw':cell['text'],'label':cells.get((row,'D'),{}).get('text',''),
                            'dimensions':{},'scope':None,'header_row':None,'period_text':'',
                            'formula':False,'style':cell['style'],'target_present':False})
                continue
            date_row = max(dates)
            columns = {c:v['text'] for (r,c),v in cells.items() if r == date_row
                       and re.search(r'\d{2}/\d{2}/\d{4}',v['text'])}
            for target_col, period_text in columns.items():
                headers = [(r,header_dimensions(v['text'])) for (r,c),v in cells.items()
                           if c == target_col and r < date_row and header_dimensions(v['text'])]
                header_row, dims = max(headers,default=(None,{}),key=lambda item:item[0])
                dimensions = {a:m for a,m in dims.items() if not a.endswith('ConsolidatedAndSeparateFinancialStatementsAxis')}
                # Native total columns have an explicit zero marker beside a verified component axis.
                if sheet == 'SOCIE' and not dimensions:
                    hrows = [r for (r,c),v in cells.items() if r < date_row and header_dimensions(v['text'])]
                    if hrows:
                        hrow = max(hrows)
                        zero = cells.get((hrow,target_col),{}).get('text','')
                        ids = {i for (r,c),v in cells.items() if r == hrow for i in HREF.findall(v['text'])}
                        labels = [v['text'] for (r,c),v in cells.items() if c == target_col and hrow < r < date_row]
                        if zero.startswith('0') and 'Total' in labels:
                            for prefix in ('ifrs-full','ifrs-smes'):
                                axis = prefix+'_ComponentsOfEquityAxis'
                                if axis in ids and prefix+'_StatementOfChangesInEquityTable' in ids:
                                    dimensions[axis] = prefix+'_EquityMember'
                            if {'ssmt-mfrs_ComponentsOfFundAxis','ssmt-mfrs_StatementOfChangesInFundTable'} <= ids:
                                dimensions['ssmt-mfrs_ComponentsOfFundAxis'] = 'ssmt-mfrs_FundsAndReservesMember'
                scope_rows = [r for (r,c),v in cells.items() if c == 'B' and r < row
                              and any(i.endswith('ConsolidatedAndSeparateFinancialStatementsAxis') for i in HREF.findall(v['text']))]
                scope = 'Company' if '_Company_' in path.name else None
                if scope_rows:
                    label = cells.get((max(scope_rows),target_col),{}).get('text')
                    scope = {'Consolidated':'Group','Separate':'Company'}.get(label)
                target = cells.get((row,target_col),{})
                out.append({'sheet':sheet,'cell':f'{target_col}{row}','row':row,'col':target_col,
                            'primary':HREF.findall(cell['text'])[0],'primary_raw':cell['text'],
                            'label':cells.get((row,'D'),{}).get('text',''),'dimensions':dimensions,
                            'scope':scope,'header_row':header_row,'period_text':period_text,
                            'formula':target.get('formula',False),'style':target.get('style'),
                            'target_present':bool(target)})
    return out


def _label(text):
    return str(text).strip().lstrip('*').strip().lower()


def run_workbook(source, output_dir, include_primary=False):
    """Exercise all native category-note inputs with isolated public fact writes."""
    from fastapi import HTTPException
    from concept_model.bootstrap import _import_one
    from concept_model.dimensions import numeric_category_catalog, dimension_key, instance_key
    from concept_model.facts_api import FactWrite, apply_fact
    from db.schema import init_db
    from mtool.exporter import build_fill_doc
    from mtool.template_map import resolve_filing_doc
    from mtool.offline_fill import fill_workbook
    from notes_types import NotesTemplateType, notes_template_path
    standard = 'mpers' if '_MPERS_' in source.name else 'clbg' if '_CLBG_' in source.name else 'mfrs'
    level = 'group' if '_Group_' in source.name else 'company'
    first_time = '_FirstTime_' in source.name
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = inventory(source)+(primary_inventory(source) if include_primary else [])
    source_entries, source_cells = raw_workbook(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    db = output_dir/'facts.db'
    if db.exists():
        raise FileExistsError(db)
    init_db(db)
    statement_variants = {}
    if include_primary:
        from statement_types import StatementType, template_path
        variants = {'SOFP':'CuNonCu','SOPL':'Nature' if 'SOIE-Nature' in source.name else 'Function',
                    'SOCI':'BeforeTax' if 'BeforeTax' in source.name else 'NetOfTax',
                    'SOCF':'Direct' if 'SOCF-Direct' in source.name else 'Indirect',
                    'SOCIE':'SoRE' if '_SORE_' in source.name else 'Default'}
        for statement, variant in variants.items():
            if standard == 'clbg' and statement == 'SOCI':
                continue
            _import_one(db,template_path(StatementType(statement),variant,level,standard),level)
            statement_variants[statement] = variant
    for note in (NotesTemplateType.ISSUED_CAPITAL, NotesTemplateType.RELATED_PARTY):
        if standard == 'clbg' and note == NotesTemplateType.ISSUED_CAPITAL:
            continue
        _import_one(db, notes_template_path(note, level=level, standard=standard), level)
    roots = numeric_category_catalog(standard, roots_only=True)
    catalog = numeric_category_catalog(standard)
    counts = Counter()
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("INSERT INTO runs(created_at,pdf_filename,status,run_config_json) VALUES (?,?,?,?)", (
            '2026-10-03', 'CATEGORY-QA-SYNTHETIC.pdf', 'completed', json.dumps({
                'filing_standard':standard,'filing_level':level,
                'denomination':'thousands' if '_RM000' in source.name else 'units',
                'first_financial_statements':first_time,
                'statements':list(statement_variants),'variants':statement_variants,
                'notes_to_run':[n.value for n in (NotesTemplateType.ISSUED_CAPITAL, NotesTemplateType.RELATED_PARTY)
                                if standard != 'clbg' or n != NotesTemplateType.ISSUED_CAPITAL]}))).lastrowid
        conn.commit()
        nodes = list(conn.execute('SELECT n.*, sa.primary_concept FROM concept_nodes n '
                                  'JOIN concept_semantic_addresses sa ON sa.concept_uuid=n.concept_uuid '
                                  'WHERE n.is_current=1'))
        periods_by_sheet = {sheet:sorted({tuple(reversed(re.findall(r'(\d{2})/(\d{2})/(\d{4})',f['period_text'])[-1]))
                          for f in fields if f['sheet'] == sheet and re.findall(r'(\d{2})/(\d{2})/(\d{4})',f['period_text'])}, reverse=True)
                          for sheet in {f['sheet'] for f in fields}}
        from mtool.offline_fill import resolve_sheet_name
        alias_cells = {}
        for sheet, cells in source_cells.items():
            alias_cells[sheet] = {}
            for (row,col), value in cells.items():
                alias_cells[sheet].setdefault(row,{})[col] = (value['type'],value['text'])
        physical_sheets = {n['render_sheet']:resolve_sheet_name(n['render_sheet'],alias_cells) for n in nodes}
        for index, field in enumerate(fields, 1):
            periods = periods_by_sheet[field['sheet']]
            date = re.findall(r'(\d{2})/(\d{2})/(\d{4})',field['period_text'])
            field['period'] = ('CY' if tuple(reversed(date[-1])) == periods[0] else 'PY') if date else None
            matches = [n for n in nodes if n['primary_concept'] == field['primary']
                       and _label(n['canonical_label']) == _label(field['label'])]
            if not matches:
                matches = [n for n in nodes if n['primary_concept'] == field['primary']]
                field['identity_match'] = 'exact_taxonomy_unique_occurrence'
            if include_primary and not field['sheet'].startswith('Notes-'):
                if not matches:
                    matches = [n for n in nodes if n['primary_concept'] == field['primary']]
                    field['identity_match'] = 'exact_taxonomy_unique_occurrence'
                dimensional = [n for n in matches if json.loads(conn.execute(
                    'SELECT dimensions_json FROM concept_semantic_addresses WHERE concept_uuid=?',(n['concept_uuid'],)
                ).fetchone()[0] or '{}') == field['dimensions']]
                matches = dimensional
                # Exact physical/canonical sheet identity distinguishes face and analysis occurrences.
                exact_sheet = [n for n in matches if physical_sheets[n['render_sheet']] == field['sheet']]
                if exact_sheet:
                    matches = exact_sheet
            exact_presentation = [n for n in matches if n['canonical_label'].strip().casefold() == field['label'].strip().casefold()]
            if exact_presentation:
                matches = exact_presentation
            if not field['formula'] and len(matches) > 1:
                inputs = [n for n in matches if n['kind'] in {'LEAF','MATRIX_CELL'}]
                if len(inputs) == 1:
                    matches = inputs
            # Exact native taxonomy plus occurrence label distinguishes opening/closing.
            schema = re.search(r'([A-Za-z0-9_.-]+\.xsd)#',field['primary_raw'])
            declaration = schema_elements(schema[1]).get(field['primary'],{}) if schema else {}
            if declaration.get('abstract') in {'true','1'}:
                field['outcome'] = 'protected_or_noninput'
            elif declaration.get('type','').lower().endswith('textblockitemtype'):
                field['outcome'] = 'prose_not_numeric'
            elif field['formula']:
                field['outcome'] = 'native_formula_owned'
            elif matches and all(n['kind'] == 'COMPUTED' for n in matches):
                field['outcome'] = 'canonical_formula_owned'
            elif len(matches) != 1:
                semantics = conn.execute('SELECT abstract,concept_role,data_type FROM taxonomy_concepts WHERE source_element_id=?',
                                         (field['primary'],)).fetchone()
                field['outcome'] = ('prose_not_numeric' if semantics and (semantics['data_type'] or '').lower().endswith('textblockitemtype')
                                   else 'protected_or_noninput' if semantics and (semantics['abstract'] or
                                    semantics['concept_role'] != 'PRIMARY_ITEM') else 'unmatched_canonical_occurrence')
                if field['outcome'] == 'unmatched_canonical_occurrence':
                    field['detail'] = {'reason':'no_unique_canonical_occurrence',
                        'candidates':[{'uuid':n['concept_uuid'],'label':n['canonical_label'],'sheet':n['render_sheet'],
                                       'kind':n['kind']} for n in matches], 'taxonomy_declaration':declaration}
            elif matches[0]['kind'] not in {'LEAF','MATRIX_CELL'}:
                field['outcome'] = 'protected_or_noninput'
            elif matches and conn.execute('SELECT 1 FROM concept_edges WHERE parent_uuid=? LIMIT 1',
                                          (matches[0]['concept_uuid'],)).fetchone():
                field['outcome'] = 'canonical_formula_owned'
            elif field['formula']:
                field['outcome'] = 'native_formula_owned'
            elif not field['target_present'] or not field['scope'] or not field['period']:
                field['outcome'] = 'native_layout_unresolved'
            elif field['sheet'].startswith('Notes-') and any(m not in catalog.get(a,[]) for a,m in field['dimensions'].items()):
                field['outcome'] = 'taxonomy_category_unrecognized'
            else:
                node = matches[0]
                field.update(concept_uuid=node['concept_uuid'], canonical_sheet=node['render_sheet'],
                             dimension_key=instance_key(conn,node['concept_uuid'],field['dimensions']), value=10000+index*7,
                             outcome='ready')
            counts[field['outcome']] += 1
        candidates = [f for f in fields if f['outcome'] == 'ready']
        identity_values = {}
        for field in candidates:
            identity = (field['concept_uuid'],field['period'],field['scope'],field['dimension_key'])
            field['value'] = identity_values.setdefault(identity,field['value'])
        # Explicit total members carry genuine aggregate controls, separate from primitives.
        for field in candidates:
            if not field['sheet'].startswith('Notes-') or not field['dimensions']:
                continue
            axis, member = next(iter(field['dimensions'].items()))
            if member in roots.get(axis,[]):
                peers = [f for f in candidates if f['sheet'] == field['sheet'] and f['row'] == field['row']
                         and f['scope'] == field['scope'] and f['period'] == field['period']
                         and f['dimensions'].get(axis) not in roots.get(axis,[])]
                field['value'] = sum(f['value'] for f in peers)
                field['source_total_control'] = True
        for field in candidates:
            try:
                apply_fact(conn, run, FactWrite(concept_uuid=field['concept_uuid'], period=field['period'],
                    entity_scope=field['scope'], dimensions=field['dimensions'], value=field['value'],
                    source='category_native_field_audit', evidence='Synthetic independent native XML cell '+field['cell']),commit=False)
                field['outcome'] = 'stored'
            except HTTPException as exc:
                field['outcome'] = 'public_write_rejected'
                field['detail'] = exc.detail
        # Retain comparatives in normal runs; their absent native destinations remain visible.
        for field in candidates:
            if field['outcome'] == 'stored' and field['period'] == 'CY' and not first_time and len(periods_by_sheet[field['sheet']]) == 1:
                apply_fact(conn, run, FactWrite(concept_uuid=field['concept_uuid'],period='PY',entity_scope=field['scope'],
                    dimensions=field['dimensions'],value=field['value']+3,source='category_native_field_audit',
                    evidence='Synthetic comparative control: no native comparative block'),commit=False)
        negatives = []
        if candidates:
            example = next(f for f in candidates if f['sheet'].startswith('Notes-'))
            for dimensions in ({next(iter(example['dimensions'])):'not_a_taxonomy_member'},
                               {'unrecognized_axis':next(iter(example['dimensions'].values()))}):
                try:
                    apply_fact(conn, run, FactWrite(concept_uuid=example['concept_uuid'],dimensions=dimensions,
                        period=example['period'],entity_scope=example['scope'],value=999,source='category_negative'))
                    negatives.append({'outcome':'unexpected_acceptance','dimensions':dimensions})
                except HTTPException as exc:
                    negatives.append({'outcome':'rejected_as_designed','dimensions':dimensions,'detail':exc.detail})
        conn.commit()
    doc = build_fill_doc(db,run,filing_standard=standard,filing_level=level)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        config = conn.execute('SELECT run_config_json FROM runs WHERE id=?',(run,)).fetchone()[0]
        negative_run = conn.execute('INSERT INTO runs(created_at,pdf_filename,status,run_config_json) VALUES (?,?,?,?)',
            ('2026-10-03','CATEGORY-UNSPECIFIED-NEGATIVE.pdf','completed',config)).lastrowid
        conn.commit()
        examples = {f['canonical_sheet']:f for f in candidates if f['outcome'] == 'stored' and f['sheet'].startswith('Notes-')}
        for field in examples.values():
            apply_fact(conn,negative_run,FactWrite(concept_uuid=field['concept_uuid'],period='CY',
                entity_scope=field['scope'],value=91234,source='category_unspecified_negative',
                evidence='Synthetic category deliberately unspecified'))
    negative_doc = build_fill_doc(db,negative_run,filing_standard=standard,filing_level=level)
    negative_ready, negative_coverage = resolve_filing_doc(str(source),negative_doc)
    negatives.append({'case':'unspecified_category','mapped':negative_coverage['mapped'],
                      'unresolved_reason_counts':dict(Counter(w['reason_code'] for w in negative_coverage['unresolved_writes'])),
                      'outcome':'rejected_as_designed' if not negative_ready['writes'] and all(
                          w['reason_code'] == 'missing_category_dimensions' for w in negative_coverage['unresolved_writes'])
                          else 'unexpected_resolution'})
    ready, coverage = resolve_filing_doc(str(source),doc)
    expected = {}
    for field in candidates:
        if field['outcome'] == 'stored':
            expected.setdefault((field['concept_uuid'],field['period'],field['scope'],field['dimension_key']),[]).append(field)
    for write in ready['writes']:
        key = (write['concept_uuid'],write['period'],write['entity_scope'],write.get('dimension_key',''))
        for field in expected.get(key,[]):
            field['resolved_cell'] = write.get('cell')
            field['resolved_sheet'] = write.get('sheet')
            field['outcome'] = 'mapped_correctly' if (write.get('cell'),write.get('sheet')) == (field['cell'],field['sheet']) else 'wrong_native_destination'
    output = output_dir/'QA-ONLY-categorized.xlsx'
    report = fill_workbook(str(source),ready,str(output))
    output_entries, output_cells = raw_workbook(output)
    for field in [f for aliases in expected.values() for f in aliases]:
        if field['outcome'] == 'mapped_correctly':
            actual = output_cells[field['sheet']].get((field['row'],field['col']),{}).get('text','')
            field['actual'] = actual
            field['outcome'] = 'mapped_correctly' if actual and float(actual) == field['value'] else 'numeric_readback_failed'
    from eval.human_file import read_human_file, HumanFileError
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        try:
            reverse = read_human_file(conn,run,output,'thousands' if '_RM000' in source.name else 'units')
            reverse_error = None
        except HumanFileError as exc:
            reverse = None
            reverse_error = str(exc)
    reverse_values = {(f['concept_uuid'],f['period'],f['entity_scope'],f.get('dimension_key','')):f.get('value')
                      for f in (reverse.facts if reverse else [])}
    reverse_issues = []
    for key, aliases in expected.items():
        for field in aliases:
            if field['outcome'] == 'mapped_correctly':
                field['reverse_pass'] = reverse_values.get(key) == field['value']
                if not field['reverse_pass']:
                    reverse_issues.append({'sheet':field['sheet'],'cell':field['cell'], 'identity':key,
                                          'expected':field['value'],'actual':reverse_values.get(key)})
            elif field['outcome'] == 'stored':
                issues = [w for w in coverage['unresolved_writes']+coverage['ambiguous_writes']
                          if w.get('concept_uuid') == field['concept_uuid'] and w.get('period') == field['period']
                          and w.get('entity_scope') == field['scope']]
                field['outcome'] = 'export_unresolved' if issues else 'export_excluded'
                field['detail'] = issues or {'reason':'canonical_export_did_not_emit_identity'}
    formula_changes, style_changes = [], []
    for sheet, cells in source_cells.items():
        for cell, original in cells.items():
            current = output_cells[sheet].get(cell,{})
            if original['style'] != current.get('style'):
                style_changes.append([sheet,cell])
        # Exact formula XML content remains unchanged, including cached expressions.
    for name, original in source_entries.items():
        if name.startswith('xl/worksheets/') and name.endswith('.xml'):
            old = ET.fromstring(original)
            new = ET.fromstring(output_entries[name])
            formulas = lambda root: {c.attrib['r']:ET.tostring(c.find(NS+'f')).decode()
                                     for c in root.iter(NS+'c') if c.find(NS+'f') is not None}
            if formulas(old) != formulas(new):
                formula_changes.append(name)
    summary = {'filename':source.name,'standard':standard,'level':level,'first_time':first_time,
               'native_instances':len(fields),'outcomes':dict(Counter(f['outcome'] for f in fields)),
               'unresolved_reason_counts':dict(Counter(w['reason_code'] for w in coverage['unresolved_writes'])),
               'mapped':coverage['mapped'],'unmapped':coverage['unmapped'],'ambiguous':coverage['ambiguous'],
               'numeric_written':len(report['written']),'formula_mismatches':len(report['mismatches']),
               'source_sha256':source_hash, 'source_unchanged':source_hash == hashlib.sha256(source.read_bytes()).hexdigest(),
               'reverse_issues':reverse_issues, 'reverse_error':reverse_error, 'formula_changes':formula_changes,
               'style_changes':style_changes,'negatives':negatives}
    for name, payload in [('fields',fields),('summary',summary),('coverage',coverage),('fill-report',report),('doc',doc)]:
        (output_dir/f'{name}.json').write_text(json.dumps(payload,indent=2,default=str),encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--filename', help='Limit to an exact available native filename')
    parser.add_argument('--inventory-only', action='store_true')
    parser.add_argument('--include-primary', action='store_true')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for source in sorted((ROOT/'data').glob('mTool*.xlsx')):
        if args.filename and source.name != args.filename:
            continue
        fields = inventory(source)+(primary_inventory(source) if args.include_primary else [])
        manifest.append({'filename':source.name,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                         'instances':len(fields),'fields':fields})
        print(source.name, len(fields), sorted({a for f in fields for a in f['dimensions']}), flush=True)
        if not args.inventory_only:
            result = run_workbook(source,args.output_dir/source.stem,args.include_primary)
            print(json.dumps({k:v for k,v in result.items() if k != 'reverse_issues'}
                             | {'reverse_issue_count':len(result['reverse_issues'])}),flush=True)
    (args.output_dir/'inventory.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
