"""Phase D — the canonical correction agent factory.

`create_canonical_correction_agent` builds a pydantic-ai agent whose tools
write resolutions through the facts API into run_concept_facts (not a scratch
xlsx), so the Concepts UI and the DB-exported download stay in sync. Driven
here with FunctionModel (no live LLM) to script a specific tool call against a
real conflict and assert the fact lands and the conflict closes.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel


REPO = Path(__file__).resolve().parent.parent
CO_SOFP = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


@pytest.fixture
def seeded(tmp_path: Path):
    from db.schema import init_db
    from concept_model.parser import parse_template
    from concept_model.importer import import_template

    db_path = tmp_path / "xbrl.db"
    init_db(db_path)
    tree = parse_template(str(CO_SOFP))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    import_template(db_path, jp)

    conn = sqlite3.connect(str(db_path))
    run_id = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES (?,?,?,?)",
        ("2026-05-25T00:00:00Z", "x.pdf", "running", "2026-05-25T00:00:00Z"),
    ).lastrowid
    # A COMPUTED concept to mark aggregate_only, and an open partial_state
    # conflict on it (as the cascade would create).
    computed = conn.execute(
        "SELECT concept_uuid FROM concept_nodes WHERE kind='COMPUTED' "
        "AND render_sheet='SOFP-CuNonCu' LIMIT 1"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO run_concept_conflicts(run_id, concept_uuid, period, "
        "entity_scope, kind, residual, detail, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, computed, "CY", "Company", "partial_state", 50.0,
         "observed parent != children sum", "open", "2026-05-25T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    return db_path, run_id, computed


def test_factory_registers_three_tools(seeded):
    from correction.canonical_agent import create_canonical_correction_agent

    db_path, run_id, _c = seeded
    agent, deps = create_canonical_correction_agent(
        model=TestModel(call_tools=[]), db_path=db_path, run_id=run_id)
    names = set()
    for ts in agent.toolsets:
        tools = getattr(ts, "tools", {})
        if isinstance(tools, dict):
            names.update(tools.keys())
    assert {"revise_leaf", "mark_aggregate_only", "mark_not_disclosed"} <= names


def test_factory_registers_read_tools(seeded):
    """Peer-review F2: the agent must be able to investigate before writing —
    read-only tools for conflict context, the child breakdown, and the PDF."""
    from correction.canonical_agent import create_canonical_correction_agent

    db_path, run_id, _c = seeded
    agent, _deps = create_canonical_correction_agent(
        model=TestModel(call_tools=[]), db_path=db_path, run_id=run_id)
    names = set()
    for ts in agent.toolsets:
        tools = getattr(ts, "tools", {})
        if isinstance(tools, dict):
            names.update(tools.keys())
    assert {"get_conflict_context", "get_child_facts", "view_pdf_pages"} <= names


def test_get_child_facts_returns_breakdown(seeded):
    """The agent can read a parent's child breakdown to locate the wrong leaf."""
    from correction.canonical_agent import create_canonical_correction_agent

    db_path, run_id, computed = seeded
    captured: list[str] = []

    def scripted(messages, info: AgentInfo) -> ModelResponse:
        for m in messages:
            for part in getattr(m, "parts", []):
                if part.part_kind == "tool-return":
                    captured.append(str(part.content))
                    return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="get_child_facts",
            args={"concept_uuid": computed})])

    agent, deps = create_canonical_correction_agent(
        model=FunctionModel(scripted), db_path=db_path, run_id=run_id)
    agent.run_sync("Investigate.", deps=deps)

    assert captured, "get_child_facts produced no tool return"
    # It reports the breakdown framing without performing any write.
    assert "children" in captured[0].lower()
    assert deps.writes_performed == 0


def test_view_pdf_pages_reports_missing_pdf_gracefully(seeded):
    """With no pdf_path wired, view_pdf_pages must say so, not crash."""
    from correction.canonical_agent import create_canonical_correction_agent

    db_path, run_id, _c = seeded
    captured: list[str] = []

    def scripted(messages, info: AgentInfo) -> ModelResponse:
        for m in messages:
            for part in getattr(m, "parts", []):
                if part.part_kind == "tool-return":
                    captured.append(str(part.content))
                    return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="view_pdf_pages", args={"pages": [1]})])

    agent, deps = create_canonical_correction_agent(
        model=FunctionModel(scripted), db_path=db_path, run_id=run_id,
        pdf_path=None)
    agent.run_sync("Look at the PDF.", deps=deps)
    assert captured and "not available" in captured[0].lower()


