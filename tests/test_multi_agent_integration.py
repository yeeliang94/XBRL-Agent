"""Integration test for run_multi_agent_stream (Phase 7 post-peer-review).

Tests the real orchestration path with mocked coordinator/merger/checks,
verifying actual SSE payloads and DB side effects.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from statement_types import StatementType
from coordinator import AgentResult, CoordinatorResult
from cross_checks.framework import CrossCheckResult
from workbook_merger import MergeResult


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """Set up a session directory with a fake PDF and wire server paths."""
    session_id = "test-integration-session"
    out = tmp_path / "output"
    (out / session_id).mkdir(parents=True)
    (out / session_id / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")

    import server
    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", out / "xbrl_agent.db")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_URL", "")

    return TestClient(server.app), session_id, out


def _parse_sse(text: str) -> list[dict]:
    events = []
    current_event = None
    current_data = None
    for line in text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            current_data = line[6:].strip()
        elif line == "" and current_event and current_data:
            try:
                data = json.loads(current_data)
            except json.JSONDecodeError:
                data = current_data
            events.append({"event": current_event, "data": data})
            current_event = None
            current_data = None
    return events


class TestMultiAgentIntegration:
    """Integration test with mocked coordinator but real server orchestration."""

    def test_full_orchestration_path(self, session_env):
        """POST /api/run → coordinator → merger → cross-checks → DB persistence."""
        client, session_id, out = session_env

        # Mock coordinator result with two succeeded agents
        fake_coordinator_result = CoordinatorResult(agent_results=[
            AgentResult(
                statement_type=StatementType.SOFP,
                variant="CuNonCu",
                status="succeeded",
                workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
            ),
            AgentResult(
                statement_type=StatementType.SOPL,
                variant="Function",
                status="succeeded",
                workbook_path=str(out / session_id / "SOPL_filled.xlsx"),
            ),
        ])

        fake_merge = MergeResult(success=True, output_path=str(out / session_id / "filled.xlsx"), sheets_copied=3)

        fake_checks = [
            CrossCheckResult(name="sofp_balance", status="passed", expected=100.0, actual=100.0, diff=0.0, tolerance=1.0, message="OK"),
            CrossCheckResult(name="sopl_to_socie_profit", status="pending", message="SOCIE not extracted"),
        ]

        run_config = {
            "statements": ["SOFP", "SOPL"],
            "variants": {"SOFP": "CuNonCu", "SOPL": "Function"},
            "models": {},
            "infopack": None,
            "use_scout": False,
        }

        async def mock_coordinator_run(config, infopack=None, event_queue=None, session_id=None):
            if event_queue is not None:
                for idx, ar in enumerate(fake_coordinator_result.agent_results):
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
            return fake_coordinator_result

        with patch("server._create_proxy_model", return_value="fake-model"), \
             patch("coordinator.run_extraction", side_effect=mock_coordinator_run), \
             patch("workbook_merger.merge", return_value=fake_merge), \
             patch("cross_checks.framework.run_all", return_value=fake_checks):

            resp = client.post(f"/api/run/{session_id}", json=run_config)

        assert resp.status_code == 200
        events = _parse_sse(resp.text)

        # Should have: status, 2 x complete (per agent), run_complete
        event_types = [e["event"] for e in events]
        assert "status" in event_types
        assert event_types.count("complete") == 2
        assert "run_complete" in event_types

        # Per-agent events should have agent_id and agent_role
        completes = [e for e in events if e["event"] == "complete"]
        roles = {e["data"]["agent_role"] for e in completes}
        assert roles == {"SOFP", "SOPL"}
        for c in completes:
            assert "agent_id" in c["data"]

        # run_complete should report success and include cross-checks
        rc = [e for e in events if e["event"] == "run_complete"][0]["data"]
        assert rc["success"] is True
        assert len(rc["cross_checks"]) == 2
        assert rc["cross_checks"][0]["name"] == "sofp_balance"
        assert rc["cross_checks"][0]["status"] == "passed"

        # Verify DB persistence
        db_path = out / "xbrl_agent.db"
        assert db_path.exists()
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        runs = conn.execute("SELECT * FROM runs").fetchall()
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"

        agents = conn.execute("SELECT * FROM run_agents ORDER BY id").fetchall()
        assert len(agents) == 2
        assert agents[0]["statement_type"] == "SOFP"
        assert agents[1]["statement_type"] == "SOPL"

        # Verify coarse events persisted (finding 5 fix)
        events_db = conn.execute("SELECT * FROM agent_events ORDER BY id").fetchall()
        assert len(events_db) >= 2  # at least status + complete per agent

        # Verify cross-checks persisted
        checks_db = conn.execute("SELECT * FROM cross_checks ORDER BY id").fetchall()
        assert len(checks_db) == 2
        assert checks_db[0]["check_name"] == "sofp_balance"
        assert checks_db[0]["status"] == "passed"

        conn.close()

    def test_merge_failure_degrades_status(self, session_env):
        """If merge fails, run status should be 'completed_with_errors', not 'completed'."""
        client, session_id, out = session_env

        fake_coordinator_result = CoordinatorResult(agent_results=[
            AgentResult(statement_type=StatementType.SOFP, variant="CuNonCu",
                        status="succeeded", workbook_path="/tmp/SOFP_filled.xlsx"),
        ])

        # Merge fails
        fake_merge = MergeResult(success=False, errors=["Disk full"])

        run_config = {
            "statements": ["SOFP"],
            "variants": {"SOFP": "CuNonCu"},
            "models": {},
            "infopack": None,
            "use_scout": False,
        }

        async def mock_coordinator_run(config, infopack=None, event_queue=None, session_id=None):
            if event_queue is not None:
                for idx, ar in enumerate(fake_coordinator_result.agent_results):
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
            return fake_coordinator_result

        with patch("server._create_proxy_model", return_value="fake-model"), \
             patch("coordinator.run_extraction", side_effect=mock_coordinator_run), \
             patch("workbook_merger.merge", return_value=fake_merge), \
             patch("cross_checks.framework.run_all", return_value=[]):

            resp = client.post(f"/api/run/{session_id}", json=run_config)

        events = _parse_sse(resp.text)
        rc = [e for e in events if e["event"] == "run_complete"][0]["data"]

        # success should be False because merge failed
        assert rc["success"] is False
        assert rc["merged_workbook"] is None
        assert "Disk full" in rc["merge_errors"]

        # DB should show degraded status
        db_path = out / "xbrl_agent.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs").fetchone()
        assert run["status"] == "completed_with_errors"
        conn.close()
