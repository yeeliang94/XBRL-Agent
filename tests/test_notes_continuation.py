"""PLAN §4 Phase E.2 — multi-page continuation prompt contract.

The notes agent has to keep reading past the inventory's stated end page
when a note's content spills over to subsequent pages. The prompt must
tell it to do so AND name a stop condition (the next note's header).

These tests pin the prompt language so a well-meaning future edit can't
silently drop the instruction. We assert on both the shared base prompt
(prompts/_notes_base.md) and each per-template prompt that relies on the
continuation behaviour.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from notes.agent import render_notes_prompt
from notes_types import NotesTemplateType
from scout.notes_discoverer import NoteInventoryEntry


_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _flatten(s: str) -> str:
    """Collapse whitespace so phrase assertions survive line breaks in the
    source prompt file."""
    return re.sub(r"\s+", " ", s).lower()


def test_base_prompt_has_multi_page_continuation_section():
    base = (_PROMPT_DIR / "_notes_base.md").read_text(encoding="utf-8")
    assert "MULTI-PAGE CONTINUATION" in base
    flat = _flatten(base)
    # Stop condition must be named — otherwise the agent will read until the
    # PDF ends or the model gets bored.
    assert "next note header" in flat or "next note's header" in flat
    # Positive instruction: keep going even if the inventory range is short.
    assert "do not stop" in flat


@pytest.mark.parametrize(
    "template_type",
    [
        NotesTemplateType.CORP_INFO,
        NotesTemplateType.ACC_POLICIES,
        NotesTemplateType.LIST_OF_NOTES,
        NotesTemplateType.ISSUED_CAPITAL,
        NotesTemplateType.RELATED_PARTY,
    ],
)
def test_rendered_prompt_includes_continuation_guidance(template_type):
    """Every rendered per-template prompt must inherit the base's
    continuation guidance — PLAN §4 E.2 stop-at-next-header rule."""
    prompt = render_notes_prompt(
        template_type=template_type,
        filing_level="company",
        inventory=[],
    )
    assert "MULTI-PAGE CONTINUATION" in prompt
    flat = _flatten(prompt)
    assert "next note header" in flat or "next note's header" in flat


def test_char_limit_footnote_present_in_base_prompt():
    """Agents must be told the 30K-char cap up front — the writer will
    truncate silently otherwise, and the agent's carefully-crafted prose
    gets clipped without any in-PDF signal to the reviewer."""
    base = (_PROMPT_DIR / "_notes_base.md").read_text(encoding="utf-8")
    assert "30,000" in base or "30000" in base
    assert "truncated" in base.lower()


def test_rendered_prompt_mentions_inventory_when_provided():
    """When scout provides an inventory, the prompt must surface the
    note-by-note list so the agent can cross-reference page ranges."""
    inventory = [
        NoteInventoryEntry(note_num=4, title="Revenue", page_range=(28, 30)),
        NoteInventoryEntry(note_num=5, title="Cost of sales", page_range=(31, 31)),
    ]
    prompt = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=inventory,
    )
    assert "Note 4: Revenue" in prompt
    assert "pp.28-30" in prompt
    assert "Note 5: Cost of sales" in prompt
