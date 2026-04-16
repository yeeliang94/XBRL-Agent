"""Server-level run lifecycle tests (Phase 1, Step 1.5).

These tests pin down the contract that **every** run — success, failure,
cancellation, or client disconnect — ends up as a terminal row in the DB
with a sensible status. History surfaces the failures users most need to
see, so nothing can slip through the cracks.

All tests mock the coordinator so they're deterministic and fast.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from statement_types import StatementType
from coordinator import AgentResult, CoordinatorResult
from cross_checks.framework import CrossCheckResult
from workbook_merger import MergeResult


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """Set up a session directory with a fake PDF and wire server paths.

    Mirrors the fixture from test_multi_agent_integration.py — keeping it
    local so future refactors of one do not silently break the other.
    """
    session_id = "test-lifecycle-session"
    out = tmp_path / "output"
    (out / session_id).mkdir(parents=True)
    (out / session_id / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")

    import server
    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", out / "xbrl_agent.db")
    # Point ENV_FILE at a temp file so the endpoint's load_dotenv(override=True)
    # can't clobber our monkeypatched env vars with the real repo .env.
    fake_env = tmp_path / ".env-test"
    fake_env.write_text("")
    monkeypatch.setattr(server, "ENV_FILE", fake_env)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("TEST_MODEL", "test-model-default")
    monkeypatch.setenv("LLM_PROXY_URL", "")

    return TestClient(server.app), session_id, out


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _happy_coordinator(agent_results):
    """Factory for a mock coordinator that publishes a per-agent event then
    the sentinel. Mirrors the real coordinator's contract."""
    async def mock_run(config, infopack=None, event_queue=None, session_id=None, **_kwargs):
        if event_queue is not None:
            for ar in agent_results:
                await event_queue.put({
                    "event": "complete",
                    "data": {
                        "success": ar.status == "succeeded",
                        "agent_id": ar.statement_type.value.lower(),
                        "agent_role": ar.statement_type.value,
                        "workbook_path": ar.workbook_path,
                        "error": ar.error,
                    },
                })
            await event_queue.put(None)
        return CoordinatorResult(agent_results=list(agent_results))
    return mock_run


# ---------------------------------------------------------------------------
# 1. Row is created BEFORE the coordinator runs (not only at the end).
# ---------------------------------------------------------------------------

def test_run_row_created_before_coordinator_runs(session_env):
    """While the coordinator is still running, the DB must already contain a
    `runs` row with status='running', session_id populated, ended_at null.

    Implementation: use an asyncio.Event to pause the mocked coordinator
    mid-stream and snapshot the DB state from inside the pause.
    """
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"
    snapshot: dict = {}

    pause = asyncio.Event()

    async def paused_coordinator(config, infopack=None, event_queue=None, session_id=None, **_kwargs):
        # Snapshot DB while paused so the test can assert on the running row.
        assert db_path.exists(), "DB must be initialised before coordinator starts"
        conn = _open_db(db_path)
        try:
            rows = conn.execute("SELECT * FROM runs").fetchall()
            snapshot["rows"] = [dict(r) for r in rows]
        finally:
            conn.close()
        # Still need to produce a valid coordinator result so the endpoint
        # can finalise cleanly instead of erroring out.
        if event_queue is not None:
            await event_queue.put(None)
        return CoordinatorResult(agent_results=[])

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=paused_coordinator), \
         patch("workbook_merger.merge", return_value=MergeResult(success=True, output_path=str(out / session_id / "filled.xlsx"), sheets_copied=0)), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200

    # While the coordinator was running the snapshot must contain exactly
    # one row with status='running' and the lifecycle columns populated.
    assert "rows" in snapshot, "coordinator mock never ran"
    assert len(snapshot["rows"]) == 1
    row = snapshot["rows"][0]
    assert row["status"] == "running"
    assert row["session_id"] == session_id
    assert row["output_dir"] == str(out / session_id)
    assert row["started_at"]  # non-empty
    assert row["ended_at"] is None


# ---------------------------------------------------------------------------
# 2. Coordinator failure marks the row 'failed'.
# ---------------------------------------------------------------------------