def test_mark_aggregate_only_writes_fact_and_closes_conflict(seeded):
    from correction.canonical_agent import create_canonical_correction_agent

    db_path, run_id, computed = seeded

    def scripted(messages, info: AgentInfo) -> ModelResponse:
        # First model turn: call mark_aggregate_only on the conflicted concept.
        # Subsequent turns (after the tool result): finish with text.
        for m in messages:
            for part in getattr(m, "parts", []):
                if part.part_kind == "tool-return":
                    return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="mark_aggregate_only",
            args={"concept_uuid": computed, "value": 1000.0,
                  "source": "Note 5", "period": "CY",
                  "entity_scope": "Company"},
        )])

    agent, deps = create_canonical_correction_agent(
        model=FunctionModel(scripted), db_path=db_path, run_id=run_id)
    agent.run_sync("Resolve the open conflicts.", deps=deps)

    assert deps.writes_performed == 1
    conn = sqlite3.connect(str(db_path))
    try:
        fact = conn.execute(
            "SELECT value, value_status, children_status FROM run_concept_facts "
            "WHERE run_id=? AND concept_uuid=?", (run_id, computed)).fetchone()
        assert fact == (1000.0, "user_override", "aggregate_only")
        status = conn.execute(
            "SELECT status FROM run_concept_conflicts WHERE run_id=? AND "
            "concept_uuid=?", (run_id, computed)).fetchone()[0]
        assert status == "resolved"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_revise_leaf_clears_parent_partial_state(tmp_path):
    """A revise_leaf that fixes a child so the parent reconciles must close
    the PARENT's partial_state conflict via the pass's post-cascade
    (peer-review finding 2)."""
    from db.schema import init_db
    from concept_model.parser import parse_template
    from concept_model.importer import import_template
    from concept_model.facts_api import write_fact, FactWrite
    from server import _run_canonical_correction_pass
    from correction.canonical_agent import _load_open_conflicts

    db_path = tmp_path / "xbrl.db"
    init_db(db_path)
    tree = parse_template(str(CO_SOFP))
    jp = tmp_path / "t.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    import_template(db_path, jp)

    conn = sqlite3.connect(str(db_path))
    run_id = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES (?,?,?,?)",
        ("2026-05-25T00:00:00Z", "x.pdf", "running", "2026-05-25T00:00:00Z"),
    ).lastrowid
    # A COMPUTED parent whose first child is a LEAF (so revise_leaf targets a
    # data row, not a formula). Order children LEAF-first for the fix.
    parent = conn.execute(
        "SELECT e.parent_uuid FROM concept_edges e "
        "JOIN concept_nodes p ON p.concept_uuid = e.parent_uuid "
        "JOIN concept_nodes c ON c.concept_uuid = e.child_uuid "
        "WHERE p.kind='COMPUTED' AND c.kind='LEAF' LIMIT 1").fetchone()[0]
    rows = conn.execute(
        "SELECT e.child_uuid, n.kind FROM concept_edges e "
        "JOIN concept_nodes n ON n.concept_uuid=e.child_uuid "
        "WHERE e.parent_uuid=? ORDER BY (n.kind='LEAF') DESC", (parent,)).fetchall()
    children = [r[0] for r in rows]
    assert rows[0][1] == "LEAF"

    def _raw(cu, val):
        # Seed facts directly — the facts API rejects observed values on a
        # COMPUTED parent, but a real partial_state arises exactly this way
        # (cascade-written observed parent + later-diverging children).
        conn.execute(
            "INSERT OR REPLACE INTO run_concept_facts(run_id, concept_uuid, "
            "period, entity_scope, value, value_status, children_status, "
            "source, updated_at) VALUES (?,?,'CY','Company',?,?,?,?,?)",
            (run_id, cu, val, "observed",
             "itemised" if cu == parent else None, "seed", "t"))
    # Observed parent = 100; one child 40, rest 0 → residual 60 → partial_state.
    _raw(parent, 100.0)
    _raw(children[0], 40.0)
    for c in children[1:]:
        _raw(c, 0.0)
    conn.commit()
    conn.close()

    from concept_model.cascade import recompute_after_turn
    recompute_after_turn(db_path, run_id)

    # Agent fixes the leaf to 100 so children now sum to 100 (reconciled).
    def scripted(messages, info: AgentInfo) -> ModelResponse:
        for m in messages:
            for part in getattr(m, "parts", []):
                if part.part_kind == "tool-return":
                    return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="revise_leaf",
            args={"concept_uuid": children[0], "value": 100.0,
                  "source": "Note 9"})])

    conflicts = _load_open_conflicts(db_path, run_id)
    assert any(c["kind"] == "partial_state" for c in conflicts)
    queue: asyncio.Queue = asyncio.Queue()
    outcome = await _run_canonical_correction_pass(
        conflicts=conflicts, model=FunctionModel(scripted),
        filing_level="company", event_queue=queue, db_path=db_path, run_id=run_id)
    assert outcome["writes_performed"] == 1

    conn = sqlite3.connect(str(db_path))
    try:
        status = conn.execute(
            "SELECT status FROM run_concept_conflicts WHERE run_id=? AND "
            "concept_uuid=? AND kind='partial_state'", (run_id, parent)).fetchone()[0]
        assert status == "resolved"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_server_canonical_correction_pass_resolves_conflict(seeded):
    """The server-side _run_canonical_correction_pass drives the agent over
    open conflicts and reports writes; the conflict ends resolved."""
    from server import _run_canonical_correction_pass
    from correction.canonical_agent import _load_open_conflicts

    db_path, run_id, computed = seeded

    def scripted(messages, info: AgentInfo) -> ModelResponse:
        for m in messages:
            for part in getattr(m, "parts", []):
                if part.part_kind == "tool-return":
                    return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="mark_aggregate_only",
            args={"concept_uuid": computed, "value": 2000.0,
                  "source": "Note 7"},
        )])

    conflicts = _load_open_conflicts(db_path, run_id)
    queue: asyncio.Queue = asyncio.Queue()
    outcome = await _run_canonical_correction_pass(
        conflicts=conflicts, model=FunctionModel(scripted),
        filing_level="company", event_queue=queue, db_path=db_path,
        run_id=run_id,
    )
    assert outcome["invoked"] is True
    assert outcome["writes_performed"] == 1
    assert outcome["max_turns"] == 10  # company + 1 conflict → 8 + 2

    conn = sqlite3.connect(str(db_path))
    try:
        status = conn.execute(
            "SELECT status FROM run_concept_conflicts WHERE run_id=? AND "
            "concept_uuid=?", (run_id, computed)).fetchone()[0]
        assert status == "resolved"
    finally:
        conn.close()
