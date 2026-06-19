"""Admin user-management routes (/api/admin/*) — the web mirror of auth.manage.

Pins the v20 admin role: every route enforces is_admin server-side (the hidden
UI tab is not the boundary), and the last-admin guard refuses to disable/demote
the only remaining admin. Runs with AUTH_MODE unset so real sessions apply.
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
        repo.upsert_auth_user(conn, "admin@firm.com", "Admin", passwords.hash_password("admin-password"))
        repo.set_auth_user_admin(conn, "admin@firm.com", True)
        repo.upsert_auth_user(conn, "user@firm.com", "User", passwords.hash_password("user-password"))
        conn.commit()
    finally:
        conn.close()
    return db


def _login(client: TestClient, email: str, password: str) -> None:
    r = client.post("/api/auth/login/password", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


def _fetch(db, email):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return repo.fetch_auth_user(conn, email)
    finally:
        conn.close()


# --- enforcement -----------------------------------------------------------

def test_unauthenticated_is_401(env):
    client = TestClient(server.app)
    assert client.get("/api/admin/users").status_code == 401


def test_non_admin_is_403_on_every_route(env):
    client = TestClient(server.app)
    _login(client, "user@firm.com", "user-password")
    assert client.get("/api/admin/users").status_code == 403
    assert client.post("/api/admin/users", json={
        "email": "x@firm.com", "password": "longenough"}).status_code == 403
    assert client.post("/api/admin/users/admin@firm.com/disable").status_code == 403
    assert client.post("/api/admin/users/admin@firm.com/reset-password",
                       json={"password": "longenough"}).status_code == 403
    assert client.post("/api/admin/users/user@firm.com/admin",
                       json={"is_admin": True}).status_code == 403
    # The non-admin must not have been promoted by the refused call.
    assert _fetch(env, "user@firm.com").is_admin is False


# --- happy paths -----------------------------------------------------------

def test_admin_can_list_without_hash(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    r = client.get("/api/admin/users")
    assert r.status_code == 200
    body = r.json()["users"]
    emails = {u["email"] for u in body}
    assert {"admin@firm.com", "user@firm.com"} <= emails
    # Never leak the hash; do expose has_password.
    for u in body:
        assert "password_hash" not in u
        assert "has_password" in u


def test_admin_can_add_user(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    r = client.post("/api/admin/users", json={
        "email": "New@Firm.com", "display_name": "New", "password": "longenough"})
    assert r.status_code == 200
    created = _fetch(env, "new@firm.com")
    assert created is not None and created.is_admin is False


def test_admin_can_add_admin_user(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    client.post("/api/admin/users", json={
        "email": "boss2@firm.com", "password": "longenough", "is_admin": True})
    assert _fetch(env, "boss2@firm.com").is_admin is True


def test_add_user_rejects_short_password(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    r = client.post("/api/admin/users", json={"email": "z@firm.com", "password": "short"})
    assert r.status_code == 422


def test_admin_can_disable_and_enable(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    assert client.post("/api/admin/users/user@firm.com/disable").status_code == 200
    assert _fetch(env, "user@firm.com").disabled is True
    assert client.post("/api/admin/users/user@firm.com/enable").status_code == 200
    assert _fetch(env, "user@firm.com").disabled is False


def test_admin_can_reset_password(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    old = _fetch(env, "user@firm.com").password_hash
    r = client.post("/api/admin/users/user@firm.com/reset-password",
                    json={"password": "brand-new-password"})
    assert r.status_code == 200
    new = _fetch(env, "user@firm.com").password_hash
    assert new != old
    assert passwords.verify_password(new, "brand-new-password")


def test_admin_can_promote_and_demote(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    assert client.post("/api/admin/users/user@firm.com/admin",
                       json={"is_admin": True}).status_code == 200
    assert _fetch(env, "user@firm.com").is_admin is True
    # Now safe to demote (two admins exist).
    assert client.post("/api/admin/users/user@firm.com/admin",
                       json={"is_admin": False}).status_code == 200
    assert _fetch(env, "user@firm.com").is_admin is False


# --- last-admin guard ------------------------------------------------------

def test_cannot_disable_last_admin(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    r = client.post("/api/admin/users/admin@firm.com/disable")
    assert r.status_code == 409
    assert _fetch(env, "admin@firm.com").disabled is False


def test_cannot_demote_last_admin(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    r = client.post("/api/admin/users/admin@firm.com/admin", json={"is_admin": False})
    assert r.status_code == 409
    assert _fetch(env, "admin@firm.com").is_admin is True


def test_can_disable_admin_once_another_admin_exists(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    client.post("/api/admin/users/user@firm.com/admin", json={"is_admin": True})
    # Two admins now — disabling the first is allowed.
    assert client.post("/api/admin/users/admin@firm.com/disable").status_code == 200


# --- not-found -------------------------------------------------------------

def test_actions_on_unknown_user_are_404(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    assert client.post("/api/admin/users/ghost@firm.com/disable").status_code == 404
    assert client.post("/api/admin/users/ghost@firm.com/reset-password",
                       json={"password": "longenough"}).status_code == 404
    assert client.post("/api/admin/users/ghost@firm.com/admin",
                       json={"is_admin": True}).status_code == 404