def test_run_row_marked_failed_when_coordinator_raises(session_env):
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"

    async def boom_coordinator(config, infopack=None, event_queue=None, session_id=None, **_kwargs):
        if event_queue is not None:
            # Signal shutdown so the drain loop exits cleanly, then raise
            # from the task itself so the awaiter catches it.
            await event_queue.put(None)
        raise RuntimeError("coordinator exploded")

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=boom_coordinator), \
         patch("workbook_merger.merge", return_value=MergeResult(success=False, errors=["unreachable"])), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200  # SSE stream returns 200 regardless
    assert db_path.exists()
    conn = _open_db(db_path)
    try:
        rows = conn.execute("SELECT * FROM runs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["ended_at"] is not None


# ---------------------------------------------------------------------------
# 3. CancelledError mid-stream → status='aborted'.
# ---------------------------------------------------------------------------

def test_run_row_marked_aborted_on_cancel(session_env):
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"

    async def cancelled_coordinator(config, infopack=None, event_queue=None, session_id=None, **_kwargs):
        if event_queue is not None:
            await event_queue.put(None)
        raise asyncio.CancelledError()

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=cancelled_coordinator), \
         patch("workbook_merger.merge", return_value=MergeResult(success=False, errors=[])), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    conn = _open_db(db_path)
    try:
        rows = conn.execute("SELECT * FROM runs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["status"] == "aborted"
    assert rows[0]["ended_at"] is not None


# ---------------------------------------------------------------------------
# 4. Client disconnect: the finally block must finalize the row.
# ---------------------------------------------------------------------------

def test_client_disconnect_still_finalizes_row(session_env):
    """Simulated by having the coordinator raise GeneratorExit-equivalent.

    We can't easily drop the SSE client mid-stream with TestClient, so the
    contract we verify is: if the drain loop catches GeneratorExit /
    CancelledError, the finally block marks the row terminal. The
    test_run_row_marked_aborted_on_cancel case covers the happiest version
    of this path; here we additionally assert the row is never left in
    'running' even when the drain loop exits abruptly.
    """
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"

    async def runaway_coordinator(config, infopack=None, event_queue=None, session_id=None, **_kwargs):
        # Queue is drained by the server; then server closes the drain loop
        # and awaits the task. Simulate an unexpected exit.
        if event_queue is not None:
            await event_queue.put({"event": "status", "data": {"phase": "starting"}})
            await event_queue.put(None)
        raise RuntimeError("client gone — drain closed")

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=runaway_coordinator), \
         patch("workbook_merger.merge", return_value=MergeResult(success=False, errors=[])), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    conn = _open_db(db_path)
    try:
        rows = conn.execute("SELECT * FROM runs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    # Must not still be 'running' — either 'failed' or 'aborted' is acceptable.
    assert rows[0]["status"] in {"failed", "aborted"}
    assert rows[0]["ended_at"] is not None


# ---------------------------------------------------------------------------
# 5. Happy path persists merged_workbook_path.
# ---------------------------------------------------------------------------

def test_merged_workbook_path_persisted_on_success_path(session_env):
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"

    merged_path = str(out / session_id / "filled.xlsx")
    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
    ]

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=_happy_coordinator(agent_results)), \
         patch("workbook_merger.merge", return_value=MergeResult(success=True, output_path=merged_path, sheets_copied=1)), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    conn = _open_db(db_path)
    try:
        row = conn.execute("SELECT * FROM runs").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["merged_workbook_path"] == merged_path
    assert row["status"] == "completed"


# ---------------------------------------------------------------------------
# 6. Effective model is persisted on run_agents even when no override exists.
# ---------------------------------------------------------------------------

