"""Admin gate on /api/settings (docs/PLAN-ui-ux-plain-language-overhaul.md
Phase 6). AI-plumbing, firm-wide run defaults and the shared notes appearance
are admin-only server-side. Runs with
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
    r = client.post("/api/settings", json={"notes_coverage": False})
    assert r.status_code == 200


@pytest.mark.parametrize("body", [
    {"notes_appearance_overrides": {"headerFill": "#f4f4f4"}},
    {"notes_appearance_reset": True},
])
def test_shared_notes_appearance_is_admin_only(env, body):
    """The house notes look applies to everyone's runs, so only admins set it."""
    member = TestClient(server.app)
    _login(member, "user@firm.com", "user-password")
    assert member.post("/api/settings", json=body).status_code == 403
    admin = TestClient(server.app)
    _login(admin, "admin@firm.com", "admin-password")
    assert admin.post("/api/settings", json=body).status_code == 200


def test_non_admin_cannot_test_connection(env):
    """The connection test exercises the shared AI plumbing, so it's admin-only
    server-side — hiding the button in the UI is not the boundary."""
    client = TestClient(server.app)
    _login(client, "user@firm.com", "user-password")
    r = client.post("/api/test-connection", json={"model": "openai.gpt-5.4"})
    assert r.status_code == 403


def test_unauthenticated_test_connection_is_401(env):
    client = TestClient(server.app)
    r = client.post("/api/test-connection", json={"model": "openai.gpt-5.4"})
    assert r.status_code == 401


def test_non_admin_cannot_write_advanced_settings(env):
    client = TestClient(server.app)
    _login(client, "user@firm.com", "user-password")
    r = client.post("/api/settings", json={
        "advanced_settings": {"XBRL_MAX_CONCURRENT_AGENTS": 1},
    })
    assert r.status_code == 403


def test_non_admin_cannot_reset_shared_defaults(env):
    client = TestClient(server.app)
    _login(client, "user@firm.com", "user-password")
    r = client.post("/api/settings", json={"reset_shared_defaults": True})
    assert r.status_code == 403


def test_connection_details_are_not_sent_to_non_admins(env, monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "https://proxy.example.com")
    monkeypatch.setattr(server, "_resolve_api_key", lambda: "sk-test-1234567890")
    member = TestClient(server.app)
    _login(member, "user@firm.com", "user-password")
    body = member.get("/api/settings").json()
    assert body["proxy_url"] == "" and body["api_key_preview"] == "" and body["api_key_set"] is True
    admin = TestClient(server.app)
    _login(admin, "admin@firm.com", "admin-password")
    body = admin.get("/api/settings").json()
    assert body["proxy_url"] == "https://proxy.example.com" and body["api_key_preview"].startswith("sk-t")
