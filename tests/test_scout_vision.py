"""Tests for scout LLM vision helpers (mocked LLM)."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import fitz

from scout.vision import (
    extract_toc_via_vision,
    VisionTocResult,
    VisionTocEntry,
)
from scout.vision import _vision_entries_to_toc_entries
from statement_types import StatementType


@pytest.fixture
def simple_pdf(tmp_path: Path) -> Path:
    """A minimal PDF with a few pages for rendering."""
    doc = fitz.open()
    for _ in range(5):
        doc.new_page()
    path = tmp_path / "simple.pdf"
    doc.save(str(path))
    doc.close()
    return path


class TestVisionTocModels:
    """VisionTocEntry and VisionTocResult Pydantic models."""

    def test_vision_toc_entry(self):
        e = VisionTocEntry(statement_name="SOFP", stated_page=8)
        assert e.statement_name == "SOFP"
        assert e.stated_page == 8

    def test_vision_toc_result_empty(self):
        r = VisionTocResult(entries=[])
        assert r.entries == []

    def test_vision_toc_result_with_entries(self):
        r = VisionTocResult(entries=[
            VisionTocEntry(statement_name="SOFP", stated_page=8),
            VisionTocEntry(statement_name="SOPL", stated_page=10),
        ])
        assert len(r.entries) == 2


class TestVisionEntriesToTocEntries:
    """Converting VisionTocEntry list to TocEntry list."""

    def test_converts_standard_names(self):
        vision_entries = [
            VisionTocEntry(statement_name="Statement of Financial Position", stated_page=8),
            VisionTocEntry(statement_name="Statement of Profit or Loss", stated_page=10),
        ]
        toc_entries = _vision_entries_to_toc_entries(vision_entries)
        types = {e.statement_type for e in toc_entries if e.statement_type}
        assert StatementType.SOFP in types
        assert StatementType.SOPL in types

    def test_empty_returns_empty(self):
        assert _vision_entries_to_toc_entries([]) == []

    def test_preserves_page_numbers(self):
        vision_entries = [
            VisionTocEntry(statement_name="Statement of Cash Flows", stated_page=42),
        ]
        toc_entries = _vision_entries_to_toc_entries(vision_entries)
        socf = [e for e in toc_entries if e.statement_type == StatementType.SOCF]
        assert len(socf) == 1
        assert socf[0].stated_page == 42


class TestExtractTocViaVision:
    """extract_toc_via_vision with mocked PydanticAI agent."""

    @pytest.mark.asyncio
    async def test_returns_entries_from_llm(self, simple_pdf: Path):
        """Mocked LLM returns TOC entries for rendered pages."""
        mock_result = VisionTocResult(entries=[
            VisionTocEntry(statement_name="Statement of Financial Position", stated_page=8),
        ])

        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_agent_result)

        with patch("scout.vision.Agent", return_value=mock_agent):
            result = await extract_toc_via_vision(simple_pdf, [1, 2], model="fake")

        assert len(result.entries) == 1
        assert result.entries[0].statement_name == "Statement of Financial Position"

    @pytest.mark.asyncio
    async def test_empty_candidate_list_returns_empty(self, simple_pdf: Path):
        """Empty candidate page list → empty result without calling LLM."""
        result = await extract_toc_via_vision(simple_pdf, [], model="fake")
        assert result.entries == []
