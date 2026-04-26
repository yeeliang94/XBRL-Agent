"""PATCH /api/runs/{id} — persist pre-run config edits onto a draft row.

Plan: docs/PLAN-persistent-draft-uploads.md — Phase B (steps 5-6).

The frontend PreRunPanel debounce-PATCHes the runs row each time the user
toggles a statement, switches filing level, or changes a model. The
endpoint is a partial-update merge: a body with only `statements`
preserves the previously-saved `filing_level`, etc. Only drafts are
mutable; once a run starts, its config is frozen.
"""
from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from db.schema import init_db


@pytest.fixture
def draft_session(tmp_path, monkeypatch):
    """Spin up a fresh OUTPUT_DIR + audit DB and create one draft run.

    Returns (client, run_id, output_dir). The draft starts with
    run_config_json=NULL — that's the Phase A upload-time shape, and the
    PATCH endpoint must not assume a pre-existing config blob.
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    db_path = output_dir / "xbrl_agent.db"
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db_path)
    init_db(db_path)

    client = TestClient(server.app)
    upload = client.post(
        "/api/upload",
        files={"file": ("X.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    return client, upload.json()["run_id"], output_dir


def _read_config(output_dir: Path, run_id: int) -> dict | None:
    """Helper — read the run_config_json blob directly so we can assert
    on what was persisted, not just on what the API echoes back."""
    conn = sqlite3.connect(str(output_dir / "xbrl_agent.db"))
    try:
        row = conn.execute(
            "SELECT run_config_json FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    raw = row[0] if row else None
    return json.loads(raw) if raw else None


def test_patch_updates_run_config(draft_session):
    """A first PATCH writes the entire payload; a second PATCH merges."""
    client, run_id, output_dir = draft_session

    resp = client.patch(
        f"/api/runs/{run_id}",
        json={
            "statements": ["SOFP"],
            "filing_level": "group",
            "filing_standard": "mpers",
        },
    )
    assert resp.status_code == 200, resp.text

    persisted = _read_config(output_dir, run_id)
    assert persisted is not None
    assert persisted["statements"] == ["SOFP"]
    assert persisted["filing_level"] == "group"
    assert persisted["filing_standard"] == "mpers"

    # Partial follow-up: change only filing_level. The previously-saved
    # statements list must survive — the endpoint is a merge, not a replace.
    resp = client.patch(f"/api/runs/{run_id}", json={"filing_level": "company"})
    assert resp.status_code == 200

    persisted = _read_config(output_dir, run_id)
    assert persisted["filing_level"] == "company"
    assert persisted["statements"] == ["SOFP"]
    assert persisted["filing_standard"] == "mpers"


def test_patch_rejected_on_non_draft(draft_session):
    """Once a run is no longer `draft`, PATCH must 409. Editing a running
    or completed run's stored config would lie about what was extracted."""
    client, run_id, output_dir = draft_session

    # Manually flip status — simulate a draft that was already started.
    conn = sqlite3.connect(str(output_dir / "xbrl_agent.db"))
    try:
        conn.execute(
            "UPDATE runs SET status = 'running' WHERE id = ?", (run_id,)
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.patch(
        f"/api/runs/{run_id}", json={"filing_level": "group"}
    )
    assert resp.status_code == 409
    # The run row must NOT have been mutated.
    persisted = _read_config(output_dir, run_id)
    assert persisted is None  # still null — the PATCH was refused before write


def test_patch_validates_payload(draft_session):
    """Bad payload values (e.g. invalid filing_level) return 422."""
    client, run_id, _ = draft_session

    resp = client.patch(
        f"/api/runs/{run_id}", json={"filing_level": "consolidated"}
    )
    assert resp.status_code == 422


def test_patch_atomic_against_status_race(draft_session):
    """Peer-review MEDIUM #4 regression: PATCH must atomically guard on
    `status='draft'`. If another request flips the row to 'running'
    between fetch and update, the PATCH must NOT silently mutate a
    started run's stored config (which is the audit-trail record of
    what was actually extracted)."""
    client, run_id, output_dir = draft_session

    # Simulate the race by flipping the row to 'running' AFTER the
    # request lands but BEFORE update_run_config could complete. We
    # emulate that by patching repo.update_run_config to first do the
    # status flip, then call through. With the atomic-guard fix the
    # UPDATE's WHERE clause will reject the write and the endpoint
    # returns 409.
    import db.repository as repo

    real_update = repo.update_run_config

    def race_then_update(conn, rid, patch):
        conn.execute("UPDATE runs SET status='running' WHERE id = ?", (rid,))
        conn.commit()
        return real_update(conn, rid, patch)

    with patch_target(repo, "update_run_config", race_then_update):
        resp = client.patch(
            f"/api/runs/{run_id}", json={"filing_level": "group"}
        )

    # With the atomic guard, the UPDATE finds zero matching rows (status
    # is now 'running') and the endpoint returns 409. Without it, the
    # PATCH silently overwrites a started run's audit-trail config.
    assert resp.status_code == 409, (
        f"PATCH must reject post-flip writes (peer-review MEDIUM #4); "
        f"got {resp.status_code} {resp.text}"
    )

    persisted = _read_config(output_dir, run_id)
    assert persisted is None, (
        "Stored config must be unchanged when the race-flipped row was "
        "not 'draft' at write time"
    )


# Helper — `unittest.mock.patch.object` would work but the import dance
# is heavier than the one-line context manager we need.
class patch_target:
    """Tiny in-test monkeypatch that restores on exit."""
    def __init__(self, target, attr, replacement):
        self.target, self.attr, self.replacement = target, attr, replacement
    def __enter__(self):
        self.original = getattr(self.target, self.attr)
        setattr(self.target, self.attr, self.replacement)
    def __exit__(self, *_):
        setattr(self.target, self.attr, self.original)


def test_patch_404_on_missing_run(tmp_path, monkeypatch):
    """A PATCH against a non-existent run id returns 404 (not 500)."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    db_path = output_dir / "xbrl_agent.db"
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db_path)
    init_db(db_path)

    client = TestClient(server.app)
    resp = client.patch("/api/runs/9999", json={"filing_level": "group"})
    assert resp.status_code == 404
