"""Plan agent-efficiency Phase 2 — read-only reviewer investigation bundle.

Pins the contract from the plan's Step 2.2/2.3/2.5:

* Flag OFF (default) ⇒ the tool is NOT registered and the system prompt
  carries no batching paragraph — factory identity with today.
* Every operation routes through the SAME helper as the individual tool
  (parity assertion), scoped by the run's family prefix from deps.
* Per-item isolation: a bad item errors under its own id, siblings survive.
* Bounds: item cap, per-item truncation footer, budget-exhausted skip.
* Judgement boundaries: no PDF-viewing / write / flag / verify kind exists.
* No mutation: facts are byte-identical after a bundle run.
"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_db
from concept_model.facts_api import FactWrite, write_fact
from correction.reviewer_agent import (
    BUNDLE_KINDS,
    InvestigateItem,
    MAX_BUNDLE_ITEMS,
    ReviewerDeps,
    read_concept_facts_text,
    run_investigation_bundle,
)


_TEMPLATE = "mfrs-company-sofp-test-v1"
PARENT = "00000000-0000-0000-0000-0000000000aa"
LEAF1 = "00000000-0000-0000-0000-0000000000b1"


@pytest.fixture()
def seeded(tmp_path):
    db = tmp_path / "bundle.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    run_id = int(conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status) "
        "VALUES ('2026-07-23T00:00:00Z','x.pdf','running')").lastrowid)
    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path, shape) "
        "VALUES (?, 'x.xlsx', 'linear')", (_TEMPLATE,))
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) VALUES "
        "(?, ?, 'COMPUTED', 'Total assets', 'SOFP', 10, 'B')",
        (PARENT, _TEMPLATE))
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) VALUES "
        "(?, ?, 'LEAF', 'Cash', 'SOFP', 5, 'B')", (LEAF1, _TEMPLATE))
    conn.execute(
        "INSERT INTO concept_edges(parent_uuid, child_uuid, coefficient) "
        "VALUES (?, ?, 1.0)", (PARENT, LEAF1))
    conn.commit()
    conn.close()
    write_fact(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company", value=100.0,
        value_status="observed", source="extraction", actor="agent"))
    return db, run_id


def _deps(db, run_id) -> ReviewerDeps:
    return ReviewerDeps(db_path=str(db), run_id=run_id)


def test_mixed_batch_isolates_errors_and_keeps_siblings(seeded):
    db, run_id = seeded
    out = run_investigation_bundle(_deps(db, run_id), [
        InvestigateItem(kind="read_fact", id="a", concept_uuid=LEAF1),
        InvestigateItem(kind="read_fact", id="bad"),          # missing uuid
        InvestigateItem(kind="calculate", id="c",
                        expressions=["100+1", "2*3"]),
        InvestigateItem(kind="nonsense", id="d"),
    ])
    assert "[a] read_fact" in out and "Cash" in out
    assert "[bad] read_fact\nerror: read_fact needs concept_uuid" in out
    assert "[c] calculate" in out and "101" in out
    assert "[d] nonsense\nerror: unknown kind" in out


def test_parity_with_individual_tool_helpers(seeded):
    """Bundle output for read_fact == the read_facts tool's exact text."""
    db, run_id = seeded
    direct = read_concept_facts_text(db, run_id, LEAF1)
    out = run_investigation_bundle(_deps(db, run_id), [
        InvestigateItem(kind="read_fact", id="x", concept_uuid=LEAF1)])
    assert out == f"[x] read_fact\n{direct}"


def test_family_scoping_comes_from_deps_not_items(seeded):
    """An MPERS-scoped deps sees NO facts from the MFRS template family —
    the item cannot smuggle a family in."""
    db, run_id = seeded
    mfrs = run_investigation_bundle(
        ReviewerDeps(db_path=str(db), run_id=run_id,
                     filing_standard="mfrs", filing_level="company"),
        [InvestigateItem(kind="list_sheet", id="s")])
    mpers = run_investigation_bundle(
        ReviewerDeps(db_path=str(db), run_id=run_id,
                     filing_standard="mpers", filing_level="company"),
        [InvestigateItem(kind="list_sheet", id="s")])
    assert "Cash" in mfrs
    assert "Cash" not in mpers


