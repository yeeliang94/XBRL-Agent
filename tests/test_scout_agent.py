"""Tests for scout agent TOC entry extraction (LLM vision).

These tests use mocked LLM responses to avoid requiring API keys.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from statement_types import StatementType
from scout.toc_parser import TocEntry, parse_toc_entries_from_text


class TestTocEntry:
    """TocEntry data model."""

    def test_toc_entry_fields(self):
        entry = TocEntry(
            statement_name="Statement of Financial Position",
            statement_type=StatementType.SOFP,
            stated_page=42,
        )
        assert entry.statement_name == "Statement of Financial Position"
        assert entry.statement_type == StatementType.SOFP
        assert entry.stated_page == 42

    def test_toc_entry_optional_type(self):
        """statement_type can be None when the name doesn't match a known type."""
        entry = TocEntry(
            statement_name="Directors' Report",
            statement_type=None,
            stated_page=3,
        )
        assert entry.statement_type is None


class TestParseTocEntriesFromText:
    """Deterministic parsing when we have LLM-extracted text from TOC."""

    def test_extracts_all_five_statements(self):
        toc_text = """
Statement of Financial Position    8
Statement of Profit or Loss    10
Statement of Comprehensive Income    12
Statement of Cash Flows    14
Statement of Changes in Equity    16
Notes to the Financial Statements    18
"""
        entries = parse_toc_entries_from_text(toc_text)
        # Should find all 5 statement types
        found_types = {e.statement_type for e in entries if e.statement_type}
        assert StatementType.SOFP in found_types
        assert StatementType.SOPL in found_types
        assert StatementType.SOCI in found_types
        assert StatementType.SOCF in found_types
        assert StatementType.SOCIE in found_types

    def test_extracts_page_numbers(self):
        toc_text = """
Statement of Financial Position    42
Statement of Profit or Loss    44
"""
        entries = parse_toc_entries_from_text(toc_text)
        sofp = [e for e in entries if e.statement_type == StatementType.SOFP]
        assert len(sofp) == 1
        assert sofp[0].stated_page == 42

    def test_handles_dotted_lines(self):
        toc_text = """
Statement of Financial Position ...... 8
Statement of Profit or Loss ...... 10
"""
        entries = parse_toc_entries_from_text(toc_text)
        assert len(entries) >= 2

    def test_handles_malay_variants(self):
        """Malaysian annual reports may use Malay statement names."""
        toc_text = """
Penyata Kedudukan Kewangan    8
Penyata Untung Rugi    10
Penyata Pendapatan Komprehensif    12
Penyata Aliran Tunai    14
Penyata Perubahan Ekuiti    16
"""
        entries = parse_toc_entries_from_text(toc_text)
        found_types = {e.statement_type for e in entries if e.statement_type}
        # Should recognise at least some Malay statement names
        assert len(found_types) >= 3

    def test_multi_page_toc(self):
        """TOC text from multiple pages concatenated."""
        toc_text = """
Directors' Report    1
Statement of Financial Position    8
Statement of Profit or Loss    10
Statement of Comprehensive Income    12
Statement of Cash Flows    14
Statement of Changes in Equity    16
Notes to the Financial Statements    18
Independent Auditors' Report    25
"""
        entries = parse_toc_entries_from_text(toc_text)
        # Should find all 5 statements, plus possibly other entries
        found_types = {e.statement_type for e in entries if e.statement_type}
        assert len(found_types) == 5

    def test_empty_text_returns_empty(self):
        entries = parse_toc_entries_from_text("")
        assert entries == []
