"""Data Preview is projected from canonical facts, never legacy JSON."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


def _seed(tmp_path: Path):
    from concept_model.importer import import_template
    from concept_model.parser import _derive_template_id, parse_template
    from db.schema import init_db

    db_path = tmp_path / "audit.db"
    init_db(db_path)
    tree = parse_template(str(TEMPLATE))
    tree_path = tmp_path / "tree.json"
    tree_path.write_text(json.dumps(tree.to_json()), encoding="utf-8")
    import_template(db_path, tree_path)
    template_id = _derive_template_id(TEMPLATE)

    conn = sqlite3.connect(db_path)
    run_id = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES (?, ?, ?, ?)",
        ("2026-08-25T00:00:00Z", "source.pdf", "running", "2026-08-25T00:00:00Z"),
    ).lastrowid
    concept_uuid, label = conn.execute(
        "SELECT concept_uuid, canonical_label FROM concept_nodes "
        "WHERE template_id=? AND kind='LEAF' ORDER BY render_row LIMIT 1",
        (template_id,),
    ).fetchone()
    conn.execute(
        "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status, source, evidence) "
        "VALUES (?, ?, 'CY', 'Company', 4321, 'observed', 'agent', 'page 5')",
        (run_id, concept_uuid),
    )
    conn.commit()
    conn.close()
    return db_path, run_id, template_id, label


def test_preview_reads_canonical_fact_dimensions_and_coordinates(tmp_path: Path):
    from concept_model.preview import build_preview_fields

    db_path, run_id, template_id, label = _seed(tmp_path)
    fields = build_preview_fields(
        db_path, run_id, {template_id: "SOFP"},
    )

    assert len(fields) == 1
    assert fields[0]["statement"] == "SOFP"
    assert fields[0]["field_label"] == label
    assert fields[0]["value"] == 4321
    assert fields[0]["period"] == "CY"
    assert fields[0]["entity_scope"] == "Company"
    assert fields[0]["sheet"]
    assert fields[0]["row"] > 0
    assert fields[0]["col"]


def test_empty_preview_overwrites_stale_result_file(tmp_path: Path):
    from concept_model.preview import write_preview_result

    path = tmp_path / "result.json"
    path.write_text('{"fields": [{"value": "stale"}]}', encoding="utf-8")

    write_preview_result(path, [])

    assert json.loads(path.read_text(encoding="utf-8")) == {"fields": []}
