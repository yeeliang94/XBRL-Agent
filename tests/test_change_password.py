"""Self-service change-my-own-password (/api/auth/change-password).

Requires the current password (re-auth), rotates the hash on success, 403s on a
wrong current password (401 is reserved for a gone session), 422s on a too-short
new one. Runs with AUTH_MODE unset
so a real session applies.
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
def env(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    init_db(db)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    lockout.reset_all()
    conn = sqlite3.connect(str(db))
    try:
        repo.upsert_auth_user(conn, "you@firm.com", "You", passwords.hash_password("old-password"))
        conn.commit()
    finally:
        conn.close()
    return db


def _login(client: TestClient, email: str, password: str) -> None:
    r = client.post("/api/auth/login/password", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


def _hash(db, email):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return repo.fetch_auth_user(conn, email).password_hash
    finally:
        conn.close()


def test_requires_a_session(env):
    client = TestClient(server.app)
    r = client.post("/api/auth/change-password", json={
        "current_password": "old-password", "new_password": "new-password"})
    assert r.status_code == 401


def test_correct_current_rotates_hash_and_new_password_logs_in(env):
    client = TestClient(server.app)
    _login(client, "you@firm.com", "old-password")
    before = _hash(env, "you@firm.com")
    r = client.post("/api/auth/change-password", json={
        "current_password": "old-password", "new_password": "brand-new-password"})
    assert r.status_code == 200
    after = _hash(env, "you@firm.com")
    assert after != before
    # The new password actually works on a fresh login; the old one no longer does.
    fresh = TestClient(server.app)
    assert fresh.post("/api/auth/login/password", json={
        "email": "you@firm.com", "password": "brand-new-password"}).status_code == 200
    assert fresh.post("/api/auth/login/password", json={
        "email": "you@firm.com", "password": "old-password"}).status_code == 401


def test_wrong_current_is_403_and_does_not_rotate(env):
    # 403, not 401: the session is valid, only the re-auth check failed. A 401
    # would trip the SPA's global session-expiry handler and log the user out
    # on a typo (Codex review P2). The session-gone case (test_requires_a_session)
    # keeps 401 so a genuine expiry still logs out.
    client = TestClient(server.app)
    _login(client, "you@firm.com", "old-password")
    before = _hash(env, "you@firm.com")
    r = client.post("/api/auth/change-password", json={
        "current_password": "WRONG", "new_password": "brand-new-password"})
    assert r.status_code == 403
    assert _hash(env, "you@firm.com") == before


def test_short_new_password_is_422(env):
    client = TestClient(server.app)
    _login(client, "you@firm.com", "old-password")
    r = client.post("/api/auth/change-password", json={
        "current_password": "old-password", "new_password": "short"})
    assert r.status_code == 422
