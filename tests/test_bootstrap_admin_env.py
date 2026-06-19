"""Env-driven admin bootstrap (start.bat / start.sh local-dev convenience).

Pins `server._bootstrap_admin_from_env`: with BOOTSTRAP_ADMIN_EMAIL +
BOOTSTRAP_ADMIN_PASSWORD set, a real argon2id admin row is seeded once at
startup so local devs skip the `python -m auth.manage` CLI step. The function
is opt-in, idempotent, and non-destructive (never resets a changed password,
never re-enables a disabled account).
"""
from __future__ import annotations

import sqlite3

import pytest

import server
from auth import passwords
from db import repository as repo
from db.schema import init_db


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "auth.db"
    init_db(p)
    return p


def _conn(db):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _run(db):
    conn = _conn(db)
    try:
        status = server._bootstrap_admin_from_env(conn)
        conn.commit()
    finally:
        conn.close()
    return status


def _fetch(db, email):
    conn = _conn(db)
    try:
        return repo.fetch_auth_user(conn, email)
    finally:
        conn.close()


def _set_env(monkeypatch, email="admin@localhost", password="changeme-local-dev"):
    if email is None:
        monkeypatch.delenv("BOOTSTRAP_ADMIN_EMAIL", raising=False)
    else:
        monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", email)
    if password is None:
        monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", password)


def test_creates_real_argon2id_admin(db, monkeypatch):
    _set_env(monkeypatch)
    assert _run(db) == "created"
    user = _fetch(db, "admin@localhost")
    assert user is not None
    assert user.is_admin is True
    assert user.disabled is False
    assert user.password_hash.startswith("$argon2id$")
    assert passwords.verify_password(user.password_hash, "changeme-local-dev")


def test_noop_when_vars_unset(db, monkeypatch):
    _set_env(monkeypatch, email=None, password=None)
    assert _run(db) is None
    assert _fetch(db, "admin@localhost") is None


def test_idempotent_does_not_reset_changed_password(db, monkeypatch):
    _set_env(monkeypatch)
    assert _run(db) == "created"
    # Dev rotates their password out-of-band.
    conn = _conn(db)
    try:
        repo.upsert_auth_user(
            conn, "admin@localhost", "Local Admin",
            passwords.hash_password("rotated-by-the-dev"),
        )
        conn.commit()
    finally:
        conn.close()
    # Reboot: bootstrap must NOT clobber the rotated password.
    assert _run(db) == "already-present"
    user = _fetch(db, "admin@localhost")
    assert passwords.verify_password(user.password_hash, "rotated-by-the-dev")
    assert not passwords.verify_password(user.password_hash, "changeme-local-dev")


def test_promotes_existing_non_admin(db, monkeypatch):
    conn = _conn(db)
    try:
        repo.upsert_auth_user(
            conn, "admin@localhost", "Existing",
            passwords.hash_password("existing-password"),
        )
        conn.commit()
    finally:
        conn.close()
    _set_env(monkeypatch)
    assert _run(db) == "promoted-existing"
    assert _fetch(db, "admin@localhost").is_admin is True


def test_short_password_skips_without_creating(db, monkeypatch):
    _set_env(monkeypatch, password="short")
    assert _run(db) == "skipped-short-password"
    assert _fetch(db, "admin@localhost") is None
