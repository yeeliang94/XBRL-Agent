"""Phase 3 Step 8 — the reviewer agent factory.

Modeled on ``tests/test_canonical_correction_agent.py``: drive the agent
with FunctionModel / TestModel (no live LLM) to assert it constructs with
the right tool roster, a scripted run stages one grounded fix + one flag,
and the dynamic turn cap stays below pydantic-ai's silent 50 (gotcha #18).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from db.schema import init_db
from concept_model.facts_api import FactWrite, write_fact


_TEMPLATE = "mfrs-company-sofp-test-v1"
PARENT = "00000000-0000-0000-0000-0000000000aa"
LEAF1 = "00000000-0000-0000-0000-0000000000b1"


@pytest.fixture
def seeded(tmp_path: Path):
    db = tmp_path / "rev.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    run_id = int(conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES (?,?,?,?)",
        ("2026-05-29T00:00:00Z", "x.pdf", "running", "2026-05-29T00:00:00Z"),
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
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) VALUES "
        "(?, ?, 'LEAF', 'Cash', 'SOFP', 5, 'B')",
        (LEAF1, _TEMPLATE),
    )
    conn.execute(
        "INSERT INTO concept_edges(parent_uuid, child_uuid, coefficient) "
        "VALUES (?, ?, 1.0)", (PARENT, LEAF1),
    )
    conn.commit()
    conn.close()
    write_fact(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company",
        value=100.0, value_status="observed", source="extraction",
        actor="agent",
    ))
    return db, run_id


def _tool_names(agent) -> set[str]:
    names: set[str] = set()
    for ts in agent.toolsets:
        tools = getattr(ts, "tools", {})
        if isinstance(tools, dict):
            names.update(tools.keys())
    return names


def test_factory_registers_read_and_write_tools(seeded):
    from correction.reviewer_agent import create_reviewer_agent

    db, run_id = seeded
    agent, _deps = create_reviewer_agent(
        model=TestModel(call_tools=[]), db_path=db, run_id=run_id)
    names = _tool_names(agent)
    # Read tools.
    assert {"calculator", "lookup_definitions", "read_facts",
            "trace_cascade_source", "view_pdf_pages"} <= names
    # Write tools.
    assert {"apply_fixes", "mark_not_disclosed", "raise_flag"} <= names


def test_prompt_carries_reviewer_doctrine(seeded):
    from correction.reviewer_agent import render_reviewer_prompt

    db, run_id = seeded
    prompt = render_reviewer_prompt(
        db_path=db, run_id=run_id,
        failed_checks=[{
            "name": "sofp_assets_eq_eqliab", "expected": 150, "actual": 100,
            "diff": 50, "message": "assets != equity+liabilities",
            "target_sheet": "SOFP", "target_row": 10,
        }],
        conflicts=[],
    )
    lower = prompt.lower()
    assert "root cause" in lower
    assert "never plug" in lower or "no-plug" in lower or "plug a residual" in lower
    assert "stuck" in lower and "disputes_prior" in lower
    # The review packet carries the failing check.
    assert "sofp_assets_eq_eqliab" in lower


def test_packet_renders_precomputed_trace_under_check():
    """Phase 4: a per-check pre-computed trace is rendered, indented, under the
    check it belongs to — a pure-function pin that doesn't need a DB."""
    from correction.reviewer_agent import _format_review_packet

    packet = _format_review_packet(
        [{
            "name": "sofp_assets_eq_eqliab", "expected": 150, "actual": 100,
            "diff": 50, "message": "assets != equity+liabilities",
            "target_sheet": "SOFP", "target_row": 10,
        }],
        [], None,
        check_traces=["Total assets (COMPUTED, SOFP row 10)\n  children..."],
    )
    assert "cascade trace (pre-computed" in packet
    # The trace text is indented beneath the check.
    assert "      Total assets (COMPUTED, SOFP row 10)" in packet


