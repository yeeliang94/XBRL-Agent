"""Plan agent-efficiency Step 0.1 — reviewer per-turn telemetry persistence.

The reviewer pass always COLLECTED per-turn v8 metric rows (run_agent_loop
mutates ``_turn_records`` in place) but discarded them after computing
rollups — so every CORRECTION run_agents row had zero run_agent_turns rows
and no ``node_kind`` breakdown existed to measure reviewer cost by activity.

Pins the new contract:

* ``_run_reviewer_pass``'s outcome carries ``turn_records`` (the raw v8
  dicts) on success AND on the exception path (a failed pass still burned
  real turns — the case measurement cares about most).
* The rows round-trip through ``repo.insert_agent_turns`` under a
  CORRECTION run_agent row (what the server finalize block does).
* ``repo.replace_agent_turns`` replaces rather than appends — the manual
  re-review reuses the same CORRECTION row and overwrites its rollups, so
  appended turn rows would disagree with them.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from db import repository as repo
from db.schema import init_db
from concept_model.facts_api import FactWrite, write_fact
from cross_checks.framework import CrossCheckResult


_TEMPLATE = "mfrs-company-sofp-test-v1"
PARENT = "00000000-0000-0000-0000-0000000000aa"
LEAF1 = "00000000-0000-0000-0000-0000000000b1"
LEAF2 = "00000000-0000-0000-0000-0000000000b2"


def _seed(tmp_path):
    db = tmp_path / "turns.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    run_id = int(conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES (?,?,?,?)",
        ("2026-07-23T00:00:00Z", "x.pdf", "running", "2026-07-23T00:00:00Z"),
    ).lastrowid)
    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path, shape) "
        "VALUES (?, 'x.xlsx', 'linear')", (_TEMPLATE,),
    )
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) VALUES "
        "(?, ?, 'COMPUTED', 'Total assets', 'SOFP', 10, 'B')",
        (PARENT, _TEMPLATE),
    )
    for uid, label, row in [(LEAF1, "Cash", 5), (LEAF2, "Receivables", 6)]:
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) VALUES "
            "(?, ?, 'LEAF', ?, 'SOFP', ?, 'B')",
            (uid, _TEMPLATE, label, row),
        )
        conn.execute(
            "INSERT INTO concept_edges(parent_uuid, child_uuid, coefficient) "
            "VALUES (?, ?, 1.0)", (PARENT, uid),
        )
    conn.commit()
    conn.close()
    write_fact(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company", value=100.0,
        value_status="observed", source="extraction", actor="agent"))
    write_fact(db, run_id, FactWrite(
        concept_uuid=LEAF2, period="CY", entity_scope="Company", value=50.0,
        value_status="observed", source="extraction", actor="agent"))
    from concept_model.cascade import recompute_after_turn
    recompute_after_turn(db, run_id)
    return db, run_id


_FAILED = [CrossCheckResult(
    name="sofp_assets_balance", status="failed", expected=170.0,
    actual=150.0, diff=20.0, message="assets total off by 20",
    target_sheet="SOFP", target_row=10)]


def _fix_cash_scripted(messages, info: AgentInfo) -> ModelResponse:
    for m in messages:
        for part in getattr(m, "parts", []):
            if part.part_kind == "tool-return":
                return ModelResponse(parts=[TextPart("done")])
    return ModelResponse(parts=[ToolCallPart(
        tool_name="apply_fixes",
        args={"fixes": [{"concept_uuid": LEAF1, "value": 120.0,
                         "reason": "extraction misread 100; PDF shows 120",
                         "evidence": "page 12: Cash 120"}]})])


@pytest.mark.asyncio
async def test_outcome_carries_turn_records_and_they_persist(tmp_path):
    from server import _run_reviewer_pass

    db, run_id = _seed(tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    outcome = await _run_reviewer_pass(
        failed_checks=_FAILED, conflicts=[],
        model=FunctionModel(_fix_cash_scripted),
        filing_level="company", event_queue=queue, db_path=db, run_id=run_id)

    records = outcome.get("turn_records")
    assert isinstance(records, list) and records, (
        "outcome must carry the raw per-turn rows for the finalizer")
    kinds = {t.get("node_kind") for t in records}
    assert "model_request" in kinds, (
        "activity measurement keys on node_kind='model_request'")
    assert "call_tools" in kinds

    # What the server finalize block does: persist under the CORRECTION row.
    conn = sqlite3.connect(str(db))
    try:
        agent_id = repo.create_run_agent(
            conn, run_id, statement_type="CORRECTION", variant=None,
            model="test-model")
        repo.insert_agent_turns(conn, agent_id, records)
        conn.commit()
        n = conn.execute(
            "SELECT count(*) FROM run_agent_turns WHERE run_agent_id=?",
            (agent_id,)).fetchone()[0]
        assert n == len(records)
        model_reqs = conn.execute(
            "SELECT count(*) FROM run_agent_turns WHERE run_agent_id=? "
            "AND node_kind='model_request'", (agent_id,)).fetchone()[0]
        assert model_reqs >= 1
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_failure_path_still_carries_partial_turns(tmp_path):
    """A pass that dies mid-run must still hand over the turns that ran —
    failed passes are exactly what the cost inventory needs to see."""
    from server import _run_reviewer_pass

    db, run_id = _seed(tmp_path)
    calls = {"n": 0}

    def _tool_then_crash(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="calculator", args={"expressions": ["1+1"]})])
        raise RuntimeError("provider blew up mid-pass")

    queue: asyncio.Queue = asyncio.Queue()
    outcome = await _run_reviewer_pass(
        failed_checks=_FAILED, conflicts=[],
        model=FunctionModel(_tool_then_crash),
        filing_level="company", event_queue=queue, db_path=db, run_id=run_id)

    assert outcome["error"] == "reviewer_exception"
    records = outcome.get("turn_records")
    assert isinstance(records, list) and records, (
        "exception path must still carry the turns that ran before the crash")


def test_replace_agent_turns_replaces_not_appends(tmp_path: Path):
    db = tmp_path / "replace.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        run_id = int(conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-07-23T00:00:00Z','x.pdf','running')").lastrowid)
        agent_id = repo.create_run_agent(
            conn, run_id, statement_type="CORRECTION", variant=None,
            model="test-model")
        first = [
            {"turn_index": i, "node_kind": "model_request",
             "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
            for i in range(3)
        ]
        repo.insert_agent_turns(conn, agent_id, first)
        assert conn.execute(
            "SELECT count(*) FROM run_agent_turns WHERE run_agent_id=?",
            (agent_id,)).fetchone()[0] == 3

        # Re-review pass: rollups get overwritten, turn rows must too.
        repo.replace_agent_turns(conn, agent_id, [
            {"turn_index": 0, "node_kind": "model_request",
             "total_tokens": 9}])
        assert conn.execute(
            "SELECT count(*) FROM run_agent_turns WHERE run_agent_id=?",
            (agent_id,)).fetchone()[0] == 1

        # Empty replacement still clears — a silent re-run must not inherit
        # the previous pass's rows.
        repo.replace_agent_turns(conn, agent_id, [])
        assert conn.execute(
            "SELECT count(*) FROM run_agent_turns WHERE run_agent_id=?",
            (agent_id,)).fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_non_streamed_model_request_rows_carry_latency_and_tokens(tmp_path):
    """The reviewer does not stream model nodes, so PydanticAI runs each
    request between loop bodies. Its wall time and tokens must land on the
    model_request row (not 0 ms, not on the following tool row), and each
    persisted turn keeps its own timestamp — otherwise request latency on a
    slow proxy cannot be told apart from tool time."""
    from server import _run_reviewer_pass

    db, run_id = _seed(tmp_path)

    async def _slow_fix(messages, info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(0.12)
        return _fix_cash_scripted(messages, info)

    queue: asyncio.Queue = asyncio.Queue()
    outcome = await _run_reviewer_pass(
        failed_checks=_FAILED, conflicts=[],
        model=FunctionModel(_slow_fix),
        filing_level="company", event_queue=queue, db_path=db, run_id=run_id)

    records = outcome["turn_records"]
    model_rows = [t for t in records if t.get("node_kind") == "model_request"]
    tool_rows = [t for t in records if t.get("node_kind") == "call_tools"]
    assert model_rows and tool_rows
    assert all(t["duration_ms"] >= 100 for t in model_rows)
    assert all(t["prompt_tokens"] > 0 and t["usage_status"] == "complete"
               for t in model_rows)
    assert all(t["total_tokens"] == 0 for t in tool_rows)
    assert all(t["duration_ms"] < 100 for t in tool_rows)

    conn = sqlite3.connect(str(db))
    try:
        agent_id = repo.create_run_agent(
            conn, run_id, statement_type="CORRECTION", variant=None,
            model="test-model")
        repo.insert_agent_turns(conn, agent_id, records)
        conn.commit()
        stamps = [r[0] for r in conn.execute(
            "SELECT ts FROM run_agent_turns WHERE run_agent_id=? "
            "AND node_kind='model_request' ORDER BY turn_index", (agent_id,))]
        assert len(set(stamps)) == len(stamps)
        ledger = conn.execute(
            "SELECT started_at, ended_at, input_tokens FROM model_usage_calls "
            "WHERE run_agent_id=? AND status != 'bookkeeping' "
            "ORDER BY request_index", (agent_id,)).fetchall()
        assert ledger and all(start < end and tokens > 0
                              for start, end, tokens in ledger)
    finally:
        conn.close()
