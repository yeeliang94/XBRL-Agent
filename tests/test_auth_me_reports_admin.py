"""/api/auth/me reports the is_admin role so the SPA can gate the Users tab.

An admin session gets is_admin: true, a regular session false, and the dev
bypass (AUTH_MODE=dev) reports true so CI/local exercise the admin surface.
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
def db(tmp_path, monkeypatch):
    p = tmp_path / "auth.db"
    init_db(p)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", p)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    lockout.reset_all()
    conn = sqlite3.connect(str(p))
    try:
        repo.upsert_auth_user(conn, "admin@firm.com", "Admin", passwords.hash_password("admin-password"))
        repo.set_auth_user_admin(conn, "admin@firm.com", True)
        repo.upsert_auth_user(conn, "user@firm.com", "User", passwords.hash_password("user-password"))
        conn.commit()
    finally:
        conn.close()
    return p


def _login(client, email, password):
    assert client.post("/api/auth/login/password",
                       json={"email": email, "password": password}).status_code == 200


def test_admin_session_reports_is_admin_true(db, monkeypatch):
    monkeypatch.delenv("AUTH_MODE", raising=False)
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    assert client.get("/api/auth/me").json()["is_admin"] is True


def test_regular_session_reports_is_admin_false(db, monkeypatch):
    monkeypatch.delenv("AUTH_MODE", raising=False)
    client = TestClient(server.app)
    _login(client, "user@firm.com", "user-password")
    assert client.get("/api/auth/me").json()["is_admin"] is False


def test_dev_bypass_reports_is_admin_true(db, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    client = TestClient(server.app)
    body = client.get("/api/auth/me").json()
    assert body["email"] == "dev@localhost"
    assert body["is_admin"] is True
