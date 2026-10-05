from pathlib import Path

import pytest
from concept_model.parser import parse_template

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('level', ['Company', 'Group'])
def test_order_of_liquidity_inventory_is_computed(level):
    tree = parse_template(str(ROOT / 'XBRL-template-MFRS' / level / '02-SOFP-OrderOfLiquidity.xlsx'))
    node = next(n for n in tree.concepts if n.render_key['sheet'] == 'SOFP-Sub-OrdOfLiq' and n.render_key['row'] == 134)
    assert node.kind == 'COMPUTED'
    assert len(node.edges) == 5

@pytest.mark.parametrize('level,scope', [('Company', 'Company'), ('Group', 'Group'), ('Group', 'Company')])
@pytest.mark.parametrize('period', ['CY', 'PY'])
def test_inventory_and_biological_assets_reach_total_assets(tmp_path, level, scope, period):
    import json
    import sqlite3
    from db.schema import init_db
    from concept_model.importer import import_template
    from concept_model.cascade import recompute_after_turn

    tree = parse_template(str(ROOT / 'XBRL-template-MFRS' / level / '02-SOFP-OrderOfLiquidity.xlsx'))
    db = tmp_path / 'test.db'
    init_db(db)
    tree_path = tmp_path / 'tree.json'
    tree_path.write_text(json.dumps(tree.to_json()))
    import_template(db, tree_path)
    nodes = {(n.render_key['sheet'], n.render_key['row']): n for n in tree.concepts}
    with sqlite3.connect(db) as conn:
        run_id = conn.execute("INSERT INTO runs(created_at,pdf_filename,status) VALUES ('2026-09-19','test.pdf','running')").lastrowid
        for row, value in [(129, 10888), (130, 1374), (131, 1454), (50, 80), (51, 20)]:
            conn.execute("INSERT INTO run_concept_facts(run_id,concept_uuid,period,entity_scope,value,value_status,source,updated_at) VALUES (?,?,?,?,?,'observed','test','2026-09-19')", (run_id,nodes['SOFP-Sub-OrdOfLiq',row].concept_uuid,period,scope,value))
    recompute_after_turn(db,run_id)
    with sqlite3.connect(db) as conn:
        for sheet,row,expected in [('SOFP-Sub-OrdOfLiq',134,13716),('SOFP-Sub-OrdOfLiq',52,100),('SOFP-OrdOfLiq',27,13816)]:
            value = conn.execute('SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=? AND period=? AND entity_scope=?',(run_id,nodes[sheet,row].concept_uuid,period,scope)).fetchone()
            assert value is not None and value[0] == expected


def test_all_numeric_template_graphs_have_complete_computed_dependencies(tmp_path):
    """A correct workbook formula must not disappear behind a header label."""
    import sqlite3
    from db.schema import init_db
    from concept_model.bootstrap import import_all_face_templates, import_all_notes_templates

    db = tmp_path / 'all-templates.db'
    init_db(db)
    assert len(import_all_face_templates(db)) == 38
    assert len(import_all_notes_templates(db)) == 20
    with sqlite3.connect(db) as conn:
        abstract_dependencies = conn.execute(
            "SELECT p.template_id,p.render_sheet,p.render_row,c.render_sheet,c.render_row "
            "FROM concept_edges e JOIN concept_nodes p ON p.concept_uuid=e.parent_uuid "
            "JOIN concept_nodes c ON c.concept_uuid=e.child_uuid "
            "WHERE p.is_current=1 AND c.kind='ABSTRACT'"
        ).fetchall()
        missing_dependencies = conn.execute(
            "SELECT n.template_id,n.render_sheet,n.render_row FROM concept_nodes n "
            "WHERE n.is_current=1 AND n.kind='COMPUTED' AND NOT EXISTS "
            "(SELECT 1 FROM concept_edges e WHERE e.parent_uuid=n.concept_uuid)"
        ).fetchall()
    assert abstract_dependencies == []
    assert missing_dependencies == []


def test_startup_reimports_old_header_classification_without_changing_historical_facts(tmp_path):
    import json
    import sqlite3
    from db.schema import init_db
    from concept_model.importer import import_template
    from concept_model.bootstrap import _import_one

    template = ROOT / 'XBRL-template-MFRS' / 'Company' / '02-SOFP-OrderOfLiquidity.xlsx'
    tree = parse_template(str(template))
    inventory = next(n for n in tree.concepts if n.render_key['sheet'] == 'SOFP-Sub-OrdOfLiq' and n.render_key['row'] == 134)
    inventory.kind = 'ABSTRACT'
    inventory.edges = []
    db = tmp_path / 'upgrade.db'
    init_db(db)
    tree_path = tmp_path / 'old-tree.json'
    tree_path.write_text(json.dumps(tree.to_json()))
    import_template(db, tree_path)
    with sqlite3.connect(db) as conn:
        run_id = conn.execute("INSERT INTO runs(created_at,pdf_filename,status) VALUES ('2026-09-18','historic.pdf','completed')").lastrowid
        conn.execute("INSERT INTO run_concept_facts(run_id,concept_uuid,period,entity_scope,value,value_status,source,updated_at) VALUES (?,?,'CY','Company',123,'observed','test','2026-09-18')", (run_id,inventory.concept_uuid))
    _import_one(db, template, 'company')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT kind FROM concept_nodes WHERE concept_uuid=?', (inventory.concept_uuid,)).fetchone()[0] == 'COMPUTED'
        assert conn.execute('SELECT count(*) FROM concept_edges WHERE parent_uuid=?', (inventory.concept_uuid,)).fetchone()[0] == 5
        assert conn.execute('SELECT value FROM run_concept_facts WHERE run_id=?', (run_id,)).fetchone()[0] == 123
