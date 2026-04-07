"""Tests for variant detection through the scout calibrator."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from statement_types import StatementType, VARIANTS
from scout.toc_parser import TocEntry
from scout.calibrator import calibrate_pages, CalibratedPage
from scout.variant_detector import detect_variant_from_signals


class TestDetectVariantFromSignals:
    """Deterministic variant detection using detection_signals from registry."""

    def test_sofp_cunoncu(self):
        page_text = """
        Statement of Financial Position
        Non-current assets
        Property, plant and equipment    Note 4    1,234,567
        Current assets
        Trade receivables    Note 5    384,375
        Non-current liabilities
        """
        result = detect_variant_from_signals(StatementType.SOFP, page_text)
        assert result == "CuNonCu"

    def test_sofp_order_of_liquidity(self):
        page_text = """
        Statement of Financial Position
        Assets
        Cash and cash equivalents    1,234,567
        Trade receivables    384,375
        Liabilities
        Equity
        """
        result = detect_variant_from_signals(StatementType.SOFP, page_text)
        assert result == "OrderOfLiquidity"

    def test_sopl_function(self):
        page_text = """
        Statement of Profit or Loss
        Revenue    10,000,000
        Cost of sales    (7,000,000)
        Distribution costs    (500,000)
        Administrative expenses    (1,000,000)
        """
        result = detect_variant_from_signals(StatementType.SOPL, page_text)
        assert result == "Function"

    def test_sopl_nature(self):
        page_text = """
        Statement of Profit or Loss
        Revenue    10,000,000
        Changes in inventories    (200,000)
        Raw materials    (3,000,000)
        Employee benefits expense    (2,000,000)
        """
        result = detect_variant_from_signals(StatementType.SOPL, page_text)
        assert result == "Nature"

    def test_socf_indirect(self):
        page_text = """
        Statement of Cash Flows
        Profit before tax    2,000,000
        Adjustments for:
        Depreciation    500,000
        """
        result = detect_variant_from_signals(StatementType.SOCF, page_text)
        assert result == "Indirect"

    def test_socf_direct(self):
        page_text = """
        Statement of Cash Flows
        Cash receipts from customers    10,000,000
        Cash paid to suppliers    (7,000,000)
        """
        result = detect_variant_from_signals(StatementType.SOCF, page_text)
        assert result == "Direct"

    def test_socie_default(self):
        """SOCIE only has one variant."""
        page_text = """
        Statement of Changes in Equity
        Share capital    Retained earnings    Total equity
        """
        result = detect_variant_from_signals(StatementType.SOCIE, page_text)
        assert result == "Default"

    def test_ambiguous_returns_first_variant(self):
        """When no signals match clearly, return first variant as fallback."""
        page_text = "Some random text with no signals"
        result = detect_variant_from_signals(StatementType.SOCI, page_text)
        # Should return something (first variant), not None
        assert result is not None

    def test_all_statement_types_have_variants(self):
        """Every StatementType should be detectable."""
        for st in StatementType:
            result = detect_variant_from_signals(st, "")
            assert result is not None


class TestCalibrationWithVariants:
    """Variant detection integrated into calibration flow."""

    @pytest.mark.asyncio
    async def test_calibrated_page_has_variant(self):
        entries = [
            TocEntry("Statement of Financial Position", StatementType.SOFP, 42),
        ]

        async def mock_validate(pdf_path, page_num, statement_name, model):
            if page_num == 42:
                return {"found": True, "variant_suggestion": "CuNonCu"}
            return {"found": False, "variant_suggestion": None}

        with patch("scout.calibrator._validate_page_via_llm", side_effect=mock_validate):
            result = await calibrate_pages(
                pdf_path=Path("/fake.pdf"),
                toc_entries=entries,
                pdf_length=100,
                model="fake-model",
            )

        assert result.pages[StatementType.SOFP].variant_suggestion == "CuNonCu"
