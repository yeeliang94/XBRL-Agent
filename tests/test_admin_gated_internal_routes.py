"""Server-side admin gate on the internal/QA surfaces (Phase 2 hardening).

The Benchmarks library and the global template-label ("Field labels") editor
are admin-only in the nav — but hiding the nav item is only UX. These routes
must enforce ``is_admin`` server-side so a non-admin who hits the URL directly
still gets a 403. Runs with AUTH_MODE unset so real sessions apply (the rest of
the suite runs in AUTH_MODE=dev, where the guard is bypassed by design).
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


# The benchmark-management + template-label routes that must be admin-only.
# (Per-run surfaces like /api/runs/{id}/concepts and /api/runs/{id}/eval are
# intentionally NOT gated — everyday users review their own runs there.)
_GATED = [
    ("get", "/api/benchmarks"),
    ("get", "/api/benchmarks/1"),
    ("get", "/api/benchmarks/1/concepts"),
    ("delete", "/api/benchmarks/1"),
    ("get", "/api/templates"),
    ("get", "/api/templates/some-template-v1/concepts"),
    ("patch", "/api/concepts/some-uuid/display_label"),
]


def test_unauthenticated_is_401(env):
    client = TestClient(server.app)
    for method, path in _GATED:
        resp = client.request(method.upper(), path, json=({} if method in ("patch", "post") else None))
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


def test_non_admin_is_403(env):
    client = TestClient(server.app)
    _login(client, "user@firm.com", "user-password")
    for method, path in _GATED:
        resp = client.request(method.upper(), path, json=({} if method in ("patch", "post") else None))
        assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


def test_admin_passes_the_gate(env):
    # An admin clears the gate — they may still get a 404 for a missing
    # benchmark/template, but never a 403 (proves the gate isn't blanket-denying).
    client = TestClient(server.app)
    _login(client, "admin@firm.com", "admin-password")
    for method, path in _GATED:
        resp = client.request(method.upper(), path, json=({} if method in ("patch", "post") else None))
        assert resp.status_code != 403, f"{method} {path} -> {resp.status_code}"
        assert resp.status_code != 401, f"{method} {path} -> {resp.status_code}"
