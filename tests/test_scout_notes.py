"""Tests for scout note page discovery."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from statement_types import StatementType
from scout.toc_parser import TocEntry
from scout.notes_discoverer import (
    discover_note_pages,
    extract_note_refs_from_text,
    find_note_page_ranges,
)


class TestExtractNoteRefs:
    """Extract note references (e.g. 'Note 4', 'Note 12') from statement text."""

    def test_extracts_note_numbers(self):
        text = """
        Property, plant and equipment    Note 4    1,234,567
        Trade receivables    Note 5    384,375
        Cash and bank balances    Note 6    200,000
        """
        refs = extract_note_refs_from_text(text)
        assert 4 in refs
        assert 5 in refs
        assert 6 in refs

    def test_handles_note_variants(self):
        """'Note 4' and '(Note 4)' and 'Notes 4' all match."""
        text = """
        PPE (Note 4)    1,234
        Receivables Notes 5    384
        Cash note 6    200
        """
        refs = extract_note_refs_from_text(text)
        assert 4 in refs
        assert 5 in refs
        assert 6 in refs

    def test_empty_text(self):
        refs = extract_note_refs_from_text("")
        assert refs == []

    def test_no_notes(self):
        refs = extract_note_refs_from_text("Revenue    10,000,000")
        assert refs == []

    def test_deduplicates(self):
        text = "Note 4  Note 4  Note 5"
        refs = extract_note_refs_from_text(text)
        assert refs.count(4) == 1


class TestFindNotePageRanges:
    """Map note numbers to TOC page entries."""

    def test_finds_notes_in_toc(self):
        toc_entries = [
            TocEntry("Statement of Financial Position", StatementType.SOFP, 8),
            TocEntry("Notes to the Financial Statements", None, 18),
        ]
        # note_refs = [4, 5, 6]
        # With only one notes entry, all notes map to page 18+
        pages = find_note_page_ranges(
            note_refs=[4, 5, 6],
            toc_entries=toc_entries,
            pdf_length=50,
            notes_start_page=18,
        )
        assert len(pages) > 0
        assert all(p >= 18 for p in pages)

    def test_generates_range_after_notes_start(self):
        """Should generate a reasonable range of pages after the notes start."""
        pages = find_note_page_ranges(
            note_refs=[4, 5, 6],
            toc_entries=[],
            pdf_length=50,
            notes_start_page=18,
        )
        # Should have pages starting from 18
        assert 18 in pages
        assert all(1 <= p <= 50 for p in pages)

    def test_no_notes_start_returns_empty(self):
        """If we don't know where notes start, return empty."""
        pages = find_note_page_ranges(
            note_refs=[4],
            toc_entries=[],
            pdf_length=50,
            notes_start_page=None,
        )
        assert pages == []


class TestDiscoverNotePages:
    """End-to-end note discovery (mocked LLM for face page reading)."""

    @pytest.mark.asyncio
    async def test_basic_discovery(self):
        """Given face page text with note refs and a notes start page."""
        face_text = """
        Property, plant and equipment    Note 4    1,234,567
        Trade receivables    Note 5    384,375
        """
        toc_entries = [
            TocEntry("Notes to the Financial Statements", None, 18),
        ]

        result = discover_note_pages(
            face_page_text=face_text,
            toc_entries=toc_entries,
            pdf_length=50,
        )

        assert len(result) > 0
        assert all(p >= 1 for p in result)

    @pytest.mark.asyncio
    async def test_no_note_refs_returns_empty(self):
        """Face page with no note references."""
        result = discover_note_pages(
            face_page_text="Revenue    10,000,000",
            toc_entries=[],
            pdf_length=50,
        )
        assert result == []
