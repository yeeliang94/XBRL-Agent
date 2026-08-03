"""The formatter is the one role that judges borders, and it could not zoom.

Peer review, 2026-08-01. `notes/formatting_agent.py` registered only
`view_pdf_pages` (whole pages, downscaled hard before they reach the model)
and `read_note_cell` (since removed as redundant). Its job is deciding a
border's extent, whether a rule is single or double, and whether a caption is
right-aligned — exactly the detail a downscaled full page loses.

The notes extraction agent already had `zoom_pdf_region`, and
`prompts/_notes_base.md` tells it to zoom before judging a rule. The formatter
was given the harder version of that task with the weaker view.

A zoom also counts as viewing the page: the write guard checks
`viewed_pages`, so a formatter that zoomed and then wrote would otherwise be
refused its own grounded edit.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_PROMPT = (
    Path(__file__).resolve().parent.parent / "prompts" / "notes_formatter.md"
).read_text(encoding="utf-8")


def _tool_names(agent) -> set[str]:
    names: set[str] = set()
    for ts in getattr(agent, "toolsets", []) or []:
        tools = getattr(ts, "tools", {}) or {}
        if isinstance(tools, dict):
            names.update(tools.keys())
    return names


def _build(tmp_path):
    from pydantic_ai.models.test import TestModel

    from notes.formatting_agent import create_notes_formatter_agent

    return create_notes_formatter_agent(
        run_id=1, sheet="Notes-Listofnotes", pdf_path="/tmp/no.pdf",
        db_path=str(tmp_path / "x.db"), model=TestModel(),
    )


def test_formatter_has_the_zoom_tool(tmp_path):
    built = _build(tmp_path)
    agent = built[0] if isinstance(built, tuple) else built
    names = _tool_names(agent)
    assert "zoom_pdf_region" in names, sorted(names)
    # The weaker view it used to be limited to must still be there.
    assert "view_pdf_pages" in names


def test_zoom_shares_the_extraction_agents_region_vocabulary():
    """Two different region vocabularies across two agents is how an operator
    learns one and gets an error from the other."""
    from notes.agent import resolve_zoom_region

    for region in ("top-half", "bottom-third", "top-left", "center", "full"):
        resolve_zoom_region(region)
    with pytest.raises(ValueError):
        resolve_zoom_region("upper-bit")


def test_prompt_tells_the_formatter_to_zoom():
    """A registered tool the prompt never mentions is a tool the model uses
    late or not at all — the same reason the extraction prompt says it."""
    assert "zoom_pdf_region" in _PROMPT
    assert "downscaled" in _PROMPT


def test_zoom_marks_the_page_as_viewed(tmp_path):
    """The write guard refuses a write whose source_pages were not viewed.
    If zooming did not count, a formatter that zoomed would be blocked from
    acting on what it just looked at."""
    import inspect

    from notes import formatting_agent

    src = inspect.getsource(formatting_agent)
    zoom_src = src[src.index("async def zoom_pdf_region"):]
    zoom_src = zoom_src[: zoom_src.index("return agent, deps")]
    assert "viewed_pages.add(page)" in zoom_src


def test_the_formatter_has_no_tool_that_re_reads_the_prompt(tmp_path):
    """`read_note_cell` returned sheet/row/label/html/evidence/source_pages
    for ONE row. `_build_user_prompt`'s CURRENT CELLS payload already
    carries exactly those fields for EVERY row, plus `table_geometry` the
    tool did not have — so a call could only return a strict subset of what
    the model had already read, at the cost of a turn, and its schema rode on
    every request (peer review, 2026-08-03).

    Pinned because the redundancy is invisible from either side alone.
    """
    import inspect

    from notes import formatting_agent

    built = _build(tmp_path)
    agent = built[0] if isinstance(built, tuple) else built
    assert "read_note_cell" not in _tool_names(agent)

    # The payload must still carry everything the tool used to serve.
    src = inspect.getsource(formatting_agent._build_user_prompt)
    for field in ("row", "label", "html", "evidence", "source_pages",
                  "table_geometry"):
        assert field in src, field
