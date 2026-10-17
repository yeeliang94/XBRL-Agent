"""Typed reports migrate without losing earlier filings or breaking cascade deletion."""
import sqlite3
from db.schema import init_db, CURRENT_SCHEMA_VERSION
from db import repository as repo


def test_v53_upgrade_fresh_schema_and_idempotency_preserve_filings(tmp_path):
    db = tmp_path / "audit.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        rid = repo.create_run(conn, "historical.pdf", status="completed")
        conn.execute("DROP TABLE report_fields")
        conn.execute("UPDATE schema_version SET version=53")
    init_db(db); init_db(db)
    with repo.db_session(db) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert repo.fetch_run(conn, rid).pdf_filename == "historical.pdf"
        conn.execute("INSERT INTO report_fields(run_id,report_type,concept_id,target_id,value_kind,value_status,updated_at) VALUES(?,?,?,?,?,?,?)",
                     (rid,"AUDITORS_REPORT","field","target","text","unresolved","2026-10-17"))
        conn.execute("DELETE FROM runs WHERE id=?", (rid,))
        assert conn.execute("SELECT count(*) FROM report_fields").fetchone()[0] == 0
