"""Offline native text-block audit; expected trigger cells come from raw XML."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import sqlite3
from scripts.audit_native_category_fields import ROOT, HREF, raw_workbook, schema_elements
from scripts.audit_field_roundtrip import import_for_audit
from concept_model.filing_targets import active_template_paths, targets_for_template
from concept_model.facts_api import FactWrite, apply_fact
from db.schema import init_db
from mtool.offline_fill import resolve_sheet_name, fill_footnotes
from mtool.notes_exporter import build_notes_fill_doc
import re


def run(source, directory):
    directory.mkdir(parents=True,exist_ok=True)
    db = directory/'prose.db'
    if db.exists():
        raise FileExistsError(db)
    init_db(db)
    standard = source.name.split('_')[1].lower()
    level = 'group' if '_Group_' in source.name else 'company'
    original, cells = raw_workbook(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    alias = {}
    for sheet, values in cells.items():
        alias[sheet] = {}
        for (row,col), value in values.items():
            alias[sheet].setdefault(row,{})[col] = (value['type'],value['text'])
    targets = []
    for path in active_template_paths(ROOT):
        if path.parent.name.lower() != level or path.parent.parent.name.lower() != 'xbrl-template-'+standard:
            continue
        _, candidates = targets_for_template(path)
        html = [t for t in candidates if t.writable and t.value_kind == 'html']
        if html:
            import_for_audit(db,path,level,candidates)
            targets.extend(html)
    fields = []
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        run_id = conn.execute('INSERT INTO runs(created_at,pdf_filename,status,run_config_json) VALUES(?,?,?,?)',
            ('2026-10-03','SYNTHETIC-PROSE.pdf','completed',json.dumps({'filing_standard':standard,'filing_level':level}))).lastrowid
        conn.commit()
        for sheet, values in cells.items():
            for (row,col), value in values.items():
                if col != 'A':
                    continue
                ids = HREF.findall(value['text'])
                schema = re.search(r'([A-Za-z0-9_.-]+\.xsd)#',value['text'])
                if not ids or not schema:
                    continue
                declaration = schema_elements(schema[1]).get(ids[-1],{})
                if not declaration.get('type','').lower().endswith('textblockitemtype'):
                    continue
                field = {'sheet':sheet,'row':row,'cell':'E'+str(row),'primary':ids[-1],
                         'label':values.get((row,'D'),{}).get('text','')}
                if declaration.get('abstract') in {'true','1'}:
                    field.update(outcome='protected_abstract_textblock',reason='taxonomy_abstract_not_writable')
                    fields.append(field)
                    continue
                matched = [t for t in targets if t.taxonomy_element_id == ids[-1]
                           and resolve_sheet_name(t.sheet,alias) == sheet]
                if len(matched) != 1:
                    field.update(outcome='unaddressed_native_textblock',reason='no_unique_supported_canonical_html_field',
                                 candidate_count=len(matched))
                else:
                    target = matched[0]
                    html = '<p>Native prose field '+str(len(fields)+1)+' &amp; source evidence.</p>'
                    try:
                        apply_fact(conn,run_id,FactWrite(concept_uuid=target.canonical_target_id,html=html,
                            sheet=target.sheet,row=target.row,label=target.label,actor='native-prose-audit'))
                        field.update(outcome='stored',html=html,concept_uuid=target.canonical_target_id)
                    except Exception as exc:
                        field.update(outcome='public_write_rejected',reason=str(exc))
                fields.append(field)
    doc = build_notes_fill_doc(db,run_id,decorate=False)
    report = fill_footnotes(str(source),doc,str(directory/'QA-ONLY-prose.xlsx'),create_missing=True)
    _, output_cells = raw_workbook(directory/'QA-ONLY-prose.xlsx')
    style_changes = [(sheet,row,col) for sheet,values in cells.items() for (row,col),value in values.items()
                     if output_cells[sheet].get((row,col),{}).get('style') != value['style']]
    formula_changes = [(sheet,row,col) for sheet,values in cells.items() for (row,col),value in values.items()
                       if output_cells[sheet].get((row,col),{}).get('formula') != value['formula']]
    by_identity = {(f['sheet'],f['cell']):f for f in fields if f['outcome']=='stored'}
    for write in report['footnotes_written']:
        field = by_identity.get((write.get('sheet'),write.get('cell')))
        if field:
            field['outcome']='forward_and_exact_xhtml_readback_passed' if not report.get('footnote_mismatches') else 'xhtml_readback_failed'
            field['native_key']=write.get('key')
    for field in fields:
        if field['outcome']=='stored':
            field.update(outcome='export_unresolved',reason='no_exact_native_trigger_written')
    summary = {'filename':source.name,'instances':len(fields),'outcomes':dict(Counter(f['outcome'] for f in fields)),
               'source_unchanged':source_hash==hashlib.sha256(source.read_bytes()).hexdigest(),
               'style_changes':style_changes,'formula_changes':formula_changes,
               'footnote_mismatches':report.get('footnote_mismatches',[]),
               'reverse_ingest_scope':'numeric human-file reader excludes prose; exact XHTML patcher readback verified'}
    for name,payload in [('fields',fields),('summary',summary),('fill-report',report),('doc',doc)]:
        (directory/(name+'.json')).write_text(json.dumps(payload,indent=2,default=str),encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=__import__('pathlib').Path,required=True)
    parser.add_argument('--filename')
    args=parser.parse_args()
    for source in sorted((ROOT/'data').glob('mTool*.xlsx')):
        if args.filename and source.name != args.filename:
            continue
        print(json.dumps(run(source,args.output_dir/source.stem)),flush=True)
