"""Shared team guidance migration preserves old runs and replays safely."""
import sqlite3

from agent_instructions import guidance_for_run
from db import repository as repo
from db.schema import CURRENT_SCHEMA_VERSION, init_db


def test_v49_migration_preserves_legacy_runs_and_is_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO runs(created_at,pdf_filename,status) VALUES('old','old.pdf','completed')")
        conn.execute("ALTER TABLE runs DROP COLUMN agent_instructions_json")
        conn.execute("DROP TABLE agent_instructions")
        conn.execute("UPDATE schema_version SET version=49")
    init_db(path)
    init_db(path)
    with repo.db_session(path) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert repo.fetch_run(conn, 1).pdf_filename == "old.pdf"
        assert repo.fetch_run(conn, 1).agent_instructions is None
        assert conn.execute("SELECT COUNT(*) FROM agent_instructions").fetchone()[0] == 0
    assert guidance_for_run(path, 1, "figures_review") == ""