def test_prompt_inlines_cascade_trace_for_failing_target(seeded):
    """Phase 4: render_reviewer_prompt resolves the failing check's target down
    to its children and inlines the trace, so the reviewer doesn't spend turns
    rediscovering it via trace_cascade_source."""
    from correction.reviewer_agent import render_reviewer_prompt

    db, run_id = seeded
    prompt = render_reviewer_prompt(
        db_path=db, run_id=run_id,
        failed_checks=[{
            "name": "sofp_assets_eq_eqliab", "expected": 150, "actual": 100,
            "diff": 50, "message": "assets != equity+liabilities",
            "target_sheet": "SOFP", "target_row": 10,
        }],
        conflicts=[],
    )
    assert "cascade trace (pre-computed" in prompt
    # The seeded fixture's Total assets (row 10) has one child, Cash (row 5).
    assert "Total assets" in prompt
    assert "Cash" in prompt


def test_guidance_is_rendered_into_prompt(seeded):
    from correction.reviewer_agent import render_reviewer_prompt

    db, run_id = seeded
    prompt = render_reviewer_prompt(
        db_path=db, run_id=run_id, failed_checks=[], conflicts=[],
        guidance="The PPE note is on page 44, not 42.",
    )
    assert "page 44" in prompt
    assert "human guidance" in prompt.lower()


def test_scripted_run_stages_grounded_fix_and_flag(seeded):
    """A scripted two-step run: apply one grounded fix, then raise one flag."""
    from correction.reviewer_agent import create_reviewer_agent

    db, run_id = seeded
    state = {"step": 0}

    def scripted(messages, info: AgentInfo) -> ModelResponse:
        # Drive deterministically by step, ignoring tool returns.
        if state["step"] == 0:
            state["step"] = 1
            return ModelResponse(parts=[ToolCallPart(
                tool_name="apply_fixes",
                args={"fixes": [{"concept_uuid": LEAF1, "value": 120.0,
                                 "reason": "misread 100 for 120",
                                 "evidence": "page 12: Cash 120"}]},
            )])
        if state["step"] == 1:
            state["step"] = 2
            return ModelResponse(parts=[ToolCallPart(
                tool_name="raise_flag",
                args={"kind": "disputes_prior",
                      "reason": "extraction read 100 but PDF shows 120",
                      "concept_uuid": LEAF1,
                      "applied_fix": "revised Cash 100 -> 120"},
            )])
        return ModelResponse(parts=[TextPart("done")])

    agent, deps = create_reviewer_agent(
        model=FunctionModel(scripted), db_path=db, run_id=run_id)
    agent.run_sync("Review the failing checks.", deps=deps)

    assert deps.writes_performed == 1
    assert deps.flags_raised == 1
    conn = sqlite3.connect(str(db))
    try:
        val = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=?",
            (run_id, LEAF1),
        ).fetchone()[0]
        flag = conn.execute(
            "SELECT category, status FROM reviewer_flags WHERE run_id=?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert val == 120.0
    assert flag == ("disputes_prior", "open")


def test_ungrounded_fix_in_run_is_rejected_not_applied(seeded):
    from correction.reviewer_agent import create_reviewer_agent

    db, run_id = seeded
    captured: list[str] = []
    state = {"done": False}

    def scripted(messages, info: AgentInfo) -> ModelResponse:
        for m in messages:
            for part in getattr(m, "parts", []):
                if part.part_kind == "tool-return":
                    captured.append(str(part.content))
                    state["done"] = True
        if state["done"]:
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="apply_fixes",
            args={"fixes": [{"concept_uuid": LEAF1, "value": 999.0,
                             "reason": "guess", "evidence": ""}]},
        )])

    agent, deps = create_reviewer_agent(
        model=FunctionModel(scripted), db_path=db, run_id=run_id)
    agent.run_sync("Review.", deps=deps)

    assert deps.writes_performed == 0
    # A single-item batch whose only fix is ungrounded reports it honestly:
    # the summary is a partial with a per-item rejection, nothing applied.
    assert captured and captured[0].startswith("partial:")
    assert "rejected" in captured[0] and "0 applied, 1 rejected" in captured[0]
    # The original value is untouched.
    conn = sqlite3.connect(str(db))
    try:
        val = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=?",
            (run_id, LEAF1),
        ).fetchone()[0]
    finally:
        conn.close()
    assert val == 100.0


