"""SSE API tests — POST /api/run/{session_id} with multi-agent streaming."""
import json
from unittest.mock import patch, AsyncMock

import server
from fastapi.testclient import TestClient
from server import app
from coordinator import AgentResult, CoordinatorResult
from cross_checks.framework import CrossCheckResult
from statement_types import StatementType

client = TestClient(app)


def test_sse_streams_events(tmp_path, monkeypatch):
    """POST /api/run/{session_id} streams SSE events from multi-agent orchestration."""
    output_dir = tmp_path / "output"
    session_dir = output_dir / "test-session"
    session_dir.mkdir(parents=True)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", output_dir / "xbrl_agent.db")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_URL", "")

    fake_result = CoordinatorResult(agent_results=[
        AgentResult(statement_type=StatementType.SOFP, variant="CuNonCu",
                    status="succeeded", workbook_path=str(session_dir / "SOFP_filled.xlsx")),
    ])

    # Create a minimal workbook file
    import openpyxl
    wb = openpyxl.Workbook()
    wb.save(str(session_dir / "SOFP_filled.xlsx"))
    wb.close()

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    async def mock_coordinator_run(config, infopack=None, event_queue=None, session_id=None, **_kwargs):
        if event_queue is not None:
            for idx, ar in enumerate(fake_result.agent_results):
                agent_id = ar.statement_type.value.lower()
                await event_queue.put({
                    "event": "complete",
                    "data": {
                        "success": ar.status == "succeeded",
                        "agent_id": agent_id,
                        "agent_role": ar.statement_type.value,
                        "workbook_path": ar.workbook_path,
                        "error": ar.error,
                    },
                })
            await event_queue.put(None)
        return fake_result

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=mock_coordinator_run), \
         patch("cross_checks.framework.run_all", return_value=[]):

        resp = client.post("/api/run/test-session", json=run_config)

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    # Parse SSE events
    events = []
    current_event = None
    for line in resp.text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: ") and current_event:
            events.append({
                "event": current_event,
                "data": json.loads(line[6:]),
            })
            current_event = None

    event_types = [e["event"] for e in events]
    assert "status" in event_types
    assert "complete" in event_types
    assert "run_complete" in event_types


def test_sse_rejects_missing_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path / "output")
    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }
    resp = client.post("/api/run/nonexistent-session", json=run_config)
    assert resp.status_code == 404


def test_sse_rejects_concurrent_run(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    session_dir = output_dir / "dup-session"
    session_dir.mkdir(parents=True)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    # Re-read `active_runs` from the module each time. Other tests
    # (e.g. test_recheck_endpoint, test_phase6_mpers) call
    # ``importlib.reload(server)`` which swaps ``server.active_runs``
    # for a fresh empty set. If we kept a module-level
    # ``from server import active_runs`` import, that name would still
    # point at the *pre-reload* set object — our ``add()`` would land
    # there, but the endpoint reads the new ``server.active_runs`` and
    # returns 200 instead of the expected 409.
    server.active_runs.add("dup-session")

    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }
    resp = client.post("/api/run/dup-session", json=run_config)
    assert resp.status_code == 409
    server.active_runs.discard("dup-session")
