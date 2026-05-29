"""Phase 1a Step 7 — prompt rendering of face_line_refs.

Covers:
- With non-empty face_line_refs → new block renders
- With empty face_line_refs → falls back to today's bare hint block
- The soft-advisory "VERIFY" framing is always present in the new block
- The base prompt rule about scout's map being a starting index, not a
  substitute, is present in every rendered prompt
"""
from __future__ import annotations

from statement_types import StatementType
from prompts import render_prompt
from prompts import _build_scoped_navigation, _render_face_line_refs_block


def test_block_renders_with_section_grouping():
    refs = [
        {"label": "Property, plant and equipment", "note_num": 4,
         "section": "non-current assets"},
        {"label": "Right-of-use assets", "note_num": 5,
         "section": "non-current assets"},
        {"label": "Trade receivables", "note_num": 7,
         "section": "current assets"},
    ]
    block = _render_face_line_refs_block(refs, face_read_in_detail=True)
    assert "FACE LINE → NOTE REFERENCES" in block
    assert "VERIFY against the PDF" in block
    assert "[non-current assets]" in block
    assert "[current assets]" in block
    assert "Property, plant and equipment → Note 4" in block
    assert "Trade receivables → Note 7" in block
    # Asserts the "jump straight to" wording present on read-in-detail
    assert "jump straight to" in block


def test_block_renders_low_confidence_wording_when_not_in_detail():
    refs = [{"label": "PPE", "note_num": 4, "section": None}]
    block = _render_face_line_refs_block(refs, face_read_in_detail=False)
    # Asserts the cautious wording present when scout didn't read in detail
    assert "starting hypothesis" in block.lower()
    assert "verify" in block.lower()


def test_empty_block_returns_empty_string():
    # Caller falls back to today's bare navigation block — no regression.
    assert _render_face_line_refs_block([], face_read_in_detail=False) == ""
    assert _render_face_line_refs_block([], face_read_in_detail=True) == ""


def test_unclassified_entries_render():
    # Lines without a section header (vision LLM didn't classify them)
    # still appear, under an [unclassified] heading.
    refs = [
        {"label": "Misc line", "note_num": 99, "section": None},
    ]
    block = _render_face_line_refs_block(refs, face_read_in_detail=True)
    # No [section] heading since none were provided; the line still shows
    assert "Misc line → Note 99" in block


def test_scoped_navigation_includes_block_when_refs_present():
    hints = {
        "face_page": 5,
        "note_pages": [10, 11],
        "face_line_refs": [
            {"label": "PPE", "note_num": 4, "section": "non-current assets"},
        ],
        "face_read_in_detail": True,
    }
    nav = _build_scoped_navigation(hints)
    # Existing block content still present
    assert "Face page: 5" in nav
    # New block appears
    assert "FACE LINE → NOTE REFERENCES" in nav
    assert "PPE → Note 4" in nav


def test_scoped_navigation_falls_back_when_refs_empty():
    hints = {
        "face_page": 5,
        "note_pages": [10, 11],
        "face_line_refs": [],
        "face_read_in_detail": False,
    }
    nav = _build_scoped_navigation(hints)
    assert "Face page: 5" in nav
    # No new block when there are no refs
    assert "FACE LINE → NOTE REFERENCES" not in nav


def test_scoped_navigation_works_without_new_keys_for_back_compat():
    # Older callers that didn't set face_line_refs / face_read_in_detail
    # at all must not break — Phase 1a additions are additive only.
    hints = {"face_page": 5, "note_pages": [10, 11]}
    nav = _build_scoped_navigation(hints)
    assert "Face page: 5" in nav
    assert "FACE LINE → NOTE REFERENCES" not in nav


def test_base_prompt_carries_advisory_rule():
    # Render a full prompt for any statement and assert the new
    # soft-advisory rule from _base.md is included.
    prompt = render_prompt(
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
    )
    # The rule's lead phrase should make it through the assembly
    assert "scout's face-line → note-references map" in prompt.lower()
    assert "starting index" in prompt.lower()


def test_full_prompt_with_hints_renders_face_refs_block():
    prompt = render_prompt(
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
        page_hints={
            "face_page": 5,
            "note_pages": [10, 11],
            "face_line_refs": [
                {"label": "PPE", "note_num": 4, "section": "non-current assets"},
            ],
            "face_read_in_detail": True,
        },
    )
    assert "FACE LINE → NOTE REFERENCES" in prompt
    assert "PPE → Note 4" in prompt
