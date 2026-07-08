"""Admin gate on /api/settings (docs/PLAN-ui-ux-plain-language-overhaul.md
Phase 6). AI-plumbing + firm-wide run-default keys are admin-only server-side;
cosmetic keys (notes_table_style) stay writable by everyone. Runs with
AUTH_MODE unset so real sessions apply (dev-bypass would waive the gate).
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
    # Writes land in a throwaway .env, never the repo's real one.
    monkeypatch.setattr(server, "ENV_FILE", tmp_path / ".env")
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


def test_non_admin_cannot_write_ai_plumbing(env):
    client = TestClient(server.app)
    _login(client, "user@firm.com", "user-password")
    r = client.post("/api/settings", json={"model": "openai.gpt-5.4"})
    assert r.status_code == 403


def test_unauthenticated_ai_plumbing_write_is_401(env):
    client = TestClient(server.app)
    r = client.post("/api/settings", json={"proxy_url": "https://example.com"})
    assert r.status_code == 401


def test_admin_can_write_ai_plumbing(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    r = client.post("/api/settings", json={"auto_review": False})
    assert r.status_code == 200


def test_non_admin_can_write_cosmetic_only(env):
    """A non-admin write touching only the cosmetic firm default is allowed."""
    client = TestClient(server.app)
    _login(client, "user@firm.com", "user-password")
    r = client.post("/api/settings", json={
        "notes_table_style": {"headerFill": "#f4f4f4"},
    })
    assert r.status_code == 200
