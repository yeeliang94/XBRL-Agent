"""Schema v40: taxonomy-semantic addresses for mTool filing."""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_fresh_init_has_semantic_address_table(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(concept_semantic_addresses)"
            )
        }
        assert {
            "concept_uuid",
            "primary_concept",
            "dimensions_json",
            "taxonomy_version",
            "address_version",
        } <= cols
        assert _version(conn) == CURRENT_SCHEMA_VERSION == 40
    finally:
        conn.close()


def test_v39_walks_forward_without_touching_canonical_facts(tmp_path):
    db = tmp_path / "v39.db"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE concept_semantic_addresses")
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'x.pdf', 'completed')"
        ).lastrowid
        conn.execute("UPDATE schema_version SET version = 39")
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(db)
    try:
        assert _version(conn) == 40
        assert conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == "completed"
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='concept_semantic_addresses'"
        ).fetchone()
    finally:
        conn.close()


def test_semantic_addresses_cascade_with_concept(tmp_path):
    db = tmp_path / "cascade.db"
    init_db(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path) VALUES ('t', 'x')"
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES ('c', 't', 'LEAF', 'Cash', 'S', 1, 'B')"
        )
        conn.execute(
            "INSERT INTO concept_semantic_addresses("
            "concept_uuid, primary_concept) VALUES ('c', 'ifrs-full_Cash')"
        )
        conn.execute("DELETE FROM concept_nodes WHERE concept_uuid = 'c'")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM concept_semantic_addresses").fetchone()[0] == 0
    finally:
        conn.close()
