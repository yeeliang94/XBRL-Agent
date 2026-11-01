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
    assert client.post("/api/admin/users", json={"email": "x@firm.com"}).status_code == 403
    assert client.post("/api/admin/users/admin@firm.com/disable").status_code == 403
    assert client.post("/api/admin/users/admin@firm.com/reset-password").status_code == 403
    assert client.get("/api/admin/audit").status_code == 403
    assert client.post("/api/admin/users/user@firm.com/admin",
                       json={"is_admin": True}).status_code == 403
    # The non-admin must not have been promoted by the refused call.
    assert _fetch(env, "user@firm.com").is_admin is False


def test_guidance_is_readable_by_members_but_only_admins_can_save(env):
    from agent_instructions import SCOPES
    member = TestClient(server.app)
    _login(member, "user@firm.com", "user-password")
    assert member.get("/api/agent-instructions").status_code == 200
    assert member.get("/api/agent-instructions/sources").status_code == 200
    payload = {"texts": {key: "" for key in SCOPES}, "revision": 0}
    assert member.put("/api/agent-instructions", json=payload).status_code == 403
    admin = TestClient(server.app)
    _login(admin, "admin@firm.com", "admin-password")
    assert admin.put("/api/agent-instructions", json=payload).status_code == 200
    assert member.get("/api/agent-instructions").json()["revision"] == 1
    assert TestClient(server.app).get("/api/agent-instructions").status_code == 401


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


def test_added_person_must_replace_temporary_password_before_using_the_app(env):
    admin = TestClient(server.app)
    _login(admin, "admin@firm.com", "admin-password")
    r = admin.post("/api/admin/users", json={"email": "New@Firm.com", "display_name": "New"})
    assert r.status_code == 200
    temporary = r.json()["temporary_password"]
    created = _fetch(env, "new@firm.com")
    assert created.is_admin is False and created.must_change_password is True

    person = TestClient(server.app)
    login = person.post("/api/auth/login/password",
                        json={"email": "new@firm.com", "password": temporary})
    assert login.status_code == 200 and login.json()["must_change_password"] is True
    assert person.get("/api/auth/me").json()["must_change_password"] is True
    blocked = person.get("/api/runs")
    assert blocked.status_code == 403
    assert blocked.json()["reason"] == "password_change_required"

    other = TestClient(server.app)
    _login(other, "new@firm.com", temporary)
    assert other.get("/api/runs").status_code == 403

    # Keeping the temporary password would leave the admin knowing it.
    assert person.post("/api/auth/change-password",
                       json={"current_password": "", "new_password": temporary}).status_code == 422
    # The session was opened with the temporary password, so it is not re-asked.
    assert person.post("/api/auth/change-password",
                       json={"current_password": "", "new_password": "chosen-password"}).status_code == 200
    assert person.get("/api/runs").status_code == 200
    assert other.get("/api/runs").status_code == 401
    assert other.post("/api/auth/change-password", json={
        "current_password": "", "new_password": "another-password"}).status_code == 401
    assert other.post("/api/auth/login/password", json={
        "email": "new@firm.com", "password": temporary}).status_code == 401
    _login(other, "new@firm.com", "chosen-password")
    assert other.get("/api/runs").status_code == 200
    assert _fetch(env, "new@firm.com").must_change_password is False
    assert _fetch(env, "new@firm.com").last_sign_in_at


def test_admin_can_add_admin_user(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    client.post("/api/admin/users", json={"email": "boss2@firm.com", "is_admin": True})
    assert _fetch(env, "boss2@firm.com").is_admin is True


def test_adding_an_existing_email_changes_nothing(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    before = _fetch(env, "user@firm.com").password_hash
    r = client.post("/api/admin/users", json={"email": "USER@firm.com", "is_admin": True})
    assert r.status_code == 409
    after = _fetch(env, "user@firm.com")
    assert after.password_hash == before and after.is_admin is False


def test_admin_can_disable_and_enable(env):
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    assert client.post("/api/admin/users/user@firm.com/disable").status_code == 200
    assert _fetch(env, "user@firm.com").disabled is True
    assert client.post("/api/admin/users/user@firm.com/enable").status_code == 200
    assert _fetch(env, "user@firm.com").disabled is False


def test_reset_issues_a_temporary_password_and_signs_the_person_out(env):
    person = TestClient(server.app)
    _login(person, "user@firm.com", "user-password")
    admin = TestClient(server.app)
    _login(admin, "admin@firm.com", "admin-password")
    r = admin.post("/api/admin/users/user@firm.com/reset-password")
    assert r.status_code == 200
    temporary = r.json()["temporary_password"]
    assert person.get("/api/runs").status_code == 401
    account = _fetch(env, "user@firm.com")
    assert account.must_change_password is True
    assert passwords.verify_password(account.password_hash, temporary)
    assert not passwords.verify_password(account.password_hash, "user-password")


def test_account_changes_are_recorded_in_the_audit_log(env):
    admin = TestClient(server.app)
    _login(admin, "admin@firm.com", "admin-password")
    admin.post("/api/admin/users", json={"email": "new@firm.com", "display_name": "New"})
    admin.post("/api/admin/users/user@firm.com/disable")
    admin.post("/api/admin/users/user@firm.com/enable")
    admin.post("/api/admin/users/user@firm.com/reset-password")
    admin.post("/api/admin/users/user@firm.com/admin", json={"is_admin": True})
    admin.post("/api/admin/users/ghost@firm.com/disable")  # refused: not recorded
    events = admin.get("/api/admin/audit").json()["events"]
    assert [(e["action"], e["target"]) for e in events] == [
        ("role_changed", "user@firm.com"),
        ("password_reset", "user@firm.com"),
        ("user_enabled", "user@firm.com"),
        ("user_disabled", "user@firm.com"),
        ("user_added", "new@firm.com"),
    ]
    assert {e["actor_email"] for e in events} == {"admin@firm.com"}
    assert events[0]["actor_name"] == "Admin" and events[0]["target_name"] == "User"


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
    assert client.post("/api/admin/users/ghost@firm.com/reset-password").status_code == 404
    assert client.post("/api/admin/users/ghost@firm.com/admin",
                       json={"is_admin": True}).status_code == 404