@pytest.mark.parametrize("is_group, n_items, expected", [
    (False, 0, 12),    # base, clamped at min (Phase 3: 10→12)
    (False, 1, 14),    # 12 + 2
    (False, 5, 22),    # 12 + 10
    (True, 5, 26),     # 12 + 4 + 10
    (False, 20, 36),   # clamped at max (Phase 3: 30→36)
    (True, 20, 36),    # clamped at max
])
def test_turn_cap_formula(is_group, n_items, expected):
    from correction.reviewer_agent import compute_reviewer_turn_cap
    assert compute_reviewer_turn_cap(
        filing_level="group" if is_group else "company", n_items=n_items,
    ) == expected


def test_turn_cap_below_pydantic_50(seeded):
    """Worst-case dynamic cap stays below MAX_AGENT_ITERATIONS, which is
    below pydantic-ai's silent 50 (gotcha #18)."""
    from agent_tracing import MAX_AGENT_ITERATIONS
    from correction.reviewer_agent import compute_reviewer_turn_cap
    assert MAX_AGENT_ITERATIONS < 50
    worst = compute_reviewer_turn_cap(filing_level="group", n_items=999)
    assert worst < MAX_AGENT_ITERATIONS


@pytest.mark.parametrize("level, mode, expected", [
    ("company", "light", 6),
    ("group", "light", 8),
    ("company", "full", 12),   # reuses reviewer base cap (n_items=0)
    ("group", "full", 16),     # base 12 + group 4
])
def test_spot_check_turn_cap(level, mode, expected):
    """Issue 1: the clean-run spot-check cap. Light is a tight sanity pass;
    full reuses the holistic reviewer budget."""
    from correction.reviewer_agent import compute_spot_check_turn_cap
    assert compute_spot_check_turn_cap(filing_level=level, mode=mode) == expected


def test_spot_check_turn_cap_below_pydantic_50():
    """Both spot-check depths stay under MAX_AGENT_ITERATIONS (gotcha #18)."""
    from agent_tracing import MAX_AGENT_ITERATIONS
    from correction.reviewer_agent import compute_spot_check_turn_cap
    for level in ("company", "group"):
        for mode in ("light", "full"):
            assert compute_spot_check_turn_cap(
                filing_level=level, mode=mode
            ) < MAX_AGENT_ITERATIONS


def test_spot_check_prompt_swaps_body_and_packet(seeded):
    """Issue 1: spot_check_mode='light' swaps to the tight spot_check.md body;
    'full' keeps the reviewer.md body; both replace the failing-check packet
    with a spot-check packet (no failing checks/conflicts to inline). The
    normal path (no spot_check_mode) is unchanged."""
    from correction.reviewer_agent import render_reviewer_prompt
    db_path, run_id = seeded
    light = render_reviewer_prompt(
        db_path=db_path, run_id=run_id, filing_level="company",
        filing_standard="mfrs", spot_check_mode="light",
    )
    full = render_reviewer_prompt(
        db_path=db_path, run_id=run_id, filing_level="company",
        filing_standard="mfrs", spot_check_mode="full",
    )
    normal = render_reviewer_prompt(
        db_path=db_path, run_id=run_id, filing_level="company",
        filing_standard="mfrs",
    )
    assert "fast spot-check" in light          # spot_check.md body
    assert "fast spot-check" not in full       # reviewer.md body retained
    assert "SPOT-CHECK PACKET" in light and "SPOT-CHECK PACKET" in full
    assert "SPOT-CHECK PACKET" not in normal   # failing-check packet path intact
