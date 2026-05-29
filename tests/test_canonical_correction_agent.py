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
    assert {"calculator", "get_conflict_context", "get_child_facts", "view_pdf_pages"} <= names


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



# NOTE (docs/Archive/PLAN-reviewer-agent.md, Step 10): the two server-integration
# tests that drove the now-deleted ``server._run_canonical_correction_pass``
# were removed when the reviewer pass replaced the autonomous canonical
# correction pass. The canonical-agent *factory* tests above still stand
# (correction/canonical_agent.py remains as a unit-tested module); the
# server-side pipeline coverage now lives in tests/test_reviewer_pipeline.py.
