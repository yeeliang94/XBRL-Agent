"""Brute-force lockout: N failures lock the (email, IP); the lock expires.

Pins PLAN auth Phase 1.2 brute-force defence. The HTTP test proves a locked
pair gets 429 even with the correct password; the unit test proves the window
ages out (via a monkeypatched monotonic clock so we don't actually sleep).
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
    monkeypatch.setenv("AUTH_LOGIN_MAX_ATTEMPTS", "3")
    lockout.reset_all()
    conn = sqlite3.connect(str(db))
    try:
        repo.upsert_auth_user(conn, "you@firm.com", "You", passwords.hash_password("correct-horse"))
        conn.commit()
    finally:
        conn.close()
    return TestClient(server.app)


def test_lockout_after_threshold_blocks_even_correct_password(client):
    for _ in range(3):
        r = client.post("/api/auth/login/password",
                        json={"email": "you@firm.com", "password": "wrong"})
        assert r.status_code == 401
    # Now locked — even the correct password is refused with 429.
    r = client.post("/api/auth/login/password",
                    json={"email": "you@firm.com", "password": "correct-horse"})
    assert r.status_code == 429
    assert "try again later" in r.json()["detail"].lower()


def test_lockout_window_expires(monkeypatch):
    # Drive the lockout module directly with a controllable clock.
    monkeypatch.setenv("AUTH_LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_S", "100")
    lockout.reset_all()
    fake = {"t": 1000.0}
    monkeypatch.setattr(lockout.time, "monotonic", lambda: fake["t"])

    for _ in range(3):
        lockout.record_failure("a@b.com", "1.2.3.4")
    assert lockout.seconds_remaining("a@b.com", "1.2.3.4") > 0

    fake["t"] += 101  # advance past the lockout window
    assert lockout.seconds_remaining("a@b.com", "1.2.3.4") == 0.0


def test_success_clears_failures(client):
    # Two wrong, then a correct login resets the counter (no creeping lockout).
    for _ in range(2):
        client.post("/api/auth/login/password",
                    json={"email": "you@firm.com", "password": "wrong"})
    ok = client.post("/api/auth/login/password",
                     json={"email": "you@firm.com", "password": "correct-horse"})
    assert ok.status_code == 200
    assert lockout.seconds_remaining("you@firm.com", "testclient") == 0.0
