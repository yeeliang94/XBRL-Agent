"""Tests for POST /api/runs/{run_id}/rerun-notes — the History-page
regenerate-notes endpoint (peer-review [HIGH] #1).

Before this endpoint existed, the Regenerate-notes button on the run
detail view redirected to `/?session=<id>#notes`, which no code read,
and the Rerun affordance on ExtractPage was only shown for failed /
cancelled agents — so a user clicking Regenerate on a completed run hit
a dead link. This endpoint gives the button a real target: it reads the
stored run's session + config, builds a notes-only rerun config
server-side, and delegates to the same SSE stream the existing
per-agent rerun uses.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """Same fixture shape as test_rerun_disconnect_finalization.py —
    throwaway OUTPUT_DIR + DB so we don't touch real files."""
    session_id = "test-regen-session"
    out = tmp_path / "output"
    (out / session_id).mkdir(parents=True)
    (out / session_id / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")

    import server
    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", out / "xbrl_agent.db")
    fake_env = tmp_path / ".env-test"
    fake_env.write_text("")
    monkeypatch.setattr(server, "ENV_FILE", fake_env)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("TEST_MODEL", "openai.gpt-5.4")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    # Initialise the DB schema so repository.create_run works.
    from db.schema import init_db
    init_db(out / "xbrl_agent.db")
    return session_id, out


def _seed_completed_run(out: Path, session_id: str, config: dict) -> int:
    """Create a completed `runs` row for the given session + config."""
    from db import repository as repo
    with repo.db_session(out / "xbrl_agent.db") as conn:
        run_id = repo.create_run(
            conn, "audit.pdf",
            session_id=session_id, output_dir=str(out / session_id),
            config=config,
        )
        repo.update_run_status(conn, run_id, "completed")
    return run_id


def test_rerun_notes_unknown_run_returns_404(session_env):
    """Guard against POSTing to a run id that doesn't exist."""
    from server import app
    client = TestClient(app)

    resp = client.post("/api/runs/99999/rerun-notes")
    assert resp.status_code == 404


def test_rerun_notes_run_without_notes_config_returns_400(session_env):
    """A run whose stored config has no notes templates can't be
    'regenerated' — there's nothing to regenerate. Better to fail
    fast than kick off an empty run."""
    session_id, out = session_env
    # Config has statements but no notes — common for face-only runs.
    run_id = _seed_completed_run(out, session_id, {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
        "filing_level": "company",
        "filing_standard": "mfrs",
        "notes_to_run": [],
        "notes_models": {},
    })

    from server import app
    client = TestClient(app)
    resp = client.post(f"/api/runs/{run_id}/rerun-notes")
    assert resp.status_code == 400
    assert "notes" in resp.json()["detail"].lower()


def test_rerun_notes_delegates_to_stream_with_notes_only_config(session_env):
    """Happy path: the endpoint loads the run, builds a notes-only
    RunConfigRequest (statements cleared, notes_to_run preserved from
    the original run's config), and delegates to run_multi_agent_stream.

    Pins the key contract: statements is empty, notes_to_run matches
    what was in the stored config, filing_level/standard/infopack all
    carry over so the regenerate uses the same template set as the
    original run.
    """
    session_id, out = session_env
    original_config = {
        "statements": ["SOFP", "SOPL"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": {"statements": []},
        "use_scout": True,
        "filing_level": "company",
        "filing_standard": "mpers",
        "notes_to_run": ["CORP_INFO", "ACC_POLICIES"],
        "notes_models": {"CORP_INFO": "openai.gpt-5.4-mini"},
    }
    run_id = _seed_completed_run(out, session_id, original_config)

    captured_config = {}

    async def fake_stream(**kwargs):
        captured_config["kwargs"] = kwargs
        # Minimal happy-path event sequence.
        yield {"event": "status", "data": {"phase": "starting"}}
        yield {"event": "run_complete", "data": {"status": "completed"}}

    from server import app
    with patch("server.run_multi_agent_stream", side_effect=fake_stream):
        client = TestClient(app)
        resp = client.post(f"/api/runs/{run_id}/rerun-notes")

    assert resp.status_code == 200
    # The endpoint streams SSE — collect the text and confirm at least
    # the run_complete event rolled through.
    assert "run_complete" in resp.text

    # Key assertion: the RunConfigRequest handed to run_multi_agent_stream
    # is a notes-only rerun of the stored config.
    run_config = captured_config["kwargs"]["run_config"]
    assert run_config.statements == []
    assert set(run_config.notes_to_run) == {"CORP_INFO", "ACC_POLICIES"}
    assert run_config.filing_level == "company"
    assert run_config.filing_standard == "mpers"
    # Per-template model override survives the round-trip.
    assert run_config.notes_models == {"CORP_INFO": "openai.gpt-5.4-mini"}


def test_rerun_notes_rejects_when_session_already_running(session_env):
    """The existing per-agent rerun returns 409 when a run is active
    for the same session. The notes-regenerate endpoint must do the
    same — two concurrent coordinator runs on one session corrupt
    output files."""
    session_id, out = session_env
    run_id = _seed_completed_run(out, session_id, {
        "statements": [],
        "variants": {},
        "models": {},
        "infopack": None,
        "use_scout": False,
        "filing_level": "company",
        "filing_standard": "mfrs",
        "notes_to_run": ["CORP_INFO"],
        "notes_models": {},
    })

    from server import app, active_runs
    active_runs.add(session_id)
    try:
        client = TestClient(app)
        resp = client.post(f"/api/runs/{run_id}/rerun-notes")
        assert resp.status_code == 409
    finally:
        active_runs.discard(session_id)
