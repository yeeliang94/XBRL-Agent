"""Tests for deterministic variant detection from page text signals."""
from __future__ import annotations

from statement_types import StatementType, VARIANTS
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
        Statement of Financial Position (Order of Liquidity)
        Total assets    5,000,000
        Total liabilities    3,000,000
        Cash and cash equivalents    1,234,567
        Trade receivables    384,375
        """
        result = detect_variant_from_signals(StatementType.SOFP, page_text)
        assert result == "OrderOfLiquidity"

    def test_sofp_cunoncu_not_confused_with_liquidity(self):
        """CuNonCu text should not match OrderOfLiquidity even though
        'assets' and 'liabilities' appear (those are no longer OoL signals)."""
        page_text = """
        Non-current assets
        Property, plant and equipment    1,000,000
        Current assets
        Cash    500,000
        Non-current liabilities
        Borrowings    300,000
        Total assets    2,000,000
        Total liabilities    800,000
        """
        result = detect_variant_from_signals(StatementType.SOFP, page_text)
        assert result == "CuNonCu"

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

    def test_no_signals_returns_none(self):
        """When no signals match, return None so caller decides fallback."""
        page_text = "Some random text with no signals"
        result = detect_variant_from_signals(StatementType.SOCI, page_text)
        assert result is None

    def test_sofp_absence_based_ool_detection(self):
        """SOFP with generic financial content but NO current/non-current
        headers should prefer OrderOfLiquidity via absence bonus."""
        page_text = """
        Statement of Financial Position
        Total assets    5,000,000
        Deposits from customers    2,000,000
        Loans and advances    1,500,000
        Total liabilities    3,000,000
        """
        result = detect_variant_from_signals(StatementType.SOFP, page_text)
        assert result == "OrderOfLiquidity"

    def test_empty_text_returns_none(self):
        """Empty text should return None (no evidence to pick a variant).

        Every statement now has at least two detectable variants — SOCIE
        gained the MPERS-only SoRE alongside Default, so there is no
        single-candidate shortcut anywhere. The scout's standard-aware
        selector (Phase 5.4 in docs/PLAN-mpers-pipeline-wiring.md) is the
        layer that will pick Default vs SoRE based on the detected filing
        standard; `detect_variant_from_signals` itself remains signal-driven.
        """
        for st in StatementType:
            result = detect_variant_from_signals(st, "")
            assert result is None, f"{st.value}: expected None, got {result!r}"


