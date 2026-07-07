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
        tool_name="apply_fixes",
        args={"fixes": [{"concept_uuid": LEAF1, "value": 120.0,
                         "reason": "extraction misread 100; PDF shows 120",
                         "evidence": "page 12: Cash 120"}]})])


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
    # Item 15: every reviewer outcome answers "how close to the cap were we".
    assert outcome["elapsed_seconds"] >= 0

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
async def test_reviewer_pass_saves_conversation_trace(tmp_path):
    """Phase 4 (holistic audit) — the reviewer's transcript is persisted to
    {output_dir}/CORRECTION_conversation_trace.json so its judgement is
    auditable via the existing /agents/CORRECTION/trace route (gotcha #6)."""
    import json
    from server import _run_reviewer_pass

    db, run_id = _seed(tmp_path)
    out_dir = tmp_path / "run_out"
    out_dir.mkdir()
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE runs SET output_dir=? WHERE id=?", (str(out_dir), run_id))
    conn.commit()
    conn.close()

    failed = [CrossCheckResult(
        name="sofp_assets_balance", status="failed", expected=170.0,
        actual=150.0, diff=20.0, message="off by 20", target_sheet="SOFP",
        target_row=10)]
    await _run_reviewer_pass(
        failed_checks=failed, conflicts=[], model=FunctionModel(_fix_cash_scripted),
        filing_level="company", event_queue=asyncio.Queue(), db_path=db,
        run_id=run_id)

    trace = out_dir / "CORRECTION_conversation_trace.json"
    assert trace.exists(), "reviewer trace should be saved under output_dir"
    payload = json.loads(trace.read_text(encoding="utf-8"))
    # The trace carries the conversation (same shape as extraction traces).
    assert "messages" in payload and isinstance(payload["messages"], list)


@pytest.mark.asyncio
async def test_reviewer_loop_spec_does_not_bound_inner_streams(tmp_path, monkeypatch):
    """Code-review pin (2026-06-13): the reviewer's apply_fix does workbook
    IO — a legitimately long tool call the 180s per-turn timeout must NOT
    cancel mid-execution. The pass's AgentLoopSpec must keep the
    pre-migration semantics (bound_inner_streams=False, same opt-out as
    notes/coordinator.py's notes_spec)."""
    import agent_runner
    from server import _run_reviewer_pass

    db, run_id = _seed(tmp_path)
    captured: dict = {}
    real_loop = agent_runner.run_agent_loop

    async def _spy(agent_run, deps, spec, emit, turn_records):
        captured["spec"] = spec
        return await real_loop(agent_run, deps, spec, emit, turn_records)

    monkeypatch.setattr(agent_runner, "run_agent_loop", _spy)
    failed = [CrossCheckResult(
        name="sofp_assets_balance", status="failed", expected=170.0,
        actual=150.0, diff=20.0, message="off by 20", target_sheet="SOFP",
        target_row=10)]
    await _run_reviewer_pass(
        failed_checks=failed, conflicts=[], model=FunctionModel(_fix_cash_scripted),
        filing_level="company", event_queue=asyncio.Queue(), db_path=db,
        run_id=run_id)

    assert "spec" in captured, "reviewer pass should reach run_agent_loop"
    assert captured["spec"].bound_inner_streams is False


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


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["light", "full"])
async def test_spot_check_runs_on_clean_run(tmp_path, mode):
    """Issue 1: with NO failing checks and NO conflicts, a spot_check pass is
    still invoked (it does NOT short-circuit like the failure-driven pass) and
    snapshots-then-applies the same grounded fix, so the result stays
    revertible. Pins both light and full depths."""
    from server import _run_reviewer_pass

    db, run_id = _seed(tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    outcome = await _run_reviewer_pass(
        failed_checks=[], conflicts=[], model=FunctionModel(_fix_cash_scripted),
        filing_level="company", event_queue=queue, db_path=db, run_id=run_id,
        spot_check=mode)

    assert outcome["invoked"] is True, "spot-check must run on a clean run"
    # The outcome is tagged as a spot-check so the run-status logic can treat
    # its exhaustion as advisory rather than `correction_exhausted`.
    assert outcome["spot_check"] == mode
    assert outcome["writes_performed"] == 1
    conn = sqlite3.connect(str(db))
    try:
        # Snapshot taken before the write → revert-to-original still works.
        snap = conn.execute(
            "SELECT value FROM run_fact_snapshots WHERE run_id=? AND concept_uuid=?",
            (run_id, LEAF1)).fetchone()
        assert snap is not None and snap[0] == 100.0
        live = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=?",
            (run_id, LEAF1)).fetchone()[0]
        assert live == 120.0
    finally:
        conn.close()


