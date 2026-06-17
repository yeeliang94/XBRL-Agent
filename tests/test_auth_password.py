"""Password login: success, generic failures (no enumeration), disabled, hash.

Pins PLAN auth Phase 1.2: a correct credential creates a session; wrong
password and unknown email return the SAME generic 401; a disabled account is
refused; the stored hash is argon2id and re-verifies.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import server
from auth import lockout, passwords
from db import repository as repo
from db.schema import init_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    init_db(db)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    lockout.reset_all()
    # Seed one enabled account.
    conn = sqlite3.connect(str(db))
    try:
        repo.upsert_auth_user(conn, "you@firm.com", "You", passwords.hash_password("correct-horse"))
        conn.commit()
    finally:
        conn.close()
    return TestClient(server.app)


def test_correct_credentials_create_a_session(client):
    r = client.post("/api/auth/login/password",
                    json={"email": "you@firm.com", "password": "correct-horse"})
    assert r.status_code == 200
    assert r.json()["email"] == "you@firm.com"
    # A session cookie was set.
    assert any("xbrl_session" in c for c in r.headers.get_list("set-cookie"))
    # And /api/auth/me now resolves through that cookie.
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "you@firm.com"


def test_wrong_password_and_unknown_email_return_identical_generic_401(client):
    wrong = client.post("/api/auth/login/password",
                        json={"email": "you@firm.com", "password": "nope"})
    unknown = client.post("/api/auth/login/password",
                          json={"email": "ghost@firm.com", "password": "whatever"})
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    # Byte-identical body — no account-enumeration signal.
    assert wrong.json() == unknown.json()
    assert "Invalid email or password" in wrong.json()["detail"]


def test_disabled_account_is_refused(client, tmp_path, monkeypatch):
    conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
    try:
        repo.set_auth_user_disabled(conn, "you@firm.com", True)
        conn.commit()
    finally:
        conn.close()
    r = client.post("/api/auth/login/password",
                    json={"email": "you@firm.com", "password": "correct-horse"})
    assert r.status_code == 401


def test_stored_hash_is_argon2id(client):
    user = None
    conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        user = repo.fetch_auth_user(conn, "you@firm.com")
    finally:
        conn.close()
    assert user.password_hash.startswith("$argon2id$")
    assert passwords.verify_password(user.password_hash, "correct-horse")
