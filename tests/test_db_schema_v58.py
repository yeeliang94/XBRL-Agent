"""v58 records each imported template's fingerprint so startup skips unchanged ones."""
import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def test_v57_upgrade_adds_template_imports_and_is_idempotent(tmp_path):
    db = tmp_path / "audit.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE template_imports")
        conn.execute("UPDATE schema_version SET version=57")
    init_db(db); init_db(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        # Empty after the upgrade, so the first start imports every template.
        assert conn.execute("SELECT COUNT(*) FROM template_imports").fetchone()[0] == 0
