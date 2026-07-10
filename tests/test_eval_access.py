"""Benchmark/eval routes are authenticated but NOT admin-gated (Step A3).

The Evals workspace is open to all signed-in users (decision #6). This pins the
deliberate behaviour change: the endpoints used to require admin; now a non-admin
authenticated user can list benchmarks, while an anonymous request still 401s.
Runs with AUTH_MODE unset so real sessions apply (like test_admin_routes).
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
        repo.upsert_auth_user(
            conn, "user@firm.com", "User", passwords.hash_password("user-password")
        )
        conn.commit()
    finally:
        conn.close()
    return db


def _login(client: TestClient, email: str, password: str) -> None:
    r = client.post(
        "/api/auth/login/password", json={"email": email, "password": password}
    )
    assert r.status_code == 200, r.text


def test_anonymous_still_401(env):
    client = TestClient(server.app)
    assert client.get("/api/benchmarks").status_code == 401


def test_non_admin_can_list_benchmarks(env):
    client = TestClient(server.app)
    _login(client, "user@firm.com", "user-password")
    r = client.get("/api/benchmarks")
    assert r.status_code == 200, r.text
    assert "benchmarks" in r.json()
