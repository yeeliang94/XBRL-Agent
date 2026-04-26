"""Peer-review #2 (HIGH, RUN-REVIEW follow-up): for the existing_run_id
path, a failure on the cosmetic config-refresh UPDATE must NOT leave
the run row stuck at 'running'.

Failure mode the fix prevents:
- /api/runs/{id}/start has already flipped the row from draft → running
- run_multi_agent_stream() begins, opens db_conn, runs:
    UPDATE runs SET run_config_json = ?, scout_enabled = ? WHERE id = ?
- That UPDATE fails (disk full / WAL conflict / connection drop)
- Pre-fix: broad except closed db_conn, set it to None
- _safe_mark_finished(db_conn=None, ...) returns False without writing
- Run row sits at 'running' forever, violating gotcha #10 (every exit
  path reaches a terminal status)

The fix splits the open-connection step from the per-path INSERT/UPDATE
step. For the existing_run_id path, an UPDATE failure rolls back and
keeps db_conn alive so the terminal-status writer at the end works.
For the fresh-row path (existing_run_id is None), an INSERT failure
still nulls db_conn — there's no row to mark, so no audit is possible.
"""
from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import server
from coordinator import CoordinatorResult
from db.schema import init_db
from workbook_merger import MergeResult


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def draft_run_setup(tmp_path, monkeypatch):
    """Mirror of the harness in tests/test_runs_start_endpoint.py.
    Returns (client, run_id, session_id, output_dir, db_path)."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    db_path = output_dir / "xbrl_agent.db"
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db_path)
    fake_env = tmp_path / ".env-test"
    fake_env.write_text("")
    monkeypatch.setattr(server, "ENV_FILE", fake_env)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-xyz")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    init_db(db_path)

    client = TestClient(server.app)
    upload = client.post(
        "/api/upload",
        files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.0\nfake"), "application/pdf")},
    )
    payload = upload.json()
    run_id = payload["run_id"]
    session_id = payload["session_id"]
    # PATCH a minimal config so the start endpoint accepts.
    client.patch(
        f"/api/runs/{run_id}",
        json={
            "statements": ["SOFP"],
            "models": {"SOFP": "test-model"},
            "filing_level": "company",
            "filing_standard": "mfrs",
            "use_scout": False,
            "notes_to_run": [],
            "infopack": None,
            "variants": {},
        },
    )
    return client, run_id, session_id, output_dir, db_path


class _FlakyConnection:
    """Wrap a real sqlite3 Connection so a specific UPDATE statement
    raises on its first call. sqlite3.Connection is a built-in
    extension type so its methods can't be monkey-patched directly."""

    def __init__(self, real, *, fail_substring: str):
        self._real = real
        self._fail_substring = fail_substring
        self._fail_remaining = 1
        self.row_factory = real.row_factory  # access on test side

    def execute(self, sql, *args, **kwargs):
        if (
            self._fail_remaining > 0
            and isinstance(sql, str)
            and self._fail_substring in sql
        ):
            self._fail_remaining -= 1
            raise sqlite3.OperationalError("simulated disk full")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        # Forward everything else (commit, rollback, close, cursor,
        # row_factory setter, etc.) to the real connection.
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        if name in ("_real", "_fail_substring", "_fail_remaining", "row_factory"):
            object.__setattr__(self, name, value)
            if name == "row_factory":
                self._real.row_factory = value
        else:
            setattr(self._real, name, value)


def test_existing_run_id_update_failure_still_writes_terminal_status(
    draft_run_setup, monkeypatch,
) -> None:
    """The canonical fix scenario: simulate a failure on the cosmetic
    config-refresh UPDATE; the run row must still end in a terminal
    status, not stuck at 'running'.

    We wrap the sqlite3.Connection returned by sqlite3.connect() so
    the FIRST `UPDATE runs SET run_config_json …` statement raises;
    every other operation goes through to the real connection. That
    mirrors a transient WAL conflict on the cosmetic refresh while
    leaving the rest of the run able to finalize."""
    client, run_id, session_id, output_dir, db_path = draft_run_setup

    real_connect = sqlite3.connect
    flaky_count = {"created": 0}

    def flaky_connect(*args, **kwargs):
        real = real_connect(*args, **kwargs)
        # Only wrap the FIRST connection (the audit one inside
        # run_multi_agent_stream). Other connections (like the one
        # /api/runs/{id}/start opens to flip status) stay un-flaky.
        if flaky_count["created"] >= 1:
            return real
        flaky_count["created"] += 1
        return _FlakyConnection(
            real, fail_substring="UPDATE runs SET run_config_json"
        )

    async def quiet_coordinator(
        config, infopack=None, event_queue=None, session_id=None, **_kwargs
    ):
        if event_queue is not None:
            await event_queue.put(None)
        return CoordinatorResult(agent_results=[])

    monkeypatch.setattr(sqlite3, "connect", flaky_connect)

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=quiet_coordinator), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(
                 success=True,
                 output_path=str(output_dir / session_id / "filled.xlsx"),
                 sheets_copied=0,
             ),
         ), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/runs/{run_id}/start")

    assert resp.status_code == 200
    # Drain the SSE response so the generator runs to completion.
    list(resp.iter_lines())

    # The row must NOT be stuck at 'running' — gotcha #10
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            "SELECT status, ended_at FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] != "running", (
        f"The run row must reach a terminal status even when the "
        f"cosmetic config-refresh UPDATE fails; got status={row['status']!r}. "
        f"Pre-fix the broad except nulled db_conn and the terminal-status "
        f"writer silently no-opped, leaving 'running' permanently."
    )
    assert row["ended_at"] is not None
