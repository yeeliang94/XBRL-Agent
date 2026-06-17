"""Middleware gate: every /api/* route needs a session except /api/auth/* and
/api/health, and /api/health actually exists (no phantom-route exemption).

Pins PLAN auth Phase 1.2. The "no endpoint ships unguarded" guarantee is
checked programmatically against the live route table via is_guarded(), which
is robust to path params (walking real HTTP requests would trip over `{id}`).
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import server
from auth import lockout, middleware, passwords
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
    conn = sqlite3.connect(str(db))
    try:
        repo.upsert_auth_user(conn, "you@firm.com", "You", passwords.hash_password("correct-horse"))
        conn.commit()
    finally:
        conn.close()
    return TestClient(server.app)


def test_guarded_routes_401_without_session(client):
    for path in ("/api/config", "/api/settings", "/api/runs"):
        r = client.get(path)
        assert r.status_code == 401, f"{path} should require a session"


def test_health_is_public_and_real(client):
    # Exists in the route table...
    assert any(getattr(r, "path", "") == "/api/health" for r in server.app.routes)
    # ...and is reachable without a session.
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_auth_endpoints_are_exempt(client):
    # /api/auth/me is reachable without a session (returns its own 401, not the
    # gate's) — i.e. it is not blocked by the middleware before running.
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated."


def test_health_is_exact_match_not_a_prefix():
    """/api/health is exempt by EXACT path so a future /api/health-detailed
    carrying real data can't be silently exempted by an accidental prefix."""
    assert middleware.is_guarded("/api/health") is False
    assert middleware.is_guarded("/api/health-detailed") is True
    assert middleware.is_guarded("/api/healthz") is True
    # /api/auth/* stays a subtree exemption.
    assert middleware.is_guarded("/api/auth/login/password") is False


def test_no_api_route_ships_unguarded(client):
    """Every registered /api/* path is guarded unless it is an auth or health
    route. Guards against a future endpoint silently shipping without auth."""
    api_paths = {
        getattr(r, "path", "")
        for r in server.app.routes
        if getattr(r, "path", "").startswith("/api/")
    }
    assert api_paths  # sanity: we actually found routes
    for path in api_paths:
        guarded = middleware.is_guarded(path)
        is_exempt = path.startswith("/api/auth/") or path == "/api/health"
        assert guarded == (not is_exempt), f"{path}: guarded={guarded}, exempt={is_exempt}"
