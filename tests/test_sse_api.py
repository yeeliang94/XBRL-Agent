"""SSE API tests — GET /api/run/{session_id} with async streaming."""
import json

import server
from fastapi.testclient import TestClient
from server import active_runs, app

client = TestClient(app)


async def _mock_iter_agent_events(**kwargs):
    """Fake async generator that yields a sequence of SSE events."""
    yield {"event": "status", "data": {"phase": "reading_template", "message": "Starting..."}}
    yield {"event": "tool_call", "data": {"tool_name": "read_template", "tool_call_id": "tc_1", "args": {}}}
    yield {"event": "tool_result", "data": {"tool_name": "read_template", "tool_call_id": "tc_1", "result_summary": "50 fields", "duration_ms": 100}}
    yield {"event": "complete", "data": {"success": True, "output_path": "", "excel_path": "", "trace_path": "", "total_tokens": 5000, "cost": 0.003}}


def test_sse_streams_events(tmp_path, monkeypatch):
    """SSE endpoint streams events from the async generator."""
    output_dir = tmp_path / "output"
    session_dir = output_dir / "test-session"
    session_dir.mkdir(parents=True)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    # Point ENV_FILE at a nonexistent path so load_dotenv() cannot overwrite the
    # monkeypatched env vars below with stale values from a real .env on disk.
    monkeypatch.setattr(server, "ENV_FILE", tmp_path / "nonexistent.env")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROXY_URL", "http://localhost:4000")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    monkeypatch.setattr(server, "_find_template", lambda _: "/fake/template.xlsx")
    monkeypatch.setattr(server, "iter_agent_events", _mock_iter_agent_events)

    resp = client.get("/api/run/test-session")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    # Parse SSE blocks
    lines = resp.text.strip().split("\n\n")
    events = []
    for block in lines:
        event_line = [l for l in block.split("\n") if l.startswith("event:")]
        data_line = [l for l in block.split("\n") if l.startswith("data:")]
        if event_line and data_line:
            events.append({
                "event": event_line[0].replace("event: ", ""),
                "data": json.loads(data_line[0].replace("data: ", "")),
            })

    assert len(events) == 4
    assert events[0]["event"] == "status"
    assert events[1]["event"] == "tool_call"
    assert events[1]["data"]["tool_call_id"] == "tc_1"
    assert events[2]["event"] == "tool_result"
    assert events[3]["event"] == "complete"


def test_sse_rejects_missing_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path / "output")
    resp = client.get("/api/run/nonexistent-session")
    assert resp.status_code == 404


def test_sse_rejects_concurrent_run(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    session_dir = output_dir / "dup-session"
    session_dir.mkdir(parents=True)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)

    # Simulate already running (active_runs now stores bool, not EventQueue)
    active_runs["dup-session"] = True

    resp = client.get("/api/run/dup-session")
    assert resp.status_code == 409
    active_runs.pop("dup-session", None)
