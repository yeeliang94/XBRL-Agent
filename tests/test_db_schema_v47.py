"""Category migration preserves legacy identity, IDs and audit history."""
import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, _CREATE_STATEMENTS, init_db


def test_legacy_unique_keys_are_rebuilt_without_guessing_categories(tmp_path):
    db = tmp_path / 'legacy.db'
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO runs(id,created_at,pdf_filename,status) VALUES (1,'t','synthetic.pdf','completed')")
        conn.execute("INSERT INTO concept_templates(template_id,source_path) VALUES ('synthetic','synthetic.xlsx')")
        conn.execute("INSERT INTO concept_nodes(concept_uuid,template_id,kind,canonical_label,render_sheet,render_row,render_col) VALUES ('leaf','synthetic','LEAF','Capital','Capital',1,'B')")
        for table in ('run_concept_facts', 'run_fact_snapshots', 'gold_concept_facts'):
            conn.execute(f'DROP TABLE {table}')
            ddl = next(s for s in _CREATE_STATEMENTS if f'CREATE TABLE IF NOT EXISTS {table} (' in s)
            ddl = ddl.replace("        dimension_key    TEXT NOT NULL DEFAULT '',\n", '').replace('entity_scope, dimension_key)', 'entity_scope)')
            conn.execute(ddl)
        conn.execute("INSERT INTO run_concept_facts(id,run_id,concept_uuid,period,entity_scope,value,value_status) VALUES (7,1,'leaf','PY','Company',123,'observed')")
        conn.execute('UPDATE schema_version SET version=46')
    init_db(db)
    init_db(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert conn.execute('SELECT id,value,dimension_key FROM run_concept_facts').fetchall() == [(7,123,'')]
        conn.execute("INSERT INTO run_concept_facts(run_id,concept_uuid,period,entity_scope,value,value_status,dimension_key) VALUES (1,'leaf','PY','Company',15,'observed','{\"ClassAxis\":\"Preference\"}')")
        assert conn.execute('SELECT COUNT(*) FROM run_concept_facts').fetchone()[0] == 2
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