def _always_fix(messages, info: AgentInfo) -> ModelResponse:
    """Never finishes — always calls apply_fix, to drive the turn cap."""
    return ModelResponse(parts=[ToolCallPart(
        tool_name="apply_fixes",
        args={"fixes": [{"concept_uuid": LEAF1, "value": 120.0,
                         "reason": "fix", "evidence": "page 12: Cash 120"}]})])


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
    # Item 15: elapsed is stamped on the error path too.
    assert outcome["elapsed_seconds"] >= 0
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
    # Item 15: elapsed is stamped on the exhaustion path too.
    assert outcome["elapsed_seconds"] >= 0
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
async def test_exhausted_spot_check_is_tagged_not_a_hard_failure(tmp_path, monkeypatch):
    """Peer-review HIGH (2026-06-21): a spot-check that merely runs out of its
    tight turn budget carries `exhausted=True` AND the `spot_check` tag, while
    its error stays the soft `reviewer_exhausted`. The run-status logic relies
    on exactly this shape to NOT flag a clean run as `correction_exhausted`
    (spot_check excluded) and to NOT treat reviewer_exhausted as a hard
    `reviewer_failed`."""
    import correction.reviewer_agent as ra
    from server import _run_reviewer_pass

    db, run_id = _seed(tmp_path)
    # Spot-check uses its OWN cap function — force exhaustion after one write.
    monkeypatch.setattr(ra, "compute_spot_check_turn_cap", lambda **k: 1)

    outcome = await _run_reviewer_pass(
        failed_checks=[], conflicts=[], model=FunctionModel(_always_fix),
        filing_level="company", event_queue=asyncio.Queue(), db_path=db,
        run_id=run_id, spot_check="light")

    assert outcome["exhausted"] is True
    assert outcome["error"] == "reviewer_exhausted"  # soft, not a hard failure
    assert outcome["spot_check"] == "light"           # excluded from correction_exhausted


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


@pytest.mark.asyncio
async def test_reviewer_pass_handles_failed_check_with_comparands(tmp_path):
    """Regression (peer-review HIGH): `_run_reviewer_pass` serialises each
    failed check's `comparands` via `dataclasses.asdict`. server.py had no
    module-level `import dataclasses`, so a real failed check — which now ALWAYS
    carries comparands from the 6 numeric checks — raised NameError before the
    pass emitted a single event. The existing tests passed only because they
    built checks with an EMPTY comparands list (the comprehension short-
    circuits). This pins a NON-empty list so the import can't silently regress.
    """
    from server import _run_reviewer_pass
    from cross_checks.framework import Comparand

    db, run_id = _seed(tmp_path)
    failed = [CrossCheckResult(
        name="sofp_assets_balance", status="failed", expected=170.0,
        actual=150.0, diff=20.0, message="assets total off by 20",
        target_sheet="SOFP", target_row=10,
        comparands=[
            Comparand(label="Total assets", sheet="SOFP", value=150.0,
                      role="lhs", statement="SOFP", row=10),
            Comparand(label="Total equity and liabilities", sheet="SOFP",
                      value=170.0, role="rhs", statement="SOFP", row=20),
        ])]

    # Pre-fix this raised NameError inside failed_payload construction.
    outcome = await _run_reviewer_pass(
        failed_checks=failed, conflicts=[],
        model=FunctionModel(_fix_cash_scripted),
        filing_level="company", event_queue=asyncio.Queue(), db_path=db,
        run_id=run_id)

    assert outcome["invoked"] is True
    assert outcome["writes_performed"] == 1
    assert not outcome.get("error")
