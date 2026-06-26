"""Phase 3 Step 9 — _run_notes_reviewer_pass end-to-end (mocked model).

Drives the real server pass with a scripted FunctionModel over a seeded DB:
the pass builds findings, snapshots, the agent fixes a collision, flags are
persisted, and the outcome reflects the work.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import notes.reviewer_agent as ra
import notes.detectors as det  # _render_single_page (PDF render) lives here now
from db import repository as repo
from db.schema import init_db
from notes.persistence import persist_notes_review_inputs

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
    monkeypatch.setattr(
        det, "render_pages_to_png_bytes",
        lambda pdf_path, start, end, dpi=200: [b"png"],
    )


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_reviewer_pass_runs_snapshots_and_persists_flags(db_path: Path, tmp_path):
    import server

    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir=str(tmp_path))
        # A real Sheet-12 collision: row 49 holds notes 4 + 20.
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=_S12, row=49,
                               label="Disclosure of fair value information",
                               html="<p>fair value</p>")
        # An empty LEAF target to re-route into.
        conn.execute(
            "INSERT INTO notes_nodes(node_uuid, template_id, sheet, row, label, kind) "
            "VALUES (?, ?, ?, ?, ?, 'LEAF')",
            ("n80", f"{_PREFIX}notes-listofnotes-v1", _S12, 80,
             "Disclosure of financial instruments"),
        )

    persist_notes_review_inputs(
        db_path=str(db_path), run_id=run_id,
        sidecar_entries=[{
            "sheet": _S12, "row": 49, "row_label": "Disclosure of fair value information",
            "source_note_refs": ["4.1", "20.7"], "content_preview": "fv"}],
        inventory=[{"note_num": 4, "subnote_refs": []},
                   {"note_num": 20, "subnote_refs": []}],
    )

    # Scripted reviewer: view, move the collision, then flag + finish.
    steps = [
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [36]})],
        [ToolCallPart(tool_name="move_note_cell", args={
            "from_sheet": _S12, "from_row": 49, "to_sheet": _S12, "to_row": 80,
            "source_pages": [36], "evidence": "FI fair value note"})],
        [ToolCallPart(tool_name="raise_flag", args={
            "kind": "needs_human", "reason": "double-check the split"})],
        [TextPart("done")],
    ]
    idx = {"i": 0}

    def fn(messages, info):
        i = idx["i"]; idx["i"] += 1
        return ModelResponse(parts=steps[i]) if i < len(steps) else \
            ModelResponse(parts=[TextPart("done")])

    q: asyncio.Queue = asyncio.Queue()
    outcome = asyncio.run(server._run_notes_reviewer_pass(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model=FunctionModel(fn), output_dir=str(tmp_path),
        merged_workbook_path=None, event_queue=q,
        sidecar_paths=[],
    ))

    assert outcome["invoked"] is True
    assert outcome["writes_performed"] == 1
    assert outcome["flags_raised"] == 1

    # The collision was re-routed in notes_cells.
    with repo.db_session(db_path) as conn:
        cells = {c.row: c.html for c in repo.list_notes_cells_for_run(conn, run_id)}
        flags = repo.fetch_notes_review_flags(conn, run_id)
    assert 49 not in cells and 80 in cells
    assert len(flags) == 1 and flags[0]["kind"] == "needs_human"

    # Snapshot exists → revert is possible.
    from notes.versioning import has_notes_snapshot
    assert has_notes_snapshot(str(db_path), run_id) is True

    events = _drain(q)
    assert any(e["event"] == "complete" and e["data"].get("success") for e in events)


def test_reviewer_pass_skips_when_no_findings(db_path: Path, tmp_path):
    import server

    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir=str(tmp_path))
    # No provenance / no cells → no findings.
    q: asyncio.Queue = asyncio.Queue()
    outcome = asyncio.run(server._run_notes_reviewer_pass(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")])),
        output_dir=str(tmp_path), merged_workbook_path=None, event_queue=q,
        sidecar_paths=[],
    ))
    assert outcome["invoked"] is False
    assert outcome["writes_performed"] == 0
    # No snapshot taken on a skip (nothing to protect).
    from notes.versioning import has_notes_snapshot
    assert has_notes_snapshot(str(db_path), run_id) is False
