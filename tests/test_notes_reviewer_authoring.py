"""Phase 5 / Step 13 — adversarial tests on the riskiest path: author_note_cell.

Authoring fills a previously-blank cell with grounded prose — the highest
fabrication risk. These pin that every guard rail holds and that an authored
cell is fully revertible.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import notes.reviewer_agent as ra
import notes.detectors as det  # _render_single_page (PDF render) lives here now
from db import repository as repo
from db.schema import init_db

_S12 = "Notes-Listofnotes"
_PREFIX = "mfrs-company-"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


@pytest.fixture(autouse=True)
def _mock_pdf(monkeypatch):
    monkeypatch.setattr(ra, "count_pdf_pages", lambda _p: 60)
    monkeypatch.setattr(det, "render_pages_to_png_bytes",
                        lambda pdf_path, start, end, dpi=200: [b"png"])


def _seed_run(db_path: Path) -> int:
    with repo.db_session(db_path) as conn:
        return repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")


def _seed_node(db_path, row, kind, label):
    with repo.db_session(db_path) as conn:
        conn.execute(
            "INSERT INTO notes_nodes(node_uuid, template_id, sheet, row, label, kind) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"n{row}", f"{_PREFIX}notes-listofnotes-v1", _S12, row, label, kind),
        )


def _agent(db_path, run_id, steps):
    idx = {"i": 0}

    def fn(messages, info):
        i = idx["i"]; idx["i"] += 1
        return ModelResponse(parts=steps[i]) if i < len(steps) else \
            ModelResponse(parts=[TextPart("done")])

    return ra.create_notes_reviewer_agent(
        run_id=run_id, db_path=str(db_path), pdf_path="/tmp/x.pdf",
        filing_level="company", filing_standard="mfrs",
        model=FunctionModel(fn), output_dir=str(db_path.parent),
    )


def _cells(db_path, run_id):
    with repo.db_session(db_path) as conn:
        return {c.row: c.html for c in repo.list_notes_cells_for_run(conn, run_id)}


def test_author_without_viewing_pages_refused(db_path):
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "X")
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_inventory(conn, run_id=run_id, note_num=4)
    agent, deps, _ = _agent(db_path, run_id, [
        [ToolCallPart(tool_name="author_note_cell", args={
            "sheet": _S12, "row": 50, "html": "<p>x</p>", "note_num": 4,
            "source_pages": [19]})],
    ])
    agent.run_sync("go", deps=deps)
    assert 50 not in _cells(db_path, run_id)
    assert deps.fix_rejections.get("ungrounded") == 1


def test_author_for_note_scout_never_saw_refused(db_path):
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "X")
    # Inventory has note 4 only; authoring note 99 must be refused.
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_inventory(conn, run_id=run_id, note_num=4)
    agent, deps, _ = _agent(db_path, run_id, [
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cell", args={
            "sheet": _S12, "row": 50, "html": "<p>x</p>", "note_num": 99,
            "source_pages": [19]})],
    ])
    agent.run_sync("go", deps=deps)
    assert 50 not in _cells(db_path, run_id)
    assert deps.fix_rejections.get("note_not_in_inventory") == 1


def test_grounded_author_accepted_and_revertible(db_path):
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "Disclosure of X")
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_inventory(conn, run_id=run_id, note_num=4)
    agent, deps, _ = _agent(db_path, run_id, [
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cell", args={
            "sheet": _S12, "row": 50, "html": "<p>grounded</p>", "note_num": 4,
            "source_pages": [19], "evidence": "note 4"})],
    ])
    agent.run_sync("go", deps=deps)
    assert "grounded" in _cells(db_path, run_id)[50]

    # The authored cell is fully revertible back to the (empty) original.
    from notes.versioning import revert_notes_to_original
    out = revert_notes_to_original(str(db_path), run_id)
    assert out["reverted"] is True
    assert _cells(db_path, run_id) == {}


def test_author_only_grounds_on_viewed_subset(db_path):
    """Viewing page 19 does not license citing page 20 — source_pages must be a
    subset of the pages actually viewed."""
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "X")
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_inventory(conn, run_id=run_id, note_num=4)
    agent, deps, _ = _agent(db_path, run_id, [
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cell", args={
            "sheet": _S12, "row": 50, "html": "<p>x</p>", "note_num": 4,
            "source_pages": [19, 20]})],  # 20 was never viewed
    ])
    agent.run_sync("go", deps=deps)
    assert 50 not in _cells(db_path, run_id)
    assert deps.fix_rejections.get("ungrounded") == 1
