"""Notes coverage — persistence + run-status tipping through the reviewer pass
(docs/PLAN-notes-coverage-and-routing.md Phase 6).

Drives the real `server._run_notes_reviewer_pass` over a seeded DB (mirrors
tests/test_notes_reviewer_pipeline.py) with XBRL_NOTES_COVERAGE forced ON, and
unit-tests the pure status-tipping helper's matrix.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import notes.reviewer_agent as ra
import notes.detectors as det
from db import repository as repo
from db.schema import init_db

_S12 = "Notes-Listofnotes"
_PREFIX = "mfrs-company-"


@pytest.fixture(autouse=True)
def _coverage_on(monkeypatch):
    monkeypatch.setenv("XBRL_NOTES_COVERAGE", "true")


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


@pytest.fixture(autouse=True)
def _mock_pdf(monkeypatch):
    monkeypatch.setattr(ra, "count_pdf_pages", lambda _p: 60)
    monkeypatch.setattr(
        det, "render_pages_to_png_bytes",
        lambda pdf_path, start, end, dpi=200: [b"png"],
    )


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _scripted(steps):
    idx = {"i": 0}

    def fn(messages, info):
        i = idx["i"]; idx["i"] += 1
        return ModelResponse(parts=steps[i]) if i < len(steps) else \
            ModelResponse(parts=[TextPart("done")])

    return FunctionModel(fn)


def _run_pass(db_path, run_id, tmp_path, model, q):
    import server
    return asyncio.run(server._run_notes_reviewer_pass(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model=model, output_dir=str(tmp_path),
        merged_workbook_path=None, event_queue=q, sidecar_paths=[],
    ))


def _seed_inv(db_path, run_id, note_num, subs=None):
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_inventory(conn, run_id=run_id, note_num=note_num,
                                    subnote_refs=subs, page_lo=30, page_hi=31)


def _seed_placed(db_path, run_id, row, refs):
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=_S12, row=row,
                               label=f"Row {row}", html="<p>x</p>")
        repo.upsert_notes_provenance(conn, run_id=run_id, sheet=_S12, row=row,
                                     row_label=f"Row {row}", source_note_refs=refs)


def _coverage_rows(db_path, run_id):
    with repo.db_session(db_path) as conn:
        return repo.fetch_notes_coverage(conn, run_id)


# ---- pure status-tipping matrix -------------------------------------------


def test_status_tip_matrix():
    from server import _notes_coverage_tips_status as tips
    assert tips(None) is False                                  # didn't run
    assert tips({"unresolved": 0, "banner": "reviewed"}) is False   # all clean
    assert tips({"unresolved": 1, "banner": "reviewed"}) is True    # missing
    assert tips({"unresolved": 0,
                 "banner": "inventory_unavailable"}) is True        # empty inv
    # not_verified sub-refs never reach `unresolved`, so a clean count is False.
    assert tips({"unresolved": 0, "banner": "not_reviewed"}) is False


# ---- persistence + tipping through the pass -------------------------------


def test_missing_note_persists_unresolved_and_emits_event(db_path, tmp_path):
    run_id = _run_seed = None
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s",
                                 output_dir=str(tmp_path))
    _seed_inv(db_path, run_id, 4)  # in inventory, no placement → missing
    q: asyncio.Queue = asyncio.Queue()
    # Reviewer views but doesn't fix (leaves the gap).
    outcome = _run_pass(db_path, run_id, tmp_path, _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [30]})],
        [TextPart("done")],
    ]), q)
    cov = outcome["coverage"]
    assert cov["banner"] == "reviewed"
    assert cov["unresolved"] == 1
    events = _drain(q)
    assert any(e["event"] == "notes_coverage" for e in events)
    rows = [r for r in _coverage_rows(db_path, run_id) if r["note_num"] == 4]
    assert rows and rows[0]["status"] == "missing"


def test_confirmed_absent_resolves_and_does_not_tip(db_path, tmp_path):
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s",
                                 output_dir=str(tmp_path))
    _seed_inv(db_path, run_id, 12)
    _seed_inv(db_path, run_id, 14)
    _seed_placed(db_path, run_id, 30, ["12"])
    _seed_placed(db_path, run_id, 32, ["14"])  # both placed → only gap is 13
    q: asyncio.Queue = asyncio.Queue()
    outcome = _run_pass(db_path, run_id, tmp_path, _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [30]})],
        [ToolCallPart(tool_name="resolve_coverage_note", args={
            "note_num": 13, "verdict": "confirmed_absent",
            "reason": "PDF skips 13", "source_pages": [30]})],
        [TextPart("done")],
    ]), q)
    cov = outcome["coverage"]
    assert cov["unresolved"] == 0
    from server import _notes_coverage_tips_status
    assert _notes_coverage_tips_status(cov) is False
    gap = [r for r in _coverage_rows(db_path, run_id) if r["note_num"] == 13]
    assert gap and gap[0]["reviewer_verdict"] == "confirmed_absent"


def test_empty_inventory_is_loud_and_tips(db_path, tmp_path):
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s",
                                 output_dir=str(tmp_path))
    # No inventory at all → loud inventory_unavailable, and the skip path still
    # finalizes coverage.
    q: asyncio.Queue = asyncio.Queue()
    outcome = _run_pass(db_path, run_id, tmp_path,
                        _scripted([[TextPart("done")]]), q)
    cov = outcome["coverage"]
    assert cov["banner"] == "inventory_unavailable"
    from server import _notes_coverage_tips_status
    assert _notes_coverage_tips_status(cov) is True
    events = _drain(q)
    assert any(e["event"] == "error"
               and e["data"].get("type") == "notes_inventory_unavailable"
               for e in events)


def test_coverage_gate_off_persists_nothing(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_NOTES_COVERAGE", "false")
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s",
                                 output_dir=str(tmp_path))
    _seed_inv(db_path, run_id, 4)
    q: asyncio.Queue = asyncio.Queue()
    outcome = _run_pass(db_path, run_id, tmp_path, _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [30]})],
        [TextPart("done")],
    ]), q)
    assert "coverage" not in outcome
    assert _coverage_rows(db_path, run_id) == []
    events = _drain(q)
    assert not any(e["event"] == "notes_coverage" for e in events)
