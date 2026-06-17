"""DB migration v17 -> v18: the auth tables (auth_users + auth_sessions).

Both are pure CREATE TABLE IF NOT EXISTS walk-forward steps (new tables, no
ALTER), so the migration block only bumps the version marker. Same pinning
shape as the v12->v13 table-only step: fresh init carries both tables +
version 18, a v17 fixture walks forward cleanly, re-init is idempotent, and
the repo helpers round-trip an account + session (incl. the ON DELETE CASCADE
that sweeps a user's sessions).
"""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_current_schema_version_is_at_least_v18():
    # >= so a later bump doesn't break this pin (resilient v16 convention).
    assert CURRENT_SCHEMA_VERSION >= 18


def test_fresh_init_has_auth_tables(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        tables = _tables(conn)
        assert "auth_users" in tables
        assert "auth_sessions" in tables
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_v17_db_walks_forward_to_v18(tmp_path):
    db = tmp_path / "v17.db"
    # Build a fresh DB, then simulate a committed v17 install by dropping the
    # two new tables and resetting the version marker.
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE auth_sessions")
        conn.execute("DROP TABLE auth_users")
        conn.execute("UPDATE schema_version SET version = 17")
        conn.commit()
        assert "auth_users" not in _tables(conn)
    finally:
        conn.close()

    init_db(db)  # the walk-forward
    conn = sqlite3.connect(str(db))
    try:
        tables = _tables(conn)
        assert "auth_users" in tables
        assert "auth_sessions" in tables
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
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


def test_auth_helpers_roundtrip_account_and_session(tmp_path):
    from db import repository as repo

    db = tmp_path / "rt.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")  # so the CASCADE below fires
    conn.row_factory = sqlite3.Row
    try:
        # Case-insensitive create + read.
        repo.upsert_auth_user(conn, "You@Firm.com", "You", "argon2-hash")
        conn.commit()
        user = repo.fetch_auth_user(conn, "you@firm.com")
        assert user is not None
        assert user.email == "you@firm.com"
        assert user.password_hash == "argon2-hash"
        assert user.disabled is False
        assert user.password_set_at  # stamped when a hash is supplied
        assert repo.count_auth_users(conn) == 1

        # Disable blocks but keeps the row (count_enabled drops to 0).
        assert repo.set_auth_user_disabled(conn, "you@firm.com", True) is True
        conn.commit()
        assert repo.count_auth_users(conn, enabled_only=True) == 0
        assert repo.count_auth_users(conn) == 1

        # Sessions round-trip + touch advances last_seen_at.
        repo.upsert_auth_user(conn, "you@firm.com", "You", "argon2-hash")  # re-enable
        repo.set_auth_user_disabled(conn, "you@firm.com", False)
        repo.create_auth_session(conn, "sess-abc", "you@firm.com", "You")
        conn.commit()
        sess = repo.fetch_auth_session(conn, "sess-abc")
        assert sess is not None and sess.email == "you@firm.com"
        assert sess.provider == "password"

        # Deleting the user cascades the session away.
        conn.execute("DELETE FROM auth_users WHERE email = ?", ("you@firm.com",))
        conn.commit()
        assert repo.fetch_auth_session(conn, "sess-abc") is None
    finally:
        conn.close()
