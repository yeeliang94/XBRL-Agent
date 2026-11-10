"""v56 adds the run's recorded notes-side status verdicts."""
import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db
from db import repository as repo


def test_v55_upgrade_keeps_runs_and_is_idempotent(tmp_path):
    db = tmp_path / "audit.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        run_id = repo.create_run(conn, pdf_filename="a.pdf", session_id="s", output_dir="/o")
        conn.execute("ALTER TABLE runs DROP COLUMN notes_verdicts_json")
        conn.execute("UPDATE schema_version SET version=55")
    init_db(db); init_db(db)
    with repo.db_session(db) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert repo.fetch_run(conn, run_id).pdf_filename == "a.pdf"
        # A run finished before v56 has no record, which reads as "unknown".
        assert repo.fetch_run_notes_verdicts(conn, run_id) is None
        repo.save_run_notes_verdicts(conn, run_id, {"validator_failed": True})
        assert repo.fetch_run_notes_verdicts(conn, run_id) == {"validator_failed": True}