def test_effective_model_stored_per_agent_not_only_overrides(session_env):
    """Request has override for SOFP only; SOPL must still get the
    default model recorded on its run_agents row — never null/empty."""
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
        AgentResult(
            statement_type=StatementType.SOPL, variant="Function",
            status="succeeded",
            workbook_path=str(out / session_id / "SOPL_filled.xlsx"),
        ),
    ]

    run_config = {
        "statements": ["SOFP", "SOPL"],
        "variants": {"SOFP": "CuNonCu", "SOPL": "Function"},
        # Only SOFP has an override. SOPL must inherit the env default.
        "models": {"SOFP": "claude-sonnet-4-6"},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", side_effect=lambda name, *a, **k: f"model:{name}"), \
         patch("coordinator.run_extraction", side_effect=_happy_coordinator(agent_results)), \
         patch("workbook_merger.merge", return_value=MergeResult(success=True, output_path=str(out / session_id / "filled.xlsx"), sheets_copied=2)), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    conn = _open_db(db_path)
    try:
        agents = conn.execute(
            "SELECT statement_type, model FROM run_agents ORDER BY statement_type"
        ).fetchall()
    finally:
        conn.close()

    by_type = {a["statement_type"]: a["model"] for a in agents}
    assert set(by_type.keys()) == {"SOFP", "SOPL"}
    # Both must be non-empty strings; SOPL must carry the default, not SOFP's
    # override and not an empty string.
    assert by_type["SOFP"]
    assert by_type["SOPL"]
    assert by_type["SOPL"] != by_type["SOFP"]
    # The default path came from env TEST_MODEL=test-model-default
    assert "test-model-default" in by_type["SOPL"]


# ---------------------------------------------------------------------------
# 6b. Persisted model id must be the readable model_name attribute, not the
# class repr (`OpenAIChatModel()`). This is the bug Codex caught: real
# PydanticAI model instances have a useless `__str__`, and the previous code
# stored `str(model)`, which broke the History `models_used` display and
# the `?model=` filter for every real run. Test stubs hid the bug because
# they returned plain strings from `_create_proxy_model`.
# ---------------------------------------------------------------------------

class _FakeModel:
    """Mimics the PydanticAI Model contract: useless __str__, real model_name."""
    def __init__(self, model_name: str):
        self.model_name = model_name
    def __str__(self) -> str:
        # Matches the real OpenAIChatModel/GoogleModel repr — proves the
        # production code can't fall back to str() and pass.
        return f"{type(self).__name__}()"


def test_persisted_model_uses_model_name_attr_not_class_repr(session_env):
    """Regression for Codex review finding [P1]: when _create_proxy_model
    returns a real Model object (not a string), the persisted run_agents.model
    column must be the `model_name` attribute, not `str(model)` which
    serializes to a useless class repr."""
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
        AgentResult(
            statement_type=StatementType.SOPL, variant="Function",
            status="succeeded",
            workbook_path=str(out / session_id / "SOPL_filled.xlsx"),
        ),
    ]

    run_config = {
        "statements": ["SOFP", "SOPL"],
        "variants": {"SOFP": "CuNonCu", "SOPL": "Function"},
        # Override SOFP, leave SOPL on the env default. Both code paths must
        # extract `model_name` correctly.
        "models": {"SOFP": "claude-sonnet-4-6"},
        "infopack": None,
        "use_scout": False,
    }

    # Crucially: return a Model-like object, NOT a string. This is what the
    # real `_create_proxy_model` does.
    with patch("server._create_proxy_model", side_effect=lambda name, *a, **k: _FakeModel(name)), \
         patch("coordinator.run_extraction", side_effect=_happy_coordinator(agent_results)), \
         patch("workbook_merger.merge", return_value=MergeResult(success=True, output_path=str(out / session_id / "filled.xlsx"), sheets_copied=2)), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    conn = _open_db(db_path)
    try:
        agents = conn.execute(
            "SELECT statement_type, model FROM run_agents ORDER BY statement_type"
        ).fetchall()
    finally:
        conn.close()

    by_type = {a["statement_type"]: a["model"] for a in agents}
    # The bug surface: with the old code these would both be "_FakeModel()".
    assert by_type["SOFP"] == "claude-sonnet-4-6", f"Got {by_type['SOFP']!r}"
    assert by_type["SOPL"] == "test-model-default", f"Got {by_type['SOPL']!r}"
    # And specifically, the class repr must NOT leak through.
    for value in by_type.values():
        assert "FakeModel" not in value
        assert "()" not in value


# ---------------------------------------------------------------------------
# 7. run_config_json round-trips the whole request body verbatim.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 8–10. Early-validation failures must still land in History (peer-review fix).
# The runs row has to be created BEFORE statement parsing / infopack parsing /
# model construction, so even these early exits leave a terminal row behind.
# ---------------------------------------------------------------------------

