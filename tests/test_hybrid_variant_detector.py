"""Tests for hybrid LLM + deterministic variant detection.

The hybrid detector uses an LLM as primary classifier (it understands
accounting semantics and handles noisy OCR), with the deterministic
signal scorer as a cross-check and fallback.

All LLM calls are mocked — these are unit tests, not live integration tests.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from statement_types import StatementType
from scout.variant_detector import (
    detect_variant,
    detect_variant_from_signals,
    VariantDetectionResult,
    _LlmVariantOutput,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_llm_output(variant: str, confident: bool = True, reasoning: str = "test"):
    """Build a mock LLM structured output."""
    return _LlmVariantOutput(
        variant=variant,
        confident=confident,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# Core hybrid scenarios
# ---------------------------------------------------------------------------

class TestHybridDetectVariant:
    """Async hybrid detect_variant() — LLM primary, deterministic cross-check."""

    @pytest.mark.asyncio
    async def test_llm_and_deterministic_agree_confident(self):
        """When both agree and LLM is confident → confident result."""
        page_text = """
        Non-current assets
        Property, plant and equipment    1,234,567
        Current assets
        Trade receivables    384,375
        Non-current liabilities
        Borrowings    500,000
        """
        with patch(
            "scout.variant_detector._classify_variant_via_llm",
            new_callable=AsyncMock,
            return_value=_mock_llm_output("CuNonCu", confident=True),
        ):
            result = await detect_variant(
                statement_type=StatementType.SOFP,
                page_text=page_text,
                pdf_path=Path("/fake.pdf"),
                page_num=42,
                model="fake-model",
            )
        assert result.variant == "CuNonCu"
        assert result.confident is True
        assert result.method == "hybrid"

    @pytest.mark.asyncio
    async def test_llm_and_deterministic_disagree_trust_llm(self):
        """When they disagree, LLM wins — it understands accounting semantics
        better than substring matching, especially with noisy OCR."""
        page_text = """
        Total assets    5,000,000
        non-current assets    (OCR noise — actually a footnote reference)
        """
        with patch(
            "scout.variant_detector._classify_variant_via_llm",
            new_callable=AsyncMock,
            return_value=_mock_llm_output("OrderOfLiquidity", confident=True),
        ):
            result = await detect_variant(
                statement_type=StatementType.SOFP,
                page_text=page_text,
                pdf_path=Path("/fake.pdf"),
                page_num=42,
                model="fake-model",
            )
        assert result.variant == "OrderOfLiquidity"
        # Disagreement → not confident
        assert result.confident is False
        assert result.method == "hybrid"

    @pytest.mark.asyncio
    async def test_llm_fails_falls_back_to_deterministic(self):
        """When LLM errors out, fall back to deterministic scorer."""
        page_text = """
        Profit before tax    2,000,000
        Adjustments for:
        Depreciation    500,000
        """
        with patch(
            "scout.variant_detector._classify_variant_via_llm",
            new_callable=AsyncMock,
            side_effect=Exception("API rate limit"),
        ):
            result = await detect_variant(
                statement_type=StatementType.SOCF,
                page_text=page_text,
                pdf_path=Path("/fake.pdf"),
                page_num=42,
                model="fake-model",
            )
        assert result.variant == "Indirect"
        assert result.confident is False
        assert result.method == "deterministic"

    @pytest.mark.asyncio
    async def test_llm_not_confident_deterministic_overrides(self):
        """When LLM is not confident and deterministic has a strong match,
        prefer the deterministic result."""
        page_text = """
        Cost of sales    (7,000,000)
        Distribution costs    (500,000)
        Administrative expenses    (1,000,000)
        """
        with patch(
            "scout.variant_detector._classify_variant_via_llm",
            new_callable=AsyncMock,
            return_value=_mock_llm_output("Nature", confident=False, reasoning="unclear"),
        ):
            result = await detect_variant(
                statement_type=StatementType.SOPL,
                page_text=page_text,
                pdf_path=Path("/fake.pdf"),
                page_num=42,
                model="fake-model",
            )
        # Deterministic clearly sees Function signals; LLM was uncertain
        assert result.variant == "Function"
        assert result.confident is False

    @pytest.mark.asyncio
    async def test_both_fail_returns_none(self):
        """When LLM fails AND deterministic has no signals → None."""
        with patch(
            "scout.variant_detector._classify_variant_via_llm",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            result = await detect_variant(
                statement_type=StatementType.SOCI,
                page_text="Random text with no signals at all",
                pdf_path=Path("/fake.pdf"),
                page_num=42,
                model="fake-model",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_single_variant_skips_llm(self):
        """SOCIE has only one detectable variant — no LLM call needed."""
        with patch(
            "scout.variant_detector._classify_variant_via_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            result = await detect_variant(
                statement_type=StatementType.SOCIE,
                page_text="Share capital   Retained earnings   Total equity",
                pdf_path=Path("/fake.pdf"),
                page_num=42,
                model="fake-model",
            )
        mock_llm.assert_not_called()
        assert result.variant == "Default"
        assert result.confident is True
        assert result.method == "deterministic"

    @pytest.mark.asyncio
    async def test_empty_text_skips_llm(self):
        """No page text → skip LLM, return None."""
        with patch(
            "scout.variant_detector._classify_variant_via_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            result = await detect_variant(
                statement_type=StatementType.SOFP,
                page_text="",
                pdf_path=Path("/fake.pdf"),
                page_num=42,
                model="fake-model",
            )
        mock_llm.assert_not_called()
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_returns_invalid_variant_ignored(self):
        """If LLM hallucinates a variant name not in the registry, ignore it
        and fall back to deterministic."""
        page_text = """
        Cash receipts from customers    10,000,000
        Cash paid to suppliers    (7,000,000)
        """
        with patch(
            "scout.variant_detector._classify_variant_via_llm",
            new_callable=AsyncMock,
            return_value=_mock_llm_output("SemiDirect", confident=True),
        ):
            result = await detect_variant(
                statement_type=StatementType.SOCF,
                page_text=page_text,
                pdf_path=Path("/fake.pdf"),
                page_num=42,
                model="fake-model",
            )
        assert result.variant == "Direct"
        assert result.method == "deterministic"


# ---------------------------------------------------------------------------
# Deterministic layer (existing tests kept working)
# ---------------------------------------------------------------------------

class TestDeterministicDetectVariantFromSignals:
    """The deterministic scorer still works standalone."""

    def test_sofp_cunoncu(self):
        page_text = "Non-current assets\nCurrent assets\nNon-current liabilities"
        assert detect_variant_from_signals(StatementType.SOFP, page_text) == "CuNonCu"

    def test_empty_returns_none(self):
        assert detect_variant_from_signals(StatementType.SOFP, "") is None

    def test_no_match_returns_none(self):
        assert detect_variant_from_signals(StatementType.SOCI, "random text") is None


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class TestVariantDetectionResult:
    """The result type has the expected fields."""

    def test_fields(self):
        r = VariantDetectionResult(variant="CuNonCu", confident=True, method="hybrid")
        assert r.variant == "CuNonCu"
        assert r.confident is True
        assert r.method == "hybrid"

    def test_defaults(self):
        r = VariantDetectionResult(variant="Function", confident=False, method="deterministic")
        assert r.confident is False
