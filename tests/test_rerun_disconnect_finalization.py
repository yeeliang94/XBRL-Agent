"""Regression test for rerun disconnect finalization (Option B).

When the SSE client disconnects mid-stream — e.g. a browser tab timeout
while the agent is making its post-``save_result`` wrap-up LLM call —
but the extraction agent has already written its workbook and the
coordinator is about to return a successful result, the post-pipeline
(merge + cross-checks + DB persistence + run finalization) must still
run to completion.

Pre-Option-B behaviour (the bug): ``server.run_multi_agent_stream``'s
GeneratorExit handler cancelled the coordinator and bailed immediately,
leaving ``filled.xlsx`` stale, the ``runs`` row as ``aborted``, and the
``run_agents`` row frozen at ``status='running'`` with ``ended_at=NULL``.
Observed in production on session 61ef1a1c-… where a final SOFP rerun
produced ``SOFP_filled.xlsx`` + ``SOFP_result.json`` on disk at 22:58,
but the merge never ran and the UI never saw ``run_complete``.

This test drives ``run_multi_agent_stream`` as an async generator so we
can trigger the disconnect at a precise point (``.aclose()`` after the
first ``yield``), which ``TestClient`` cannot do.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from coordinator import AgentResult, CoordinatorResult
from statement_types import StatementType
from workbook_merger import MergeResult


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """Mirror the fixture from test_server_run_lifecycle.py so the runs
    row is written to a throwaway DB and OUTPUT_DIR is scoped to tmp.
    """
    session_id = "test-disconnect-session"
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
    monkeypatch.setenv("TEST_MODEL", "test-model-default")
    monkeypatch.setenv("LLM_PROXY_URL", "")

    return session_id, out


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.asyncio
async def test_disconnect_after_agent_complete_still_finalizes_run(session_env):
    """Option B contract: disconnect after the agent finished its work does
    NOT drop merge / cross-checks / DB finalization.

    Scenario:
      1. Mock coordinator pushes a ``tool_result`` event, then returns a
         successful ``CoordinatorResult`` with a workbook path pointing
         at a file we pre-wrote on disk (simulating ``save_result``).
      2. Test pulls the first event, then ``.aclose()``s the generator
         to simulate the SSE stream dropping.
      3. Assertions run AFTER ``.aclose()`` returns — under Option B
         ``aclose()`` waits for the generator to finish the post-pipeline
         before returning.

    Contract checked against the audit DB (single source of truth):
      - ``runs.status`` ∈ {"completed", "completed_with_errors"} — never
        ``"aborted"``, because the extraction succeeded.
      - ``runs.merged_workbook_path`` points at the merged xlsx — the
        ``History → Download filled`` endpoint reads this.
      - ``run_agents.status == "succeeded"`` — not frozen at "running".
      - ``run_agents.ended_at`` is non-null.
    """
    from server import RunConfigRequest, run_multi_agent_stream

    session_id, out = session_env
    session_dir = out / session_id
    db_path = out / "xbrl_agent.db"

    # Pre-write the per-agent filled workbook the way ``save_result`` would,
    # so the post-pipeline's merge has something real to point at.
    sofp_wb = session_dir / "SOFP_filled.xlsx"
    sofp_wb.write_bytes(b"fake per-agent xlsx")
    merged_wb = session_dir / "filled.xlsx"

    async def successful_coordinator(
        config, infopack=None, event_queue=None, session_id=None,
        push_sentinel=True, **_kw,
    ):
        # One streamed event before completion — gives the test a real
        # yield point to disconnect from. Mirrors the real coordinator's
        # ``save_result`` tool_result shape.
        if event_queue is not None:
            await event_queue.put({
                "event": "tool_result",
                "data": {
                    "agent_id": "sofp",
                    "agent_role": "SOFP",
                    "tool_name": "save_result",
                    "tool_call_id": "call_test_save",
                },
            })
            await asyncio.sleep(0)
        return CoordinatorResult(agent_results=[
            AgentResult(
                statement_type=StatementType.SOFP,
                variant="CuNonCu",
                status="succeeded",
                workbook_path=str(sofp_wb),
            )
        ])

    body = RunConfigRequest(
        statements=["SOFP"],
        variants={"SOFP": "CuNonCu"},
        models={},
        infopack=None,
        use_scout=False,
    )

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch("coordinator.run_extraction", side_effect=successful_coordinator), \
         patch("workbook_merger.merge", return_value=MergeResult(
             success=True,
             output_path=str(merged_wb),
             sheets_copied=1,
         )), \
         patch("cross_checks.framework.run_all", return_value=[]):
        gen = run_multi_agent_stream(
            session_id=session_id,
            session_dir=session_dir,
            run_config=body,
            api_key="test-key",
            proxy_url="",
            model_name="test-model",
        )
        # Pull events until we reach the drain loop — we want the disconnect
        # to land at a drain-loop ``yield event`` (the real-world equivalent
        # of a client dropping mid-stream), not at the ``starting`` pre-amble
        # yield which happens before the drain loop is even set up.
        # In order: (1) ``status: starting`` [pre-drain], (2) the first
        # coordinator-emitted event (a drain-loop yield).
        await gen.__anext__()  # status: starting
        await gen.__anext__()  # first drain-loop yield (tool_result)
        # Simulate the SSE client dropping: inject GeneratorExit at the
        # current yield. Under Option B this must not kill the pipeline;
        # aclose() blocks until the generator returns naturally.
        await gen.aclose()

    # --- DB assertions: everything got finalized despite the disconnect ---
    assert db_path.exists(), "audit DB was never created"
    conn = _open_db(db_path)
    try:
        runs = [dict(r) for r in conn.execute("SELECT * FROM runs").fetchall()]
        agents = [dict(r) for r in conn.execute("SELECT * FROM run_agents").fetchall()]
    finally:
        conn.close()

    assert len(runs) == 1, f"expected 1 run row, got {len(runs)}"
    run_row = runs[0]
    assert run_row["status"] in {"completed", "completed_with_errors"}, (
        f"expected terminal non-aborted status (agent succeeded + merge succeeded), "
        f"got {run_row['status']!r}"
    )
    assert run_row["merged_workbook_path"] == str(merged_wb), (
        "merged workbook pointer must be persisted for History's download endpoint"
    )
    assert run_row["ended_at"] is not None, "ended_at must be stamped"

    assert len(agents) == 1, f"expected 1 run_agents row, got {len(agents)}"
    agent_row = agents[0]
    assert agent_row["status"] == "succeeded", (
        f"run_agents row must reflect the agent's real outcome, "
        f"got status={agent_row['status']!r} (running = orphan)"
    )
    assert agent_row["ended_at"] is not None, "run_agents.ended_at must be stamped"
    assert agent_row["workbook_path"] == str(sofp_wb)