def test_invalid_statement_type_still_creates_failed_row(session_env):
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"

    run_config = {
        "statements": ["NOT_A_STATEMENT"],
        "variants": {},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    assert db_path.exists(), (
        "Early validation failure still has to initialise the audit DB "
        "so History can surface the failure."
    )
    conn = _open_db(db_path)
    try:
        rows = conn.execute("SELECT * FROM runs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, "Exactly one run row must exist for the attempted run."
    # Must be in a terminal state, never stuck on 'running'.
    assert rows[0]["status"] in {"failed", "aborted"}
    assert rows[0]["ended_at"] is not None
    # The originating session is still captured so the user can correlate.
    assert rows[0]["session_id"] == session_id


def test_invalid_infopack_still_creates_failed_row(session_env):
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        # Cause from_json to blow up: pass a non-dict / malformed shape.
        "infopack": {"malformed": True, "page_refs": "not-a-list"},
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    conn = _open_db(db_path)
    try:
        rows = conn.execute("SELECT * FROM runs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["session_id"] == session_id


def test_model_construction_failure_still_creates_failed_row(session_env):
    """If `_create_proxy_model` itself raises (bad key, unreachable proxy),
    the row must still be recorded with status='failed' so the user can
    see 'I tried to run this PDF and it blew up at setup'."""
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    def bad_create_proxy_model(*args, **kwargs):
        raise RuntimeError("proxy unreachable")

    with patch("server._create_proxy_model", side_effect=bad_create_proxy_model):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    conn = _open_db(db_path)
    try:
        rows = conn.execute("SELECT * FROM runs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["session_id"] == session_id
    assert rows[0]["ended_at"] is not None


def test_run_config_json_round_trips_request_body(session_env):
    client, session_id, out = session_env
    db_path = out / "xbrl_agent.db"

    run_config = {
        "statements": ["SOFP", "SOPL"],
        "variants": {"SOFP": "CuNonCu", "SOPL": "Function"},
        "models": {"SOFP": "gpt-5.4"},
        "infopack": None,
        "use_scout": True,
    }

    agent_results = [
        AgentResult(statement_type=StatementType.SOFP, variant="CuNonCu", status="succeeded",
                    workbook_path=str(out / session_id / "SOFP_filled.xlsx")),
        AgentResult(statement_type=StatementType.SOPL, variant="Function", status="succeeded",
                    workbook_path=str(out / session_id / "SOPL_filled.xlsx")),
    ]

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=_happy_coordinator(agent_results)), \
         patch("workbook_merger.merge", return_value=MergeResult(success=True, output_path=str(out / session_id / "filled.xlsx"), sheets_copied=2)), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    conn = _open_db(db_path)
    try:
        row = conn.execute("SELECT run_config_json, scout_enabled FROM runs").fetchone()
    finally:
        conn.close()

    assert row is not None
    stored = json.loads(row["run_config_json"])
    # Every top-level field we sent in must be present.
    for key in ("statements", "variants", "models", "infopack", "use_scout"):
        assert key in stored, f"{key} missing from stored run_config_json"
    assert stored["statements"] == ["SOFP", "SOPL"]
    assert stored["variants"] == {"SOFP": "CuNonCu", "SOPL": "Function"}
    assert stored["models"] == {"SOFP": "gpt-5.4"}
    assert stored["use_scout"] is True
    # scout_enabled flag on the row mirrors the request.
    assert row["scout_enabled"] == 1


# ---------------------------------------------------------------------------
# 8. Original PDF filename is persisted, not the on-disk "uploaded.pdf".
#
# The upload endpoint saves every PDF as "<session_dir>/uploaded.pdf" so the
# filesystem is simple — but History must still show the user's real filename
# so the search filter is meaningful. A sidecar file captures the original
# name at upload time and the run lifecycle reads it back here.
# ---------------------------------------------------------------------------

def test_original_pdf_filename_persisted_to_runs_row(tmp_path, monkeypatch):
    """Given a session whose sidecar records the original upload name, the
    runs row's pdf_filename must match the ORIGINAL name, not 'uploaded.pdf'.
    """
    session_id = "test-original-name"
    out = tmp_path / "output"
    (out / session_id).mkdir(parents=True)
    (out / session_id / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    # Simulate what the upload endpoint does: stash the real filename.
    (out / session_id / "original_filename.txt").write_text(
        "FINCO-Audited-Financial-Statement-2021.pdf", encoding="utf-8"
    )

    import server
    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", out / "xbrl_agent.db")
    fake_env = tmp_path / ".env-test"
    fake_env.write_text("")
    monkeypatch.setattr(server, "ENV_FILE", fake_env)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("TEST_MODEL", "test-model-default")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    client = TestClient(server.app)

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=_happy_coordinator([])), \
         patch("workbook_merger.merge", return_value=MergeResult(success=True, output_path=str(out / session_id / "filled.xlsx"), sheets_copied=0)), \
         patch("cross_checks.framework.run_all", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    conn = _open_db(out / "xbrl_agent.db")
    try:
        row = conn.execute("SELECT pdf_filename FROM runs").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["pdf_filename"] == "FINCO-Audited-Financial-Statement-2021.pdf", (
        f"expected original filename, got {row['pdf_filename']!r}"
    )


def test_upload_endpoint_writes_original_filename_sidecar(tmp_path, monkeypatch):
    """The upload endpoint must save a sidecar next to uploaded.pdf so the
    lifecycle code has a single source of truth for the original name."""
    import server
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    client = TestClient(server.app)

    pdf_bytes = b"%PDF-1.4\n%fake content\n"
    resp = client.post(
        "/api/upload",
        files={"file": ("My Report 2024.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    session_id = body["session_id"]

    sidecar = out / session_id / "original_filename.txt"
    assert sidecar.exists(), "upload endpoint should create original_filename.txt"
    assert sidecar.read_text(encoding="utf-8").strip() == "My Report 2024.pdf"
    # And the regular uploaded.pdf file still exists
    assert (out / session_id / "uploaded.pdf").exists()
