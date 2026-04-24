"""Bug 5a — MPERS SOCIE prompt must use MPERS-native instructions, not MFRS.

Root cause: render_prompt() dispatches SOCIE-Default to `prompts/socie.md`
regardless of filing_standard. That file is MFRS-specific — it describes a
24-column matrix and hardcodes row numbers (6-25 CY, 30-49 PY) that only
exist in the MFRS template. On MPERS Company the template is a flat 2-col
layout with 24 rows; on MPERS Group the same labels repeat across four
vertical blocks. The MFRS row-coordinate instructions misfire on both.

Fix contract: a new `prompts/socie_mpers.md` takes precedence over `socie.md`
when filing_standard="mpers" (and the variant isn't a dedicated MPERS variant
like SoRE). These tests pin that behaviour.
"""
from __future__ import annotations

from prompts import render_prompt
from statement_types import StatementType


# ---------------------------------------------------------------------------
# MPERS — must NOT inherit MFRS matrix instructions
# ---------------------------------------------------------------------------

class TestMpersSocieDefaultPrompt:
    """MPERS SOCIE Default variant must use MPERS-native guidance."""

    def _render(self, filing_level: str = "company") -> str:
        return render_prompt(
            StatementType.SOCIE, variant="Default",
            filing_level=filing_level, filing_standard="mpers",
        )

    def test_does_not_hardcode_mfrs_row_ranges_on_group_filings(self):
        """S-1: Bug 5a affected both MPERS Company and MPERS Group (the
        latter has 4 vertical blocks at offsets 3-25/27-49/51-73/75-97 —
        the MFRS row ranges 6-25 / 30-49 don't align with either block
        cleanly). The MPERS prompt file is filing_level-agnostic, but
        the dispatch path is exercised per-level, so lock both paths."""
        prompt = self._render(filing_level="group")
        assert "Rows 6-25" not in prompt
        assert "Rows 30-49" not in prompt
        assert "rows 6-25" not in prompt.lower()
        assert "rows 30-49" not in prompt.lower()
        # Group path must still load the MPERS-native file, not the MFRS default.
        assert "mpers" in prompt.lower()

    def test_does_not_claim_matrix_template(self):
        prompt = self._render()
        # The MFRS SOCIE prompt explicitly calls the template a matrix —
        # that's wrong on MPERS. Any positive marker for matrix layout is
        # a regression.
        assert "matrix template" not in prompt.lower()
        assert "matrix layout" not in prompt.lower()

    def test_does_not_hardcode_mfrs_row_ranges(self):
        prompt = self._render()
        # These row ranges come from the MFRS SOCIE template layout
        # (rows 6-25 = CY, 30-49 = PY). On MPERS Company the template
        # ends at row 24; on MPERS Group the blocks are at different
        # offsets. Hardcoded MFRS ranges do not belong in the MPERS prompt.
        assert "Rows 6-25" not in prompt
        assert "Rows 30-49" not in prompt
        assert "rows 6-25" not in prompt.lower()
        assert "rows 30-49" not in prompt.lower()

    def test_does_not_reference_mfrs_equity_component_columns(self):
        prompt = self._render()
        # MFRS SOCIE has 24 columns (B-X) for equity components — Issued
        # capital, Retained earnings, various reserves, NCI, Total. None
        # of that exists on the flat MPERS template.
        assert "column X" not in prompt
        assert "Column X" not in prompt
        assert "columns B-X" not in prompt.lower()

    def test_instructs_field_label_matching(self):
        prompt = self._render()
        # Positive marker — agent should write by label, not by coordinate,
        # so a future template layout change doesn't drift the writes.
        assert "field_label" in prompt

    def test_mentions_mpers_explicitly(self):
        prompt = self._render()
        # Helps the agent anchor on the standard. Case-insensitive — the
        # prompt may say "MPERS" or "mpers" depending on phrasing.
        assert "mpers" in prompt.lower()


# ---------------------------------------------------------------------------
# MFRS — regression guard: existing MFRS prompt unchanged
# ---------------------------------------------------------------------------

class TestMfrsSocieDefaultPromptUnchanged:
    """MFRS SOCIE must still receive its MFRS-matrix instructions."""

    def _render(self, filing_level: str = "company") -> str:
        return render_prompt(
            StatementType.SOCIE, variant="Default",
            filing_level=filing_level, filing_standard="mfrs",
        )

    def test_still_describes_matrix_layout(self):
        prompt = self._render()
        # On MFRS the matrix description is correct and the agent relies
        # on it — pin it so the MPERS fix can't silently steal it.
        assert "matrix" in prompt.lower()

    def test_still_has_mfrs_row_ranges(self):
        prompt = self._render()
        # Matrix row ranges come from socie.md — MFRS template layout.
        # If these vanish, the MFRS SOCIE agent will lose its anchoring.
        assert "6-25" in prompt
        assert "30-49" in prompt


# ---------------------------------------------------------------------------
# SoRE variant — unchanged; the dedicated variant file wins over the
# filing-standard fallback.
# ---------------------------------------------------------------------------

class TestMpersSoreVariantStillWins:
    """MPERS + SOCIE + SoRE must still load socie_sore.md, not socie_mpers.md."""

    def test_sore_variant_loads_retained_earnings_prompt(self):
        prompt = render_prompt(
            StatementType.SOCIE, variant="SoRE",
            filing_level="company", filing_standard="mpers",
        )
        # socie_sore.md's title includes "Statement of Retained Earnings"
        assert "Retained Earnings" in prompt
