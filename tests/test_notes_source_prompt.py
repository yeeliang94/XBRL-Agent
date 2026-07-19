"""Pin the Word-source formatting block + tool gating (PLAN-word-input Step 9).

The block and the read_source_note tool must appear ONLY when a source.html
sidecar exists for the run — PDF-only runs render exactly as before.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from notes.agent import (
    _render_source_html_block,
    create_notes_agent,
    render_notes_prompt,
)
from notes_types import NotesTemplateType


def test_block_absent_when_unavailable():
    assert _render_source_html_block(False) is None


def test_block_present_and_shaped_when_available():
    block = _render_source_html_block(True)
    assert block is not None
    assert "read_source_note" in block
    assert "PDF wins" in block  # source is a reference, PDF is ground truth

    # VERBATIM PASSTHROUGH (2026-07-19). The block previously told the agent to
    # translate each source `style=` into a `format_ops` entry; that round-trip
    # through model judgement was the "AI guessing the formatting" reported in
    # run 74. Tables are now copied byte-for-byte into `content`.
    assert "VERBATIM" in block.upper()
    assert "style=" in block  # the attribute is copied, not re-described
    lowered = block.lower()
    assert "do not" in lowered and "translate" in lowered
    # format_ops survives ONLY as the PDF-only fallback, never the table path.
    assert "format_ops" in block
    assert "fallback" in lowered
    # Gotcha #16 is reversed for tables ONLY — prose must stay style-free.
    assert "prose stays style-free" in lowered


def test_render_notes_prompt_gates_on_flag():
    kwargs = dict(
        template_type=NotesTemplateType.CORP_INFO,
        filing_level="company",
        inventory=[],
    )
    with_src = render_notes_prompt(**kwargs, source_html_available=True)
    without = render_notes_prompt(**kwargs, source_html_available=False)
    assert "SOURCE DOCUMENT FORMATTING" in with_src
    assert "SOURCE DOCUMENT FORMATTING" not in without


def _tool_names(agent) -> set[str]:
    # Mirror tests/test_notes_agent_factory.py's version-stable accessor.
    for attr in ("_function_toolset", "function_toolset", "toolset"):
        ts = getattr(agent, attr, None)
        if ts is None:
            continue
        tools = getattr(ts, "tools", None)
        if tools is None:
            continue
        if isinstance(tools, dict):
            names = {getattr(t, "name", None) or k for k, t in tools.items()}
        else:
            names = {getattr(t, "name", None) for t in tools}
        return {n for n in names if n}
    return set()


def _make_agent(pdf_path: str):
    agent, deps = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=pdf_path,
        inventory=[],
        filing_level="company",
        model="test",
    )
    return agent, deps


def test_tool_registered_only_with_sidecar(tmp_path: Path):
    # No sidecar → tool absent, deps path None.
    (tmp_path / "uploaded.pdf").write_bytes(b"%PDF")
    agent_no, deps_no = _make_agent(str(tmp_path / "uploaded.pdf"))
    assert "read_source_note" not in _tool_names(agent_no)
    assert deps_no.source_html_path is None

    # With sidecar → tool present, deps path set.
    with_dir = tmp_path / "withsrc"
    with_dir.mkdir()
    (with_dir / "uploaded.pdf").write_bytes(b"%PDF")
    (with_dir / "source.html").write_text("<h1>4. X</h1><p>y</p>", encoding="utf-8")
    agent_yes, deps_yes = _make_agent(str(with_dir / "uploaded.pdf"))
    assert "read_source_note" in _tool_names(agent_yes)
    assert deps_yes.source_html_path == str(with_dir / "source.html")


# --- unconsulted-source nudge (2026-07-19) ---------------------------------
# Run 74: the Accounting Policies agent never called read_source_note, so its
# tables were rebuilt from the PDF while its peers copied real Word markup.

def test_unconsulted_source_nudge_is_silent_at_zero():
    from notes.agent import format_unconsulted_source_nudge

    assert format_unconsulted_source_nudge(0) == ""
    assert format_unconsulted_source_nudge(-1) == ""


def test_unconsulted_source_nudge_invites_a_resend_without_demanding_one():
    from notes.agent import format_unconsulted_source_nudge

    msg = format_unconsulted_source_nudge(3)
    assert "read_source_note" in msg
    assert "3 table cell(s)" in msg
    # Never pushes the agent to invent formatting when the source has none.
    assert "no action is needed" in msg.lower()


_PN = {"number": "5", "title": "Test note"}


def test_payload_consulted_helper_tracks_note_refs():
    from notes.agent import NotesDeps, _payload_source_consulted
    from notes.payload import NotesPayload

    deps = NotesDeps(
        pdf_path="x", template_path="y", model=None, output_dir="z",
        token_report=None, template_type=None, sheet_name="s",
        filing_level="company",
    )
    deps.consulted_source_notes = {5}
    p_hit = NotesPayload(chosen_row_label="a", content="<table></table>",
                         evidence="e", note_num=5, parent_note=_PN)
    p_miss = NotesPayload(chosen_row_label="a", content="<table></table>",
                          evidence="e", note_num=7, parent_note={"number": "7", "title": "Other"})
    p_sub = NotesPayload(chosen_row_label="a", content="<table></table>",
                         evidence="e", source_note_refs=["5.1"], parent_note=_PN)
    p_none = NotesPayload(chosen_row_label="a", content="<table></table>",
                          evidence="e", parent_note=_PN)
    assert _payload_source_consulted(deps, p_hit)
    assert not _payload_source_consulted(deps, p_miss)
    assert _payload_source_consulted(deps, p_sub)  # "5.1" -> parent note 5
    # parent_note number alone is enough to resolve the note (it is
    # mandatory on any content payload), and note 5 was consulted.
    assert _payload_source_consulted(deps, p_none)
