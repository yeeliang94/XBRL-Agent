"""Phase 4 Step 9 — server-side reviewer pass (``_run_reviewer_pass``).

Drives the reviewer pass without a live LLM (FunctionModel). Pins the
Step-9 contract: the pass snapshots the ORIGINAL facts FIRST, runs the
reviewer over failing cross-checks + open conflicts, applies a grounded
fix, recomputes, and reports ``writes_performed`` so the caller re-exports.
The snapshot is what makes the run revertible end-to-end.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from db.schema import init_db
from concept_model.facts_api import FactWrite, write_fact
from cross_checks.framework import CrossCheckResult


_TEMPLATE = "mfrs-company-sofp-test-v1"
PARENT = "00000000-0000-0000-0000-0000000000aa"
LEAF1 = "00000000-0000-0000-0000-0000000000b1"
LEAF2 = "00000000-0000-0000-0000-0000000000b2"


def _seed(tmp_path):
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
    # Original extraction: Cash misread as 100 (should be 120), Receivables 50.
    write_fact(db, run_id, FactWrite(
        concept_uuid=LEAF1, period="CY", entity_scope="Company", value=100.0,
        value_status="observed", source="extraction", actor="agent"))
    write_fact(db, run_id, FactWrite(
        concept_uuid=LEAF2, period="CY", entity_scope="Company", value=50.0,
        value_status="observed", source="extraction", actor="agent"))
    from concept_model.cascade import recompute_after_turn
    recompute_after_turn(db, run_id)
    return db, run_id


def _fix_cash_scripted(messages, info: AgentInfo) -> ModelResponse:
    """One grounded apply_fix on LEAF1, then finish."""
    for m in messages:
        for part in getattr(m, "parts", []):
            if part.part_kind == "tool-return":
                return ModelResponse(parts=[TextPart("done")])
    return ModelResponse(parts=[ToolCallPart(
        tool_name="apply_fix",
        args={"concept_uuid": LEAF1, "value": 120.0,
              "reason": "extraction misread 100; PDF shows 120",
              "evidence": "page 12: Cash 120"})])


@pytest.mark.asyncio
async def test_reviewer_pass_snapshots_then_applies_grounded_fix(tmp_path):
    from server import _run_reviewer_pass

    db, run_id = _seed(tmp_path)
    failed = [CrossCheckResult(
        name="sofp_assets_balance", status="failed", expected=170.0,
        actual=150.0, diff=20.0, message="assets total off by 20",
        target_sheet="SOFP", target_row=10)]
    queue: asyncio.Queue = asyncio.Queue()

    outcome = await _run_reviewer_pass(
        failed_checks=failed, conflicts=[], model=FunctionModel(_fix_cash_scripted),
        filing_level="company", event_queue=queue, db_path=db, run_id=run_id)

    assert outcome["invoked"] is True
    assert outcome["writes_performed"] == 1

    conn = sqlite3.connect(str(db))
    try:
        # Snapshot was taken BEFORE the fix — it holds the ORIGINAL 100.
        snap = conn.execute(
            "SELECT value FROM run_fact_snapshots WHERE run_id=? AND concept_uuid=?",
            (run_id, LEAF1)).fetchone()
        assert snap is not None and snap[0] == 100.0
        # Live fact carries the reviewer's grounded fix.
        live = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=?",
            (run_id, LEAF1)).fetchone()[0]
        assert live == 120.0
        # Cascade re-ran: parent total reflects the fix (120 + 50).
        parent = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=?",
            (run_id, PARENT)).fetchone()[0]
        assert parent == 170.0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_reviewer_pass_noop_when_nothing_to_review(tmp_path):
    from server import _run_reviewer_pass

    db, run_id = _seed(tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    outcome = await _run_reviewer_pass(
        failed_checks=[], conflicts=[], model=FunctionModel(_fix_cash_scripted),
        filing_level="company", event_queue=queue, db_path=db, run_id=run_id)
    assert outcome["invoked"] is False
    assert outcome["writes_performed"] == 0
    # No snapshot taken when the reviewer never runs.
    conn = sqlite3.connect(str(db))
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM run_fact_snapshots WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert cnt == 0


def _always_fix(messages, info: AgentInfo) -> ModelResponse:
    """Never finishes — always calls apply_fix, to drive the turn cap."""
    return ModelResponse(parts=[ToolCallPart(
        tool_name="apply_fix",
        args={"concept_uuid": LEAF1, "value": 120.0,
              "reason": "fix", "evidence": "page 12: Cash 120"})])


@pytest.mark.asyncio
async def test_reviewer_refuses_run_with_zero_facts(tmp_path):
    """Reversibility guard (peer-review P1): a run with ZERO canonical facts
    has an empty (== indistinguishable-from-absent) snapshot, so the reviewer
    must refuse rather than make non-revertible writes."""
    from server import _run_reviewer_pass

    db = tmp_path / "rev.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    run_id = int(conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES ('2026Z','x.pdf','running','2026Z')").lastrowid)
    conn.execute("INSERT INTO concept_templates(template_id, source_path, shape) "
                 "VALUES (?, 'x.xlsx', 'linear')", (_TEMPLATE,))
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) VALUES "
        "(?, ?, 'LEAF', 'Cash', 'SOFP', 5, 'B')", (LEAF1, _TEMPLATE))
    conn.commit()
    conn.close()

    failed = [CrossCheckResult(
        name="sofp_assets_balance", status="failed", expected=170.0,
        actual=0.0, diff=170.0, message="off", target_sheet="SOFP",
        target_row=10)]
    # _always_fix would write if the agent ran — it must NOT.
    outcome = await _run_reviewer_pass(
        failed_checks=failed, conflicts=[], model=FunctionModel(_always_fix),
        filing_level="company", event_queue=asyncio.Queue(), db_path=db,
        run_id=run_id)

    assert outcome["error"] == "no_extracted_facts_to_review"
    assert outcome["writes_performed"] == 0
    conn = sqlite3.connect(str(db))
    try:
        snaps = conn.execute(
            "SELECT COUNT(*) FROM run_fact_snapshots WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        facts = conn.execute(
            "SELECT COUNT(*) FROM run_concept_facts WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    # Refused BEFORE snapshotting and BEFORE any write — nothing happened.
    assert snaps == 0
    assert facts == 0


@pytest.mark.asyncio
async def test_exhausted_reviewer_still_cascades(tmp_path, monkeypatch):
    """Peer-review P2: when the reviewer writes a leaf then hits the turn cap,
    the shared post-pass cascade must still run so parent totals aren't left
    stale for the re-export."""
    import correction.reviewer_agent as ra
    from server import _run_reviewer_pass

    db, run_id = _seed(tmp_path)
    # Force exhaustion after a single write: cap = 1 turn.
    monkeypatch.setattr(ra, "compute_reviewer_turn_cap", lambda **k: 1)

    failed = [CrossCheckResult(
        name="sofp_assets_balance", status="failed", expected=170.0,
        actual=150.0, diff=20.0, message="off by 20", target_sheet="SOFP",
        target_row=10)]
    outcome = await _run_reviewer_pass(
        failed_checks=failed, conflicts=[], model=FunctionModel(_always_fix),
        filing_level="company", event_queue=asyncio.Queue(), db_path=db,
        run_id=run_id)

    assert outcome["exhausted"] is True
    assert outcome["error"] == "reviewer_exhausted"
    assert outcome["writes_performed"] >= 1
    conn = sqlite3.connect(str(db))
    try:
        # Cascade ran on the way out: parent reflects the leaf fix (120 + 50),
        # not the stale pre-fix 150.
        parent = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=?",
            (run_id, PARENT)).fetchone()[0]
    finally:
        conn.close()
    assert parent == 170.0


@pytest.mark.asyncio
async def test_reviewer_pass_result_is_revertible(tmp_path):
    """End-to-end reversibility: after the reviewer pass, the diff shows the
    change and revert_to_original restores the original facts."""
    from server import _run_reviewer_pass
    from concept_model.versioning import compute_review_diff, revert_to_original

    db, run_id = _seed(tmp_path)
    failed = [CrossCheckResult(
        name="sofp_assets_balance", status="failed", expected=170.0,
        actual=150.0, diff=20.0, message="off by 20",
        target_sheet="SOFP", target_row=10)]
    queue: asyncio.Queue = asyncio.Queue()
    await _run_reviewer_pass(
        failed_checks=failed, conflicts=[], model=FunctionModel(_fix_cash_scripted),
        filing_level="company", event_queue=queue, db_path=db, run_id=run_id)

    diff = compute_review_diff(db, run_id)
    cash = next(d for d in diff if d["concept_uuid"] == LEAF1)
    assert cash["original"] == 100.0 and cash["current"] == 120.0
    assert cash["reason"] == "extraction misread 100; PDF shows 120"

    out = revert_to_original(db, run_id)
    assert out["reverted"] is True
    conn = sqlite3.connect(str(db))
    try:
        val = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=?",
            (run_id, LEAF1)).fetchone()[0]
    finally:
        conn.close()
    assert val == 100.0
