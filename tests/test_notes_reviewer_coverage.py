"""Notes-reviewer coverage verdict tools + checklist recompute
(docs/PLAN-notes-coverage-and-routing.md Phase 5).

Drives the real reviewer agent with a scripted FunctionModel (mirrors
tests/test_notes_reviewer_tools.py) and asserts:
  - resolve_coverage_note / verify_subnote require PDF grounding;
  - a confirmed_absent verdict resolves a suspected numbering gap;
  - an authored missing note flips to placed (reviewer-added) on recompute;
  - an unresolved missing row survives;
  - verify_subnote upgrades a not_verified sub-ref to verified / missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import notes.reviewer_agent as ra
import notes.detectors as det
from db import repository as repo
from db.schema import init_db
from notes.coverage_checklist import (
    STATUS_MISSING, STATUS_PLACED, STATUS_SUSPECTED_GAP,
    SUBNOTE_MISSING, SUBNOTE_NOT_VERIFIED, SUBNOTE_VERIFIED,
    VERDICT_CONFIRMED_ABSENT,
)

_S12 = "Notes-Listofnotes"
_PREFIX = "mfrs-company-"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


@pytest.fixture(autouse=True)
def _mock_pdf(monkeypatch):
    monkeypatch.setattr(ra, "count_pdf_pages", lambda _p: 30)
    monkeypatch.setattr(
        det, "render_pages_to_png_bytes",
        lambda pdf_path, start, end, dpi=200: [b"png"],
    )


def _scripted(steps: list[list]) -> FunctionModel:
    idx = {"i": 0}

    def fn(messages, info):
        i = idx["i"]
        idx["i"] += 1
        if i < len(steps):
            return ModelResponse(parts=steps[i])
        return ModelResponse(parts=[TextPart("done")])

    return FunctionModel(fn)


def _agent(db_path: Path, run_id: int, model):
    return ra.create_notes_reviewer_agent(
        run_id=run_id, db_path=str(db_path), pdf_path="/tmp/x.pdf",
        filing_level="company", filing_standard="mfrs",
        model=model, output_dir=str(db_path.parent),
    )


def _seed_run(db_path: Path) -> int:
    with repo.db_session(db_path) as conn:
        return repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")


def _seed_node(db_path: Path, row: int, kind: str, label: str) -> None:
    with repo.db_session(db_path) as conn:
        conn.execute(
            "INSERT INTO notes_nodes(node_uuid, template_id, sheet, row, label, kind) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"n{row}", f"{_PREFIX}notes-listofnotes-v1", _S12, row, label, kind),
        )


def _seed_inv(db_path, run_id, note_num, title="", subs=None):
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_inventory(
            conn, run_id=run_id, note_num=note_num, title=title,
            subnote_refs=subs, page_lo=19, page_hi=20,
        )


def _seed_prov(db_path, run_id, row, refs):
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_S12, row=row, label=f"Row {row}",
            html="<p>x</p>",
        )
        repo.upsert_notes_provenance(
            conn, run_id=run_id, sheet=_S12, row=row, row_label=f"Row {row}",
            source_note_refs=refs,
        )


def _row(checklist, note_num):
    return next(r for r in checklist.rows if r.note_num == note_num)


# --------------------------------------------------------------------------


def test_resolve_coverage_note_requires_grounding(db_path):
    run_id = _seed_run(db_path)
    _seed_inv(db_path, run_id, 4, "Investment properties")
    # No view_pdf_pages → source_pages not grounded.
    model = _scripted([
        [ToolCallPart(tool_name="resolve_coverage_note", args={
            "note_num": 4, "verdict": "not_applicable",
            "reason": "no such asset", "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert 4 not in deps.coverage_note_verdicts
    assert deps.fix_rejections.get("ungrounded") == 1


def test_resolve_bad_verdict_rejected(db_path):
    run_id = _seed_run(db_path)
    _seed_inv(db_path, run_id, 4)
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="resolve_coverage_note", args={
            "note_num": 4, "verdict": "placed", "reason": "x",
            "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert 4 not in deps.coverage_note_verdicts


def test_confirmed_absent_resolves_suspected_gap(db_path):
    run_id = _seed_run(db_path)
    # notes 12 + 14 present (placed) → suspected gap at 13.
    _seed_inv(db_path, run_id, 12)
    _seed_inv(db_path, run_id, 14)
    _seed_prov(db_path, run_id, 30, ["12"])
    _seed_prov(db_path, run_id, 32, ["14"])
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="resolve_coverage_note", args={
            "note_num": 13, "verdict": "confirmed_absent",
            "reason": "PDF numbering skips 13", "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert deps.coverage_note_verdicts[13]["verdict"] == VERDICT_CONFIRMED_ABSENT
    cl = ra.recompute_notes_findings(deps)["coverage_checklist"]
    gap = _row(cl, 13)
    assert gap.status == STATUS_SUSPECTED_GAP
    assert gap.reviewer_verdict == VERDICT_CONFIRMED_ABSENT
    assert gap.is_unresolved() is False
    assert cl.has_unresolved() is False


def test_author_flips_missing_to_placed_reviewer_added(db_path):
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "Disclosure of X")
    _seed_inv(db_path, run_id, 4, "Investment properties")
    # Draft: note 4 has no placement → missing.
    _, deps0, ctx0 = _agent(db_path, run_id, _scripted([]))
    assert _row(ctx0["coverage_checklist"], 4).status == STATUS_MISSING

    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cell", args={
            "sheet": _S12, "row": 50, "html": "<p>grounded prose</p>",
            "note_num": 4, "source_pages": [19], "evidence": "IP note"})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert 4 in deps.authored_note_nums
    cl = ra.recompute_notes_findings(deps)["coverage_checklist"]
    row = _row(cl, 4)
    assert row.status == STATUS_PLACED
    assert row.reviewer_added is True


def test_not_applicable_clears_coverage_gap_for_verify(db_path):
    """After resolving a missing note as not_applicable, the detector
    coverage_gaps must drop it so verify_findings doesn't re-report it open
    (Codex review P2)."""
    run_id = _seed_run(db_path)
    _seed_inv(db_path, run_id, 4, "Investment properties")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="resolve_coverage_note", args={
            "note_num": 4, "verdict": "not_applicable",
            "reason": "no such asset", "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    ctx = ra.recompute_notes_findings(deps)
    assert 4 not in ctx["coverage_gaps"]
    # And the recompute's finding_keys carries no coverage_gap for note 4.
    assert ("coverage_gap", 4) not in ra.finding_keys(ctx)


def test_unresolved_missing_row_survives(db_path):
    run_id = _seed_run(db_path)
    _seed_inv(db_path, run_id, 4, "Investment properties")
    agent, deps, _ = _agent(db_path, run_id, _scripted([]))
    agent.run_sync("go", deps=deps)  # reviewer does nothing
    cl = ra.recompute_notes_findings(deps)["coverage_checklist"]
    row = _row(cl, 4)
    assert row.status == STATUS_MISSING
    assert row.is_unresolved() is True
    assert cl.has_unresolved() is True


def test_verify_subnote_verified_and_missing(db_path):
    run_id = _seed_run(db_path)
    _seed_inv(db_path, run_id, 9, "Investment properties", subs=["(a)", "(b)"])
    # Coarse citation of the bare note number only → both subrefs not_verified.
    _seed_prov(db_path, run_id, 48, ["9"])
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="verify_subnote", args={
            "note_num": 9, "subnote_ref": "(a)", "verdict": "verified",
            "reason": "present in cell", "source_pages": [19]})],
        [ToolCallPart(tool_name="verify_subnote", args={
            "note_num": 9, "subnote_ref": "(b)", "verdict": "missing",
            "reason": "absent", "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    cl = ra.recompute_notes_findings(deps)["coverage_checklist"]
    states = {s.subnote_ref: s.state for s in _row(cl, 9).subnotes}
    assert states == {"(a)": SUBNOTE_VERIFIED, "(b)": SUBNOTE_MISSING}
    # A confirmed-missing sub-ref makes the placed row unresolved (tips status).
    assert _row(cl, 9).is_unresolved() is True


def test_author_then_clear_drops_reviewer_added_marker(db_path):
    """Authoring a missing note then clearing the same cell must not leave a
    stale reviewer-added marker on the (now-missing) coverage row (code-review
    I3)."""
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "Disclosure of X")
    _seed_inv(db_path, run_id, 4, "Investment properties")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cell", args={
            "sheet": _S12, "row": 50, "html": "<p>ip prose</p>",
            "note_num": 4, "source_pages": [19], "evidence": "note"})],
        [ToolCallPart(tool_name="clear_note_cell", args={
            "sheet": _S12, "row": 50, "source_pages": [19], "evidence": "undo"})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert 4 not in deps.authored_note_nums
    cl = ra.recompute_notes_findings(deps)["coverage_checklist"]
    row = _row(cl, 4)
    assert row.status == STATUS_MISSING
    assert row.reviewer_added is False


def test_verify_subnote_requires_grounding(db_path):
    run_id = _seed_run(db_path)
    _seed_inv(db_path, run_id, 9, subs=["(a)"])
    _seed_prov(db_path, run_id, 48, ["9"])
    model = _scripted([
        [ToolCallPart(tool_name="verify_subnote", args={
            "note_num": 9, "subnote_ref": "(a)", "verdict": "verified",
            "reason": "x", "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert (9, "a") not in deps.coverage_subnote_verdicts
    assert deps.fix_rejections.get("ungrounded") == 1


# --------------------------------------------------------------------------
# Batch tools (2026-07-07) — fewer turns for the same work. Each batch tool
# applies every item under the SAME grounding + snapshot guarantees as its
# singular sibling, so the reviewer no longer burns one turn per row/ref.
# --------------------------------------------------------------------------


def test_verify_subnotes_batch_records_all_in_one_call(db_path):
    run_id = _seed_run(db_path)
    _seed_inv(db_path, run_id, 9, "Investment properties", subs=["(a)", "(b)", "(c)"])
    _seed_prov(db_path, run_id, 48, ["9"])  # coarse cite → all not_verified
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="verify_subnotes", args={
            "note_num": 9, "subnote_refs": ["(a)", "(b)", "(c)"],
            "verdict": "verified", "reason": "all present", "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    # All three recorded from ONE tool call.
    assert deps.coverage_subnote_verdicts[(9, "a")]["verdict"] == SUBNOTE_VERIFIED
    assert deps.coverage_subnote_verdicts[(9, "b")]["verdict"] == SUBNOTE_VERIFIED
    assert deps.coverage_subnote_verdicts[(9, "c")]["verdict"] == SUBNOTE_VERIFIED
    cl = ra.recompute_notes_findings(deps)["coverage_checklist"]
    states = {s.subnote_ref: s.state for s in _row(cl, 9).subnotes}
    assert states == {"(a)": SUBNOTE_VERIFIED, "(b)": SUBNOTE_VERIFIED,
                      "(c)": SUBNOTE_VERIFIED}


def test_verify_subnotes_requires_grounding(db_path):
    run_id = _seed_run(db_path)
    _seed_inv(db_path, run_id, 9, subs=["(a)", "(b)"])
    _seed_prov(db_path, run_id, 48, ["9"])
    model = _scripted([
        [ToolCallPart(tool_name="verify_subnotes", args={
            "note_num": 9, "subnote_refs": ["(a)", "(b)"], "verdict": "verified",
            "reason": "x", "source_pages": [19]})],  # never viewed page 19
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert (9, "a") not in deps.coverage_subnote_verdicts
    assert (9, "b") not in deps.coverage_subnote_verdicts
    assert deps.fix_rejections.get("ungrounded") == 1


def _tool_return_texts(result, tool_name: str) -> list[str]:
    """All ToolReturnPart contents for a given tool across the run's messages."""
    out: list[str] = []
    for msg in result.all_messages():
        for part in getattr(msg, "parts", []):
            if (getattr(part, "part_kind", "") == "tool-return"
                    and getattr(part, "tool_name", "") == tool_name):
                out.append(str(getattr(part, "content", "")))
    return out


