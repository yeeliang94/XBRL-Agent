"""PR-1 (rewrite Phase 5.2): the CLI `run_agent` drives the SAME canonical
pipeline as the web server.

Before this, a CLI run built a bare RunConfig with no run_id/db_path and merged
scratch workbooks directly — skipping fact projection, the audit runs row, the
canonical fact-export, and the reviewer pass. It now drives
``server.run_multi_agent_stream``. This test pins that contract with a fully
mocked pipeline (no real LLM): the coordinator asserts it received run_id +
db_path, and the audit DB ends up with a completed runs row + per-agent rows.
"""
import sqlite3
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest

import run
import server
from statement_types import StatementType
from coordinator import AgentResult as CoordAgentResult, CoordinatorResult
from cross_checks.framework import CrossCheckResult


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir(parents=True)
    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", out / "xbrl_agent.db")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("TEST_MODEL", "test-model")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    # All cross-checks pass in the mock, so the reviewer never triggers; pin
    # auto-review off anyway so no reviewer LLM path can be reached.
    monkeypatch.setenv("XBRL_AUTO_REVIEW", "false")
    pdf = tmp_path / "src.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    return out, str(pdf)


def test_cli_run_agent_drives_canonical_pipeline(cli_env):
    out, pdf = cli_env
    stmts = {StatementType.SOFP, StatementType.SOPL}

    async def mock_coordinator_run(config, infopack=None, event_queue=None,
                                   session_id=None, **kw):
        # The heart of PR-1: fact projection needs run_id + db_path on the
        # RunConfig. The bare-CLI path passed neither.
        assert config.run_id is not None, "CLI must thread run_id into RunConfig"
        assert config.db_path, "CLI must thread db_path into RunConfig"
        results = []
        for stmt in sorted(config.statements_to_run, key=lambda s: s.value):
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"{stmt.value}-Main"
            ws["A1"] = stmt.value
            wbp = str(Path(config.output_dir) / f"{stmt.value}_filled.xlsx")
            wb.save(wbp)
            wb.close()
            if event_queue is not None:
                await event_queue.put({"event": "complete", "data": {
                    "success": True, "agent_id": stmt.value.lower(),
                    "agent_role": stmt.value, "workbook_path": wbp,
                    "error": None,
                }})
            results.append(CoordAgentResult(
                statement_type=stmt, variant=None, status="succeeded",
                workbook_path=wbp,
            ))
        if event_queue is not None:
            await event_queue.put(None)
        return CoordinatorResult(agent_results=results)

    fake_checks = [CrossCheckResult(
        name="sofp_balance", status="passed", expected=1.0, actual=1.0,
        diff=0.0, tolerance=1.0, message="OK",
    )]

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("concept_model.bootstrap.import_all_face_templates",
               return_value=[1, 2]), \
         patch("coordinator.run_extraction", side_effect=mock_coordinator_run), \
         patch("cross_checks.framework.run_all", return_value=fake_checks), patch("cross_checks.framework.run_all_facts", return_value=fake_checks):
        result = run.run_agent(
            pdf_path=pdf, model="test-model", output_dir=str(out),
            statements=stmts,
        )

    assert result.success is True
    assert result.errors == []
    assert Path(result.output_excel_path).exists()

    # The CLI now persists an audit run exactly like the web UI.
    db_path = out / "xbrl_agent.db"
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        runs = conn.execute("SELECT * FROM runs").fetchall()
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"
        agents = conn.execute("SELECT * FROM run_agents").fetchall()
        assert len(agents) == 2
    finally:
        conn.close()
