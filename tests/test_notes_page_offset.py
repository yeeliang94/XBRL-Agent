"""Phase 4 (post-FINCO-2021 audit) — scout `page_offset` propagation.

Scout measures how the printed folio at the bottom of the page image
differs from the PDF page index (PDF page N = printed page N − offset).
Before Phase 4 only the face-statement side read this number — the
notes path ignored it, which is why Sheet-12 sub-agents fell back to
citing the printed folio in `evidence`.

Pinned here:
- `render_notes_prompt(page_offset=...)` injects the offset block when
  the offset is positive.
- No block is rendered for offset 0 or negative values (noise guard).
- `NotesRunConfig` carries `page_offset` and falls back to the infopack.
- `create_notes_agent` accepts the keyword and threads it into the
  rendered system prompt.
- `run_listofnotes_subcoordinator` accepts the keyword so Sheet-12
  sub-agents get the same block.
"""
from __future__ import annotations

import inspect

import pytest

from notes.agent import create_notes_agent, render_notes_prompt
from notes.coordinator import NotesRunConfig
from notes.listofnotes_subcoordinator import (
    _invoke_sub_agent_once,
    _run_list_of_notes_sub_agent,
    run_listofnotes_subcoordinator,
)
from notes_types import NotesTemplateType
from scout.notes_discoverer import NoteInventoryEntry


def test_render_notes_prompt_injects_offset_block_when_positive():
    """Offset of +2 must surface in the rendered system prompt."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        page_offset=2,
    )
    # Section header landed.
    assert "PDF vs PRINTED PAGE OFFSET" in prompt
    # The numeric offset is stated.
    assert "+2" in prompt or "MINUS 2" in prompt


def test_render_notes_prompt_omits_offset_block_when_zero():
    """Offset of 0 (no cover/TOC) must NOT emit the block — otherwise
    the model gets an irrelevant 'offset of +0' nudge that dilutes the
    real guidance."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        page_offset=0,
    )
    assert "PDF vs PRINTED PAGE OFFSET" not in prompt


def test_render_notes_prompt_omits_offset_block_when_negative():
    """Defensive: a negative offset is nonsensical — we skip rather than
    cite something confusing."""
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        page_offset=-3,
    )
    assert "PDF vs PRINTED PAGE OFFSET" not in prompt


def test_notesrunconfig_defaults_page_offset_to_zero():
    """Backwards compatibility: existing callers that don't set the new
    field must still instantiate cleanly and produce offset=0."""
    cfg = NotesRunConfig(
        pdf_path="x.pdf",
        output_dir="/tmp/x",
        model="any-model",
    )
    assert cfg.page_offset == 0


def test_create_notes_agent_accepts_page_offset(tmp_path):
    """The factory signature must accept the new kwarg so the coordinator
    can forward it without raising a TypeError."""
    # We only need the signature check — constructing the full agent
    # requires a real model and template, which is out of scope here.
    sig = inspect.signature(create_notes_agent)
    assert "page_offset" in sig.parameters
    assert sig.parameters["page_offset"].default == 0


@pytest.mark.parametrize(
    "fn",
    [
        run_listofnotes_subcoordinator,
        _run_list_of_notes_sub_agent,
        _invoke_sub_agent_once,
    ],
)
def test_sheet12_signatures_accept_page_offset(fn):
    """All three layers of the Sheet-12 call chain must accept the
    keyword, otherwise the prompt block never reaches the sub-agent."""
    sig = inspect.signature(fn)
    assert "page_offset" in sig.parameters, f"{fn.__name__} missing page_offset"
    assert sig.parameters["page_offset"].default == 0