def test_verify_subnotes_partial_failure_is_honest(db_path):
    """A batch with one malformed ref records the valid ones but reports
    'partial:', not a bare 'ok:', so the agent knows to retry the bad item."""
    run_id = _seed_run(db_path)
    _seed_inv(db_path, run_id, 9, subs=["(a)", "(b)"])
    _seed_prov(db_path, run_id, 48, ["9"])
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="verify_subnotes", args={
            "note_num": 9, "subnote_refs": ["(a)", "   "],  # 2nd ref is blank
            "verdict": "verified", "reason": "x", "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    result = agent.run_sync("go", deps=deps)
    # The valid ref landed; the blank one did not.
    assert (9, "a") in deps.coverage_subnote_verdicts
    assert (9, "") not in deps.coverage_subnote_verdicts
    # The tool reported a partial, not an unqualified success.
    returns = _tool_return_texts(result, "verify_subnotes")
    assert returns and returns[0].startswith("partial:")


def test_resolve_coverage_notes_batch_resolves_all(db_path):
    run_id = _seed_run(db_path)
    _seed_inv(db_path, run_id, 4, "Note four")
    _seed_inv(db_path, run_id, 5, "Note five")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="resolve_coverage_notes", args={
            "note_nums": [4, 5], "verdict": "not_applicable",
            "reason": "neither applies", "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert deps.coverage_note_verdicts[4]["verdict"] == "not_applicable"
    assert deps.coverage_note_verdicts[5]["verdict"] == "not_applicable"


def test_clear_note_cells_batch_clears_all_rows(db_path):
    run_id = _seed_run(db_path)
    _seed_prov(db_path, run_id, 48, ["8"])
    _seed_prov(db_path, run_id, 49, ["9"])
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="clear_note_cells", args={
            "sheet": _S12, "rows": [48, 49], "source_pages": [19],
            "evidence": "duplicates"})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert deps.writes_performed == 2
    with repo.db_session(db_path) as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM notes_cells WHERE run_id = ? AND sheet = ? "
            "AND row IN (48, 49)", (run_id, _S12),
        ).fetchone()[0]
    assert left == 0
