"""DB migration v19 -> v20: the auth_users.is_admin column (admin role).

The privilege boundary for web-based user management (Settings → Users tab +
/api/admin/* routes). One additive ALTER (NOT NULL DEFAULT 0) — same pinning
shape as the v15/v16/v17 column steps: fresh init carries the column + version
20, a v19 fixture walks forward with existing rows defaulting to non-admin, and
re-init is idempotent.
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db
from db import repository as repo


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_current_schema_version_is_at_least_v20():
    # >= so a later bump doesn't break this pin (resilient convention).
    assert CURRENT_SCHEMA_VERSION >= 20


def test_fresh_init_has_is_admin_column(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "is_admin" in _columns(conn, "auth_users")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_v19_db_walks_forward_with_existing_rows_defaulting_non_admin(tmp_path):
    db = tmp_path / "v19.db"
    # Build a fresh DB, then simulate a committed v19 install by dropping the
    # new column and resetting the version marker. SQLite can't DROP COLUMN on
    # very old engines, so rebuild auth_users without is_admin instead.
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            ALTER TABLE auth_users RENAME TO auth_users_old;
            CREATE TABLE auth_users (
                email           TEXT PRIMARY KEY,
                display_name    TEXT NOT NULL DEFAULT '',
                password_hash   TEXT,
                disabled        INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT '',
                password_set_at TEXT
            );
            INSERT INTO auth_users(email, display_name, password_hash, disabled,
                                   created_at, password_set_at)
                VALUES ('legacy@firm.com', 'Legacy', 'hash', 0, '2026-01-01', NULL);
            DROP TABLE auth_users_old;
            UPDATE schema_version SET version = 19;
            """
        )
        conn.commit()
        assert "is_admin" not in _columns(conn, "auth_users")
    finally:
        conn.close()

    init_db(db)  # the walk-forward
    conn = sqlite3.connect(str(db))
    try:
        assert "is_admin" in _columns(conn, "auth_users")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
        # The pre-existing account walks forward as a NON-admin (default 0).
        is_admin = conn.execute(
            "SELECT is_admin FROM auth_users WHERE email = 'legacy@firm.com'"
        ).fetchone()[0]
        assert is_admin == 0
    finally:
        conn.close()


def test_reinit_is_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_repo_admin_helpers_round_trip(tmp_path):
    """set_auth_user_admin flips the flag; fetch/list read it; count_admins
    counts only enabled admins (the last-admin-guard semantics)."""
    db = tmp_path / "roundtrip.db"
    init_db(db)
    with repo.db_session(db) as conn:
        repo.upsert_auth_user(conn, "admin@firm.com", "Admin", "hash")
        repo.upsert_auth_user(conn, "user@firm.com", "User", "hash")

        # Fresh accounts are non-admin.
        assert repo.fetch_auth_user(conn, "admin@firm.com").is_admin is False
        assert repo.count_admins(conn) == 0

        # Promote → fetch + list reflect it; count_admins sees one enabled admin.
        assert repo.set_auth_user_admin(conn, "admin@firm.com", True) is True
        assert repo.fetch_auth_user(conn, "admin@firm.com").is_admin is True
        listed = {u.email: u.is_admin for u in repo.list_auth_users(conn)}
        assert listed == {"admin@firm.com": True, "user@firm.com": False}
        assert repo.count_admins(conn) == 1

        # Disabling the admin drops the enabled-admin count to zero.
        repo.set_auth_user_disabled(conn, "admin@firm.com", True)
        assert repo.count_admins(conn) == 0
        assert repo.count_admins(conn, enabled_only=False) == 1

        # Unknown email returns False rather than raising.
        assert repo.set_auth_user_admin(conn, "nobody@firm.com", True) is False
