"""Structured source-evidence schema migration."""
import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def test_v47_database_gains_source_receipt_tables_one_step(tmp_path):
    db = tmp_path / "legacy.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE fact_source_terms")
        conn.execute("DROP TABLE fact_source_receipts")
        conn.execute("UPDATE schema_version SET version = 47")

    init_db(db)
    init_db(db)

    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0] == CURRENT_SCHEMA_VERSION
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"fact_source_receipts", "fact_source_terms"} <= tables
        receipt_columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(fact_source_receipts)"
            )
        }
        assert {
            "run_id", "concept_uuid", "period", "entity_scope",
            "dimension_key", "transform", "arithmetic_status",
            "semantic_status", "allocation_id",
        } <= receipt_columns
