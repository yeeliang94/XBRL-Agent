"""Human-filled mTool file schema migration."""
import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def test_v48_database_gains_human_file_tables_and_keeps_runs(tmp_path):
    db = tmp_path / "legacy.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE human_file_notes")
        conn.execute("DROP TABLE human_file_facts")
        conn.execute("DROP TABLE human_files")
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-09-25', 'doc.pdf', 'completed')"
        )
        conn.execute("UPDATE schema_version SET version = 48")

    init_db(db)
    init_db(db)

    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0] == CURRENT_SCHEMA_VERSION == 49
        assert conn.execute(
            "SELECT pdf_filename, status FROM runs"
        ).fetchall() == [("doc.pdf", "completed")]
        columns = {
            table: {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in ("human_files", "human_file_facts", "human_file_notes")
        }
        assert {"run_id", "filename", "sha256", "unit", "uploaded_by",
                "summary_json", "unmatched_json",
                "not_compared_json"} <= columns["human_files"]
        assert {"run_id", "concept_uuid", "period", "entity_scope",
                "dimension_key", "value", "calculated"} <= columns["human_file_facts"]
        assert {"run_id", "concept_uuid", "note_key", "html"} <= columns["human_file_notes"]


def test_deleting_a_run_removes_its_human_file(tmp_path):
    db = tmp_path / "audit.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-09-25', 'doc.pdf', 'completed')"
        ).lastrowid
        conn.execute(
            "INSERT INTO human_files(run_id, filename, sha256, unit, uploaded_at) "
            "VALUES (?, 'human.xlsx', 'abc', 'units', '2026-09-25')", (run_id,))
        conn.execute(
            "INSERT INTO human_file_facts(run_id, concept_uuid, period, "
            "entity_scope, value) VALUES (?, 'u1', 'CY', 'Company', 5)", (run_id,))
        conn.execute(
            "INSERT INTO human_file_notes(run_id, concept_uuid, note_key, html) "
            "VALUES (?, 'n1', 'fn_1', '<p>x</p>')", (run_id,))
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        for table in ("human_files", "human_file_facts", "human_file_notes"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