def test_bounds_cap_and_truncation(seeded):
    db, run_id = seeded
    too_many = [InvestigateItem(kind="calculate", expressions=["1+1"])
                for _ in range(MAX_BUNDLE_ITEMS + 1)]
    out = run_investigation_bundle(_deps(db, run_id), too_many)
    assert out.startswith("rejected:") and str(MAX_BUNDLE_ITEMS) in out

    assert run_investigation_bundle(
        _deps(db, run_id), []).startswith("rejected:")

    big = run_investigation_bundle(_deps(db, run_id), [
        InvestigateItem(kind="calculate", id="big",
                        expressions=[f"{i}+{i}" for i in range(2000)])])
    assert "[truncated at" in big


def test_no_mutation(seeded):
    db, run_id = seeded

    def _state(conn):
        return {
            "facts": conn.execute(
                "SELECT concept_uuid, period, entity_scope, value FROM "
                "run_concept_facts ORDER BY 1,2,3").fetchall(),
            "flags": conn.execute(
                "SELECT count(*) FROM reviewer_flags").fetchone()[0],
            "snapshots": conn.execute(
                "SELECT count(*) FROM run_fact_snapshots").fetchone()[0],
        }

    conn = sqlite3.connect(str(db))
    before = _state(conn)
    conn.close()
    run_investigation_bundle(_deps(db, run_id), [
        InvestigateItem(kind="list_sheet"),
        InvestigateItem(kind="trace_cascade", concept_uuid=PARENT),
        InvestigateItem(kind="find_candidates", value=100.0),
    ])
    conn = sqlite3.connect(str(db))
    after = _state(conn)
    conn.close()
    assert before == after


def test_group_family_scoping(seeded):
    """A Group-level deps scopes to the mfrs-group- family — the Company
    fact is invisible (deps, never the item, picks the family)."""
    db, run_id = seeded
    out = run_investigation_bundle(
        ReviewerDeps(db_path=str(db), run_id=run_id,
                     filing_standard="mfrs", filing_level="group"),
        [InvestigateItem(kind="list_sheet", id="s")])
    assert "Cash" not in out


def test_budget_exhaustion_skips_with_footer(seeded):
    """Once the 20k output budget is spent, later items are SKIPPED with an
    explicit re-send footer — never silently dropped."""
    db, run_id = seeded
    big_items = [
        InvestigateItem(kind="calculate", id=f"b{i}",
                        expressions=[f"{j}+{j}" for j in range(2000)])
        for i in range(7)
    ]
    out = run_investigation_bundle(_deps(db, run_id), big_items)
    assert "skipped: bundle output budget" in out
    assert "re-send the remaining items" in out


def test_judgement_boundaries_have_no_bundle_kind():
    for forbidden in ("view_pdf_pages", "apply_fixes", "mark_not_disclosed",
                      "raise_flag", "verify_fixes"):
        assert forbidden not in BUNDLE_KINDS


def _tool_names(agent) -> set[str]:
    names: set[str] = set()
    for ts in agent.toolsets:
        tools = getattr(ts, "tools", {})
        if isinstance(tools, dict):
            names.update(tools.keys())
    return names


def _build_agent(seeded):
    from correction.reviewer_agent import create_reviewer_agent
    db, run_id = seeded
    agent, _deps_out = create_reviewer_agent(
        model="test", db_path=db, run_id=run_id)
    return agent


def test_flag_off_tool_absent_and_prompt_clean(seeded, monkeypatch):
    monkeypatch.delenv("XBRL_REVIEWER_INVESTIGATION_BUNDLE", raising=False)
    agent = _build_agent(seeded)
    assert "investigate_review_items" not in _tool_names(agent)
    prompt = agent._system_prompts[0] if agent._system_prompts else ""
    assert "investigate_review_items" not in prompt


def test_flag_on_registers_tool_and_prompt(seeded, monkeypatch):
    monkeypatch.setenv("XBRL_REVIEWER_INVESTIGATION_BUNDLE", "1")
    agent = _build_agent(seeded)
    assert "investigate_review_items" in _tool_names(agent)
    prompt = agent._system_prompts[0] if agent._system_prompts else ""
    assert "Batching independent reads" in prompt
