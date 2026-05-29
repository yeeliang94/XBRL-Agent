"""Phase 1b Step 13 — _render_inventory_preview shows the subnote tree.

Asserts:
- Top-level note format unchanged ("  Note N: Title (p.X)" / "(pp.X-Y)")
- Sub-notes render with the └ glyph and the literal subnote_ref
- Count line uses the top-level note count, not parent+sub total
- Notes without subnotes still render flat
- Empty inventory still produces the existing fallback message
"""
from __future__ import annotations

from scout.notes_discoverer import NoteInventoryEntry, SubNoteInventoryEntry
from notes.agent import _render_inventory_preview


def test_renders_subnotes_under_parent():
    inv = [
        NoteInventoryEntry(
            note_num=2,
            title="Significant accounting policies",
            page_range=(15, 22),
            subnotes=[
                SubNoteInventoryEntry(
                    subnote_ref="2.1",
                    title="Basis of preparation",
                    page_range=(15, 15),
                ),
                SubNoteInventoryEntry(
                    subnote_ref="2.14",
                    title="Employee benefits",
                    page_range=(20, 20),
                ),
            ],
        ),
        NoteInventoryEntry(
            note_num=4, title="PPE", page_range=(45, 47),
        ),
    ]
    out = _render_inventory_preview(inv)
    # Parent line format unchanged
    assert "Note 2: Significant accounting policies (pp.15-22)" in out
    assert "Note 4: PPE (pp.45-47)" in out
    # Sub-notes render with └ marker and literal ref
    assert "└ Note 2.1: Basis of preparation (p.15)" in out
    assert "└ Note 2.14: Employee benefits (p.20)" in out
    # Count uses top-level only — 2, not 4.
    assert out.startswith("Scout identified 2 notes in the PDF:")


def test_renders_flat_without_subnotes():
    inv = [
        NoteInventoryEntry(note_num=4, title="PPE", page_range=(45, 47)),
        NoteInventoryEntry(note_num=5, title="Receivables", page_range=(51, 51)),
    ]
    out = _render_inventory_preview(inv)
    # No └ glyphs, count is right, single-page form for note 5.
    assert "└" not in out
    assert "Scout identified 2 notes" in out
    assert "Note 5: Receivables (p.51)" in out


def test_alpha_subnotes_render():
    inv = [
        NoteInventoryEntry(
            note_num=18, title="Finance costs", page_range=(35, 36),
            subnotes=[
                SubNoteInventoryEntry(
                    subnote_ref="(a)",
                    title="Interest on term loans",
                    page_range=(35, 35),
                ),
                SubNoteInventoryEntry(
                    subnote_ref="(b)",
                    title="Interest on lease liabilities",
                    page_range=(35, 35),
                ),
            ],
        ),
    ]
    out = _render_inventory_preview(inv)
    assert "└ Note (a): Interest on term loans" in out
    assert "└ Note (b): Interest on lease liabilities" in out


def test_empty_inventory_preserves_fallback_message():
    out = _render_inventory_preview([])
    # The previous wording is the load-bearing one (tests + agent
    # prompts have learned to recognise it).
    assert "No notes inventory was provided" in out
