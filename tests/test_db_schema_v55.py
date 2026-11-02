"""v55 walks existing accounts forward unchanged and adds the issue tables."""
import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db
from db import repository as repo


def test_v54_upgrade_keeps_accounts_and_is_idempotent(tmp_path):
    db = tmp_path / "audit.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        repo.upsert_auth_user(conn, "a@firm.com", "A", "hash")
        for table in ("issue_files", "issues", "auth_audit_events"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("ALTER TABLE auth_users DROP COLUMN must_change_password")
        conn.execute("ALTER TABLE auth_users DROP COLUMN last_sign_in_at")
        conn.execute("UPDATE schema_version SET version=54")
    init_db(db); init_db(db)
    with repo.db_session(db) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        user = repo.fetch_auth_user(conn, "a@firm.com")
        assert user.password_hash == "hash" and user.must_change_password is False
        assert user.last_sign_in_at is None
        issue_id = repo.create_issue(conn, "a@firm.com", "other", "x", None, None)
        repo.add_issue_file(conn, issue_id, "s.png", "a.png", "image/png", 1, "0")
        repo.delete_issue(conn, issue_id)
        assert conn.execute("SELECT count(*) FROM issue_files").fetchone()[0] == 0
