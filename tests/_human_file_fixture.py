"""Shared setup for human-file tests: a repository template imported into a
temporary audit database, a completed run, and a real mTool fill of its facts."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from concept_model.importer import import_company_targets, import_template
from concept_model.parser import parse_template
from db.schema import init_db
from mtool.exporter import build_fill_doc
from mtool.offline_fill import fill_workbook
from mtool.template_map import resolve_filing_doc

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "XBRL-template-MFRS" / "Company"
SOFP = "mfrs-company-sofp-cunoncu-v1"


def import_template_file(db: Path, tmp_path: Path, filename: str) -> str:
    tree = parse_template(str(TEMPLATES / filename))
    payload = tmp_path / f"{filename}.json"
    payload.write_text(json.dumps(tree.to_json()), encoding="utf-8")
    import_template(db, payload)
    import_company_targets(db, tree.template_id)
    return tree.template_id


def add_run(db: Path, config: dict, status: str = "completed") -> int:
    with sqlite3.connect(db) as conn:
        return int(conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, run_config_json) "
            "VALUES ('2026-09-25', 'doc.pdf', ?, ?)",
            (status, json.dumps(config)),
        ).lastrowid)


def sofp_config(variant="CuNonCu", denomination="units") -> dict:
    return {"filing_standard": "mfrs", "filing_level": "company",
            "statements": ["SOFP"], "variants": {"SOFP": variant},
            "denomination": denomination}


def make_sofp_db(tmp_path):
    db = tmp_path / "audit.db"
    init_db(db)
    import_template_file(db, tmp_path, "01-SOFP-CuNonCu.xlsx")
    return db


def leaf_facts(db: Path) -> list[tuple[str, str, float]]:
    """Facts for a pair of same-label leaves plus two ordinary leaves."""
    with sqlite3.connect(db) as conn:
        label, sheet = conn.execute(
            "SELECT canonical_label, render_sheet FROM concept_nodes "
            "WHERE template_id = ? AND kind = 'LEAF' "
            "GROUP BY render_sheet, canonical_label HAVING COUNT(*) > 1 LIMIT 1",
            (SOFP,),
        ).fetchone()
        twins = [r[0] for r in conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
            "AND kind = 'LEAF' AND render_sheet = ? AND canonical_label = ?",
            (SOFP, sheet, label))]
        others = [r[0] for r in conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
            "AND kind = 'LEAF' AND canonical_label != ? ORDER BY render_row LIMIT 2",
            (SOFP, label))]
    facts = []
    for i, uuid in enumerate(twins[:2] + others):
        facts += [(uuid, "CY", 1000.0 * (i + 1)), (uuid, "PY", 900.0 * (i + 1))]
    return facts


def filled_file(db: Path, run_id: int, facts, tmp_path: Path) -> Path:
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status, updated_at) "
            "VALUES (?, ?, ?, 'Company', ?, 'observed', '2026-09-25')",
            [(run_id, u, p, v) for u, p, v in facts])
    doc = build_fill_doc(db, run_id, filing_standard="mfrs", filing_level="company")
    template = TEMPLATES / "01-SOFP-CuNonCu.xlsx"
    ready, _ = resolve_filing_doc(str(template), doc)
    out = tmp_path / "human.xlsx"
    assert fill_workbook(str(template), ready, str(out), strict=True)["status"] == "ok"
    return out
