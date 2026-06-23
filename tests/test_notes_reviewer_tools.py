"""Notes-reviewer write tools + title detector + packet (docs/PLAN.md Steps 5,7,8).

The tools are agent closures, so they're exercised by driving the real agent
with a scripted FunctionModel (view a page, then call a write tool). Asserts:
  - tools mutate ONLY notes_cells (DB), never an xlsx;
  - author/move into an occupied or ABSTRACT row is refused;
  - edit preserves the writer-owned leading <h3>;
  - move re-routes prose and clears the source.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import notes.reviewer_agent as ra
import notes.validator_agent as va
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
    # 30-page PDF; rendering returns a stub image so view_pdf_pages records
    # the page into viewed_pages without a real file.
    monkeypatch.setattr(ra, "count_pdf_pages", lambda _p: 30)
    monkeypatch.setattr(
        va, "render_pages_to_png_bytes",
        lambda pdf_path, start, end, dpi=200: [b"png"],
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


def _seed_inventory(db_path: Path, run_id: int, note_num: int) -> None:
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_inventory(conn, run_id=run_id, note_num=note_num)


def _seed_cell(db_path: Path, run_id: int, row: int, html: str) -> None:
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_S12, row=row, label=f"Row {row}", html=html,
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


def _cells(db_path: Path, run_id: int) -> dict[int, str]:
    with repo.db_session(db_path) as conn:
        return {c.row: c.html for c in repo.list_notes_cells_for_run(conn, run_id)}


# --------------------------------------------------------------------------


def test_author_into_empty_leaf_creates_cell(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "Disclosure of X")
    _seed_inventory(db_path, run_id, 4)
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cell", args={
            "sheet": _S12, "row": 50, "html": "<p>grounded prose</p>",
            "note_num": 4, "source_pages": [19], "evidence": "fair value note"})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    cells = _cells(db_path, run_id)
    assert 50 in cells and "grounded prose" in cells[50]
    assert deps.writes_performed == 1
    # A snapshot was taken before the write (reversibility).
    from notes.versioning import has_notes_snapshot
    assert has_notes_snapshot(str(db_path), run_id) is True


def test_author_into_abstract_row_refused(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_node(db_path, 51, "ABSTRACT", "Section header")
    _seed_inventory(db_path, run_id, 4)
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cell", args={
            "sheet": _S12, "row": 51, "html": "<p>x</p>", "note_num": 4,
            "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert 51 not in _cells(db_path, run_id)
    assert deps.fix_rejections.get("not_leaf") == 1


def test_author_into_occupied_row_refused(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_node(db_path, 49, "LEAF", "Occupied")
    _seed_inventory(db_path, run_id, 4)
    _seed_cell(db_path, run_id, 49, "<p>existing</p>")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="author_note_cell", args={
            "sheet": _S12, "row": 49, "html": "<p>new</p>", "note_num": 4,
            "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert _cells(db_path, run_id)[49] == "<p>existing</p>"  # untouched
    assert deps.fix_rejections.get("occupied_target") == 1


def test_ungrounded_author_refused(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_node(db_path, 50, "LEAF", "X")
    _seed_inventory(db_path, run_id, 4)
    # No view_pdf_pages first → source_pages not in viewed set.
    model = _scripted([
        [ToolCallPart(tool_name="author_note_cell", args={
            "sheet": _S12, "row": 50, "html": "<p>x</p>", "note_num": 4,
            "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert 50 not in _cells(db_path, run_id)
    assert deps.fix_rejections.get("ungrounded") == 1


def test_edit_preserves_leading_heading(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_cell(db_path, run_id, 49,
               "<h3>4 Investment property</h3><p>OLD body</p>")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="edit_note_cell", args={
            "sheet": _S12, "row": 49, "html": "<p>NEW body</p>",
            "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    html = _cells(db_path, run_id)[49]
    assert "<h3>4 Investment property</h3>" in html  # heading preserved
    assert "NEW body" in html and "OLD body" not in html


def test_move_reroutes_and_clears_source(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    _seed_cell(db_path, run_id, 49, "<p>fair value of FI</p>")
    _seed_node(db_path, 80, "LEAF", "Disclosure of financial instruments")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [22]})],
        [ToolCallPart(tool_name="move_note_cell", args={
            "from_sheet": _S12, "from_row": 49, "to_sheet": _S12, "to_row": 80,
            "source_pages": [22]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    cells = _cells(db_path, run_id)
    assert 49 not in cells  # source cleared
    assert 80 in cells and "fair value of FI" in cells[80]


def test_edit_with_script_only_html_is_refused_not_destructive(db_path: Path) -> None:
    """Peer-review #1: content that sanitises to empty must NOT overwrite the
    existing valid body — the edit is refused before any write."""
    run_id = _seed_run(db_path)
    _seed_cell(db_path, run_id, 49, "<h3>4 X</h3><p>VALID body</p>")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="edit_note_cell", args={
            "sheet": _S12, "row": 49, "html": "<script>alert(1)</script>",
            "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    # Original body intact; the destructive write was refused.
    assert "VALID body" in _cells(db_path, run_id)[49]
    assert deps.fix_rejections.get("empty_content") == 1
    assert deps.writes_performed == 0


def test_move_from_non_prose_sheet_refused(db_path: Path) -> None:
    """Peer-review #2: both ends of a move must be prose sheets."""
    run_id = _seed_run(db_path)
    _seed_node(db_path, 80, "LEAF", "X")
    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [19]})],
        [ToolCallPart(tool_name="move_note_cell", args={
            "from_sheet": "SOFP", "from_row": 10, "to_sheet": _S12, "to_row": 80,
            "source_pages": [19]})],
    ])
    agent, deps, _ = _agent(db_path, run_id, model)
    agent.run_sync("go", deps=deps)
    assert deps.writes_performed == 0
    assert 80 not in _cells(db_path, run_id)


def test_title_detector_flags_missing_heading():
    issues = ra.detect_title_format_issues([
        {"sheet": _S12, "row": 49, "label": "X", "html": "<p>no heading</p>"},
        {"sheet": _S12, "row": 50, "label": "Y",
         "html": "<h3>5 Revenue</h3><p>ok</p>"},
    ])
    assert [i["row"] for i in issues] == [49]


def test_packet_renders_present_families_only():
    packet = ra.build_notes_reviewer_packet({
        "row_collisions": [{"row": 49, "row_label": "FV", "note_nums": [4, 20],
                            "source_note_refs": ["4.1", "20.7"]}],
    })
    assert "SAME-SHEET COLLISION" in packet
    assert "CROSS-SHEET DUPLICATION" not in packet  # no dup findings supplied


def test_packet_clean_run_is_short():
    packet = ra.build_notes_reviewer_packet({})
    assert "No structural findings" in packet
