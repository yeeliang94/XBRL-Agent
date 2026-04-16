"""Peer-review HIGH: the session reservation must cover the window
between guard + StreamingResponse return. Previously, moving the
`active_runs.add` inside the generator (I4 fix) reopened a race where
two simultaneous requests could both pass the `in active_runs` check
before either generator started streaming.

Also: if the route handler aborts AFTER reserving but BEFORE returning
(e.g. missing API key), the reservation must be released so the
session isn't locked until restart.
"""
from __future__ import annotations

import pytest

import server
from fastapi.testclient import TestClient
from server import active_runs, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_active_runs():
    active_runs.clear()
    yield
    active_runs.clear()


def test_second_request_gets_409_while_first_is_reserved(tmp_path, monkeypatch):
    """Pre-seed active_runs to simulate a first request that has reserved
    the session. The second request must get 409."""
    output_dir = tmp_path / "output"
    session_dir = output_dir / "sess-a"
    session_dir.mkdir(parents=True)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    active_runs.add("sess-a")  # first request already reserved

    resp = client.post("/api/run/sess-a", json={
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    })
    assert resp.status_code == 409


def test_reservation_released_when_api_key_missing(tmp_path, monkeypatch):
    """If the route handler reserves the session then raises (e.g.
    because GEMINI_API_KEY/GOOGLE_API_KEY aren't set), the reservation
    must be discarded — otherwise the session is stuck locked until
    server restart."""
    output_dir = tmp_path / "output"
    session_dir = output_dir / "sess-b"
    session_dir.mkdir(parents=True)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    # Clear ALL known API key env vars so _resolve_api_key returns ""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # Also override load_dotenv to prevent .env from reintroducing keys.
    monkeypatch.setattr(server, "ENV_FILE", tmp_path / "nonexistent.env")

    resp = client.post("/api/run/sess-b", json={
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    })
    assert resp.status_code == 400
    # Critical: the reservation must have been released, so a follow-up
    # request (once env is fixed) isn't blocked by a stale lock.
    assert "sess-b" not in active_runs
