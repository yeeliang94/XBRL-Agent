"""Batched prose-write tools on the notes reviewer.

The reviewer's prose writes used to be one cell per turn (edit_note_cell /
author_note_cell), which burns the pass's tight turn + wall-clock budget. They
are now list-shaped (edit_note_cells / author_note_cells): one call carries
several cells, each grounded + written independently through the same _do_write
path. This pins (a) the prompt advertises the batched form + tells the agent to
batch, and (b) the agent registers the plural tools, not the singular ones.
"""
from __future__ import annotations

from pathlib import Path

_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "notes_reviewer.md"


def test_notes_reviewer_prompt_advertises_batched_prose_writes():
    text = _PROMPT.read_text(encoding="utf-8")
    # Plural, list-shaped signatures are advertised.
    assert "edit_note_cells([{" in text
    assert "author_note_cells([{" in text
    # Explicit batch-habit guidance.
    lower = text.lower()
    assert "batch every cell" in lower or "one call instead of one call per turn" in lower
    # The singular backtick forms are gone from the prompt.
    assert "`edit_note_cell`" not in text
    assert "`author_note_cell`" not in text


def test_notes_reviewer_registers_batched_write_tools(tmp_path):
    """The wired agent exposes the plural tools and NOT the singular ones."""
    from pydantic_ai.models.test import TestModel
    from db.schema import init_db
    from notes.reviewer_agent import create_notes_reviewer_agent

    db = tmp_path / "n.db"
    init_db(db)
    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        run_id = int(conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-07-08T00:00:00Z', 'x.pdf', 'completed')"
        ).lastrowid)
        conn.commit()
    finally:
        conn.close()

    agent, _deps, _ctx = create_notes_reviewer_agent(
        run_id=run_id, db_path=str(db), pdf_path="/tmp/x.pdf",
        filing_level="company", filing_standard="mfrs",
        model=TestModel(call_tools=[]), output_dir=str(tmp_path),
    )
    names = set()
    for ts in agent.toolsets:
        tools = getattr(ts, "tools", {})
        if isinstance(tools, dict):
            names |= set(tools)
    assert {"edit_note_cells", "author_note_cells"} <= names, names
    assert "edit_note_cell" not in names and "author_note_cell" not in names
