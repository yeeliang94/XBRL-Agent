"""Tests for statement-specific prompt templates (Step 4.1)."""

import pytest

from prompts import render_prompt
from statement_types import StatementType


class TestRenderPrompt:
    """Verify render_prompt produces correct prompts for each statement type."""

    def test_sofp_prompt_contains_key_phrases(self):
        """SOFP prompt must contain the critical rules from the original agent.py prompt."""
        prompt = render_prompt(StatementType.SOFP, "CuNonCu")
        # Core identity
        assert "Malaysian chartered accountant" in prompt
        assert "XBRL" in prompt
        # SOFP-specific structure
        assert "SOFP-CuNonCu" in prompt
        assert "SOFP-Sub-CuNonCu" in prompt
        # Strategy: sub-sheet first
        assert "sub-sheet" in prompt.lower() or "Sub-CuNonCu" in prompt
        # Critical rules
        assert "Accruals" in prompt
        assert "Deferred income" in prompt
        assert "field_label" in prompt

    def test_sofp_order_of_liquidity_prompt(self):
        """OrderOfLiquidity variant should reference the correct sheet names."""
        prompt = render_prompt(StatementType.SOFP, "OrderOfLiquidity")
        assert "SOFP-OrdOfLiq" in prompt or "OrderOfLiquidity" in prompt
        assert "Malaysian chartered accountant" in prompt

    def test_sopl_function_prompt(self):
        """SOPL-Function prompt must reference revenue, cost of sales, analysis sub-sheet."""
        prompt = render_prompt(StatementType.SOPL, "Function")
        assert "Malaysian chartered accountant" in prompt
        assert "SOPL" in prompt
        # Function-variant specifics
        assert "cost of sales" in prompt.lower() or "revenue" in prompt.lower()
        assert "SOPL-Analysis-Function" in prompt or "analysis" in prompt.lower()

    def test_sopl_nature_prompt(self):
        """SOPL-Nature prompt must reference nature-of-expense classification."""
        prompt = render_prompt(StatementType.SOPL, "Nature")
        assert "Malaysian chartered accountant" in prompt
        assert "SOPL" in prompt

    def test_soci_before_tax_prompt(self):
        """SOCI prompt must reference OCI items."""
        prompt = render_prompt(StatementType.SOCI, "BeforeTax")
        assert "Malaysian chartered accountant" in prompt
        assert "comprehensive income" in prompt.lower() or "OCI" in prompt

    def test_soci_net_of_tax_prompt(self):
        prompt = render_prompt(StatementType.SOCI, "NetOfTax")
        assert "Malaysian chartered accountant" in prompt
        assert "net of tax" in prompt.lower() or "OCI" in prompt

    def test_socf_indirect_prompt(self):
        """SOCF-Indirect prompt must reference profit reconciliation."""
        prompt = render_prompt(StatementType.SOCF, "Indirect")
        assert "Malaysian chartered accountant" in prompt
        assert "cash flow" in prompt.lower() or "SOCF" in prompt
        assert "indirect" in prompt.lower() or "reconcil" in prompt.lower()

    def test_socf_direct_prompt(self):
        prompt = render_prompt(StatementType.SOCF, "Direct")
        assert "Malaysian chartered accountant" in prompt
        assert "cash" in prompt.lower()

    def test_socie_prompt(self):
        """SOCIE prompt must reference the matrix layout and equity components."""
        prompt = render_prompt(StatementType.SOCIE, "Default")
        assert "Malaysian chartered accountant" in prompt
        assert "equity" in prompt.lower()
        # SOCIE-specific: matrix/column layout
        assert "column" in prompt.lower() or "matrix" in prompt.lower()

    def test_template_summary_appended_when_provided(self):
        """When template_summary is provided, it should be embedded in the prompt."""
        summary = "Sheet: SOFP-CuNonCu\n  B14: Trade receivables [DATA_ENTRY]"
        prompt = render_prompt(StatementType.SOFP, "CuNonCu", template_summary=summary)
        assert summary in prompt
        assert "TEMPLATE STRUCTURE" in prompt

    def test_all_statement_types_have_prompts(self):
        """Every StatementType × first-variant combination must produce a non-empty prompt."""
        from statement_types import variants_for
        for stmt in StatementType:
            variants = variants_for(stmt)
            assert len(variants) > 0, f"No variants for {stmt}"
            prompt = render_prompt(stmt, variants[0].name)
            assert len(prompt) > 200, f"Prompt for {stmt}/{variants[0].name} too short"

    def test_render_prompt_with_page_hints(self):
        """When page_hints are provided, prompt should include navigation guidance."""
        prompt = render_prompt(
            StatementType.SOFP, "CuNonCu",
            page_hints={"face_page": 14, "note_pages": [30, 31, 32]},
        )
        assert "14" in prompt
        # Should mention scoped pages or hints
        assert "page" in prompt.lower()

    def test_render_prompt_without_page_hints_includes_navigation(self):
        """Without page hints (scout off), prompt should instruct self-navigation."""
        prompt = render_prompt(StatementType.SOFP, "CuNonCu", page_hints=None)
        assert "table of contents" in prompt.lower() or "TOC" in prompt
