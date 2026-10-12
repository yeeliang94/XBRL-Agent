"""Table re-copy history migrates onto existing runs without changing notes."""
import sqlite3
from db import repository as repo
from db.schema import CURRENT_SCHEMA_VERSION, init_db


def test_v51_block_revisions_migrate_without_changing_notes(tmp_path):
    path = tmp_path / "legacy.db"
    init_db(path)
    with repo.db_session(path) as conn:
        rid = repo.create_run(conn, "old.pdf", session_id="s", output_dir=str(tmp_path))
        repo.upsert_notes_cell(conn, run_id=rid, sheet="Notes", row=10, label="Note", html="<p>Original.</p>")
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE notes_source_block_revisions")
        conn.execute("UPDATE schema_version SET version=51")
    init_db(path)
    init_db(path)
    with repo.db_session(path) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert conn.execute("SELECT html FROM notes_cells").fetchone()[0] == "<p>Original.</p>"
        assert conn.execute("SELECT COUNT(*) FROM notes_source_block_revisions").fetchone()[0] == 0
