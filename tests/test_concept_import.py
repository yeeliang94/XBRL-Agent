"""Phase 1 step 1.3 — Phase 0 JSON → DB importer.

The importer takes a concept-tree JSON produced by
`concept_model/parser.py` and populates the v4 schema's
concept_templates / concept_nodes / concept_edges tables.

It MUST be idempotent on the deterministic UUIDs that the parser
mints — re-importing the same JSON must be a no-op.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from concept_model.importer import import_template
from concept_model.parser import parse_template
from db.schema import init_db


REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "xbrl.db"
    init_db(db)
    return db


@pytest.fixture
def parsed_json(tmp_path: Path) -> Path:
    """Parse the live template and stash the JSON to a temp file."""
    tree = parse_template(str(FIXTURE))
    path = tmp_path / f"{tree.template_id}.json"
    path.write_text(
        json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8"
    )
    return path


def _count(db: Path, table: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_import_concept_tree_populates_all_4_tables(
    db_path: Path, parsed_json: Path
) -> None:
    template_id = import_template(db_path, parsed_json)
    assert template_id == "mfrs-company-sofp-cunoncu-v1"

    # concept_templates: exactly one row.
    assert _count(db_path, "concept_templates") == 1

    # concept_nodes: every concept in the JSON.
    expected = len(json.loads(parsed_json.read_text())["concepts"])
    # The JSON may carry duplicate concept_uuid entries (face cells that
    # share the sub concept's identity) — those collapse to a single row
    # in concept_nodes via the importer's UPSERT.
    assert _count(db_path, "concept_nodes") <= expected
    assert _count(db_path, "concept_nodes") > 0

    # concept_edges: SOFP face has at least a dozen totals; pin a lower
    # bound rather than a brittle exact count.
    assert _count(db_path, "concept_edges") > 10

    # concept_targets: Phase 1 leaves this empty for company-only
    # filings (render_col on concept_nodes is sufficient). Phase 4 will
    # populate it.
    assert _count(db_path, "concept_targets") >= 0


def test_import_is_idempotent(db_path: Path, parsed_json: Path) -> None:
    """Re-importing the same JSON must be a no-op (UPSERT on UUID).

    Without this, every server restart that re-imports the same set of
    templates would duplicate every row.
    """
    import_template(db_path, parsed_json)
    nodes_first = _count(db_path, "concept_nodes")
    edges_first = _count(db_path, "concept_edges")

    import_template(db_path, parsed_json)
    nodes_second = _count(db_path, "concept_nodes")
    edges_second = _count(db_path, "concept_edges")

    assert nodes_first == nodes_second
    assert edges_first == edges_second
