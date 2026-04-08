"""Tests for scout offset calibration and page validation.

Uses mocked LLM responses — no API key required.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from statement_types import StatementType
from scout.toc_parser import TocEntry
from scout.calibrator import (
    CalibrationResult,
    CalibratedPage,
    calibrate_pages,
    _build_search_window,
)


class TestBuildSearchWindow:
    """Search window generation around a stated page number."""

    def test_returns_stated_page_first(self):
        window = _build_search_window(42, pdf_length=100)
        assert window[0] == 42

    def test_includes_nearby_pages(self):
        window = _build_search_window(42, pdf_length=100)
        # Should include pages around 42
        assert 43 in window
        assert 41 in window

    def test_respects_pdf_bounds(self):
        window = _build_search_window(2, pdf_length=5)
        assert all(1 <= p <= 5 for p in window)

    def test_no_duplicates(self):
        window = _build_search_window(42, pdf_length=100)
        assert len(window) == len(set(window))

    def test_window_size_capped(self):
        window = _build_search_window(42, pdf_length=100)
        # Should not exceed ±10 range (21 pages max)
        assert len(window) <= 21


class TestCalibratedPage:
    """CalibratedPage data model."""

    def test_fields(self):
        cp = CalibratedPage(
            statement_type=StatementType.SOFP,
            stated_page=42,
            actual_page=48,
            offset=6,
            confidence="HIGH",
        )
        assert cp.actual_page == 48
        assert cp.offset == 6


class TestCalibration:
    """Integration-level calibration tests with mocked LLM."""

    @pytest.fixture
    def toc_entries(self) -> list[TocEntry]:
        return [
            TocEntry("Statement of Financial Position", StatementType.SOFP, 42),
            TocEntry("Statement of Profit or Loss", StatementType.SOPL, 44),
        ]

    @pytest.mark.asyncio
    async def test_calibrate_finds_correct_page(self, toc_entries):
        """When the LLM confirms a page, calibrator locks it."""
        # Mock: page 48 is SOFP (offset +6), page 50 is SOPL
        async def mock_validate(pdf_path, page_num, statement_name, model):
            if page_num == 48 and "financial position" in statement_name.lower():
                return {"found": True}
            if page_num == 50 and "profit or loss" in statement_name.lower():
                return {"found": True}
            return {"found": False}

        with patch("scout.calibrator._validate_page_via_llm", side_effect=mock_validate):
            result = await calibrate_pages(
                pdf_path=Path("/fake.pdf"),
                toc_entries=toc_entries,
                pdf_length=100,
                model="fake-model",
            )

        assert StatementType.SOFP in result.pages
        assert result.pages[StatementType.SOFP].actual_page == 48
        assert result.pages[StatementType.SOFP].offset == 6

    @pytest.mark.asyncio
    async def test_calibrate_rejects_false_positive(self, toc_entries):
        """When LLM says no for all candidates, confidence is LOW."""
        async def mock_validate(pdf_path, page_num, statement_name, model):
            return {"found": False}

        with patch("scout.calibrator._validate_page_via_llm", side_effect=mock_validate):
            result = await calibrate_pages(
                pdf_path=Path("/fake.pdf"),
                toc_entries=toc_entries,
                pdf_length=100,
                model="fake-model",
            )

        # SOFP should be marked LOW confidence (not found)
        assert result.pages[StatementType.SOFP].confidence == "LOW"

    @pytest.mark.asyncio
    async def test_calibrate_variable_offset(self):
        """Different statements can have different offsets."""
        entries = [
            TocEntry("Statement of Financial Position", StatementType.SOFP, 42),
            TocEntry("Statement of Cash Flows", StatementType.SOCF, 50),
        ]

        # SOFP offset +6, SOCF offset +8
        async def mock_validate(pdf_path, page_num, statement_name, model):
            if page_num == 48 and "financial position" in statement_name.lower():
                return {"found": True}
            if page_num == 58 and "cash flow" in statement_name.lower():
                return {"found": True}
            return {"found": False}

        with patch("scout.calibrator._validate_page_via_llm", side_effect=mock_validate):
            result = await calibrate_pages(
                pdf_path=Path("/fake.pdf"),
                toc_entries=entries,
                pdf_length=100,
                model="fake-model",
            )

        assert result.pages[StatementType.SOFP].offset == 6
        assert result.pages[StatementType.SOCF].offset == 8

    @pytest.mark.asyncio
    async def test_calibration_result_has_all_entries(self, toc_entries):
        """Every TOC entry with a statement_type gets a CalibrationResult entry."""
        async def mock_validate(pdf_path, page_num, statement_name, model):
            return {"found": False}

        with patch("scout.calibrator._validate_page_via_llm", side_effect=mock_validate):
            result = await calibrate_pages(
                pdf_path=Path("/fake.pdf"),
                toc_entries=toc_entries,
                pdf_length=100,
                model="fake-model",
            )

        assert StatementType.SOFP in result.pages
        assert StatementType.SOPL in result.pages
