"""End-to-end integration test — full flow without a real LLM."""
import io
import json

import server
from fastapi.testclient import TestClient
from server import active_runs, app

client = TestClient(app)


async def _mock_iter_agent_events(**kwargs):
    """Fake async generator that simulates a full extraction run."""
    session_dir = kwargs.get("output_dir", "")
    yield {"event": "status", "data": {"phase": "reading_template", "message": "Starting"}}
    yield {"event": "tool_call", "data": {"tool_name": "read_template", "tool_call_id": "tc_1", "args": {}}}
    yield {"event": "tool_result", "data": {"tool_name": "read_template", "tool_call_id": "tc_1", "result_summary": "50 fields", "duration_ms": 100}}
    yield {"event": "token_update", "data": {
        "prompt_tokens": 500, "completion_tokens": 200,
        "thinking_tokens": 0, "cumulative": 700, "cost_estimate": 0.0003,
    }}
    yield {"event": "complete", "data": {
        "success": True,
        "output_path": f"{session_dir}/result.json",
        "excel_path": f"{session_dir}/filled.xlsx",
        "trace_path": f"{session_dir}/conversation_trace.json",
        "total_tokens": 5000, "cost": 0.003,
    }}


def test_full_extraction_flow(tmp_path, monkeypatch):
    """Upload -> SSE stream -> download — all wired together."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROXY_URL", "http://localhost:4000")
    monkeypatch.setenv("TEST_MODEL", "test-model")

    # 1. Upload PDF
    pdf_content = b"%PDF-1.4 fake pdf"
    resp = client.post(
        "/api/upload",
        files={"file": ("test.pdf", io.BytesIO(pdf_content), "application/pdf")},
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    # 2. Pre-populate result files (simulating agent completion)
    session_dir = output_dir / session_id
    (session_dir / "filled.xlsx").write_bytes(b"fake excel")
    (session_dir / "result.json").write_text(json.dumps({"fields": []}))
    (session_dir / "conversation_trace.json").write_text("{}")

    # 3. Mock template finder and agent runner
    monkeypatch.setattr(server, "_find_template", lambda _: "/fake/template.xlsx")
    monkeypatch.setattr(server, "iter_agent_events", _mock_iter_agent_events)

    # 4. Start SSE stream
    resp = client.get(f"/api/run/{session_id}")
    assert resp.status_code == 200

    # Parse events
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

    assert any(e["event"] == "status" for e in events)
    assert any(e["event"] == "tool_call" for e in events)
    assert any(e["event"] == "token_update" for e in events)
    assert any(e["event"] == "complete" for e in events)

    # 5. Download files
    resp = client.get(f"/api/result/{session_id}/filled.xlsx")
    assert resp.status_code == 200

    resp = client.get(f"/api/result/{session_id}/result.json")
    assert resp.status_code == 200
    assert resp.json() == {"fields": []}

    # 6. Settings round-trip
    resp = client.post("/api/settings", json={
        "api_key": "test-key",
        "model": "vertex_ai.gemini-3-flash-preview",
        "proxy_url": "https://genai-sharedservice-emea.pwc.com",
    })
    assert resp.status_code == 200

    resp = client.get("/api/settings")
    assert resp.json()["api_key_set"] is True
    assert resp.json()["proxy_url"] == "https://genai-sharedservice-emea.pwc.com"
