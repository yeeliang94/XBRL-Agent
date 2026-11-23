"""Production fail-closed startup checks (PLAN auth Phase 1.2).

_check_auth_startup_config must abort a production boot that is misconfigured
(dev mode, missing secret, or zero enabled accounts) and stay silent locally.
"""
from __future__ import annotations

import sqlite3

import pytest

import server
from auth import passwords
from db import repository as repo
from db.schema import init_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "auth.db"
    init_db(p)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", p)
    return p


def _seed_user(db):
    conn = sqlite3.connect(str(db))
    try:
        repo.upsert_auth_user(conn, "you@firm.com", "You", passwords.hash_password("correct-horse"))
        conn.commit()
    finally:
        conn.close()


def test_local_never_raises_even_with_zero_users(db, monkeypatch):
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    server._check_auth_startup_config()  # no raise


def test_prod_dev_mode_aborts(db, monkeypatch):
    monkeypatch.setenv("WEBSITE_SITE_NAME", "xbrl-prod")
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("AUTH_MODE", "dev")
    _seed_user(db)
    with pytest.raises(RuntimeError, match="AUTH_MODE=dev"):
        server._check_auth_startup_config()


def test_prod_missing_secret_aborts(db, monkeypatch):
    monkeypatch.setenv("WEBSITE_SITE_NAME", "xbrl-prod")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    _seed_user(db)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        server._check_auth_startup_config()


def test_prod_zero_enabled_users_aborts(db, monkeypatch):
    monkeypatch.setenv("WEBSITE_SITE_NAME", "xbrl-prod")
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    # No users seeded → abort.
    with pytest.raises(RuntimeError, match="No enabled login accounts"):
        server._check_auth_startup_config()


def test_prod_with_one_enabled_user_boots(db, monkeypatch):
    monkeypatch.setenv("WEBSITE_SITE_NAME", "xbrl-prod")
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    _seed_user(db)
    server._check_auth_startup_config()  # no raise
