"""Sessions: sliding expiry, logout revokes, cookie flags (dev + prod), and the
no-bump denylist. Pins PLAN auth Phase 1.1 + the production cookie hardening.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import server
from auth import config, lockout, middleware, passwords
from db import repository as repo
from db.schema import init_db


def _seed(db):
    conn = sqlite3.connect(str(db))
    try:
        repo.upsert_auth_user(conn, "you@firm.com", "You", passwords.hash_password("correct-horse"))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    init_db(db)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    lockout.reset_all()
    _seed(db)
    return TestClient(server.app)


def _login(client):
    r = client.post("/api/auth/login/password",
                    json={"email": "you@firm.com", "password": "correct-horse"})
    assert r.status_code == 200


def test_sliding_expiry_logs_out_and_deletes_row(client):
    _login(client)
    # Age the session past the timeout by rewriting last_seen_at in the DB.
    conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
    try:
        conn.execute("UPDATE auth_sessions SET last_seen_at = '2000-01-01T00:00:00Z'")
        conn.commit()
    finally:
        conn.close()
    # A guarded request now 401s (middleware path)...
    assert client.get("/api/config").status_code == 401
    # ...and the stale row was deleted.
    conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
    try:
        n = conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_logout_revokes_session(client):
    _login(client)
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_disabling_a_user_revokes_their_live_session(client):
    # Peer-review HIGH: disabling an account must lock the user out NOW, not
    # whenever the session happens to idle out.
    _login(client)
    assert client.get("/api/config").status_code == 200
    conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
    try:
        repo.set_auth_user_disabled(conn, "you@firm.com", True)
        conn.commit()
        # The live session row was deleted up front.
        n = conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
    finally:
        conn.close()
    assert n == 0
    # And the gate now refuses the previously-valid cookie.
    assert client.get("/api/config").status_code == 401


def test_resolve_session_fails_closed_on_disabled_user(client):
    # Defence in depth: even if a session row somehow outlives the disable
    # (e.g. flipped without going through set_auth_user_disabled), the gate
    # must still refuse it.
    _login(client)
    conn = sqlite3.connect(str(server.AUDIT_DB_PATH))
    try:
        # Flip the flag directly, leaving the session row in place.
        conn.execute("UPDATE auth_users SET disabled = 1 WHERE email = ?", ("you@firm.com",))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 1
    finally:
        conn.close()
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_refresh_reissues_cookie_with_renewed_max_age(client):
    """R5 follow-up: /api/auth/refresh must re-emit the session cookie so its
    browser Max-Age slides forward with the server session — otherwise an
    actively-used session's cookie expires ~1h after login and logs the user
    out mid-review even though the DB session was kept alive."""
    _login(client)
    r = client.post("/api/auth/refresh")
    assert r.status_code == 200
    set_cookies = r.headers.get_list("set-cookie")
    # A fresh Set-Cookie for the session, carrying the persistent Max-Age.
    assert any(
        "xbrl_session=" in c and "max-age=3600" in c.lower() for c in set_cookies
    ), set_cookies


def test_dev_cookie_flags(client):
    r = client.post("/api/auth/login/password",
                    json={"email": "you@firm.com", "password": "correct-horse"})
    cookie = r.headers.get_list("set-cookie")[0]
    assert "xbrl_session=" in cookie
    assert "__Host-" not in cookie          # no Secure locally → plain name
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "Secure" not in cookie
    # R5: the cookie now persists across a browser restart (Max-Age set,
    # tracking the 1-hour idle window) instead of dying with the browser.
    assert "max-age=3600" in cookie.lower()


def test_prod_cookie_is_forced_secure_over_http(tmp_path, monkeypatch):
    # Production: __Host- + Secure even though TestClient speaks http (Azure
    # terminates TLS upstream so the app sees http — the cookie must NOT
    # downgrade).
    db = tmp_path / "auth.db"
    init_db(db)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    monkeypatch.setenv("WEBSITE_SITE_NAME", "xbrl-prod")
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    lockout.reset_all()
    _seed(db)
    client = TestClient(server.app)

    r = client.post("/api/auth/login/password",
                    json={"email": "you@firm.com", "password": "correct-horse"})
    assert r.status_code == 200
    cookie = r.headers.get_list("set-cookie")[0]
    assert cookie.startswith("__Host-xbrl_session=")
    assert "Secure" in cookie
    assert "Path=/" in cookie
    assert "Domain=" not in cookie


def test_status_poll_is_not_activity():
    # Background polls don't bump the sliding window; ordinary calls do.
    assert middleware.counts_as_activity("/api/runs/5/re-review/status") is False
    assert middleware.counts_as_activity("/api/config") is True


def test_activity_bump_is_throttled():
    """should_bump_activity skips the last_seen_at write on back-to-back
    requests (a fresh timestamp) and only rewrites once it's gone stale — so a
    busy run page doesn't do one UPDATE per API call."""
    from datetime import datetime, timedelta, timezone
    from auth import sessions as auth_sessions

    fresh = repo.AuthSession(
        session_id="s", email="you@firm.com",
        last_seen_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    assert auth_sessions.should_bump_activity(fresh) is False

    stale = repo.AuthSession(
        session_id="s", email="you@firm.com",
        last_seen_at=(datetime.now(timezone.utc) - timedelta(seconds=120))
        .isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    assert auth_sessions.should_bump_activity(stale) is True

    # Unparseable timestamp fails toward keeping the user alive (refresh).
    garbage = repo.AuthSession(session_id="s", email="you@firm.com", last_seen_at="???")
    assert auth_sessions.should_bump_activity(garbage) is True


def test_sweep_expired_auth_sessions_reaps_only_old_rows(tmp_path):
    """The startup sweep deletes never-touched-again expired sessions and leaves
    live ones alone."""
    db = tmp_path / "auth.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        repo.upsert_auth_user(conn, "you@firm.com", "You", passwords.hash_password("correct-horse"))
        repo.create_auth_session(conn, "old", "you@firm.com", "You")
        repo.create_auth_session(conn, "live", "you@firm.com", "You")
        conn.execute("UPDATE auth_sessions SET last_seen_at = '2000-01-01T00:00:00Z' WHERE session_id = 'old'")
        conn.commit()
        swept = repo.sweep_expired_auth_sessions(conn, "2001-01-01T00:00:00Z")
        conn.commit()
        assert swept == 1
        remaining = {r[0] for r in conn.execute("SELECT session_id FROM auth_sessions")}
        assert remaining == {"live"}
    finally:
        conn.close()
