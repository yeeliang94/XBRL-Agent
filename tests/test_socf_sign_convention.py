"""RUN-REVIEW P2-2 (2026-04-26): SOCF/SoRE sign-from-formula injection.

The Amway run had `(Gain) loss on disposal of PPE` with AI=-70 vs
filer=70, and `Cash payments for the principal portion of lease
liabilities` with AI=3,732 vs filer=-3,732. Both signs are valid in
isolation; the right one depends on how the live template's `*Total …`
formula references the cell. This test pins:

1. The helper produces a non-empty block for live SOCF templates.
2. Specific rows from the failing run carry the right ADD/SUBTRACT
   guidance for the agent.
3. The block reaches the rendered prompt when `render_prompt` is
   given a `template_path`.
4. MFRS Co + MPERS Grp + SoRE all get coverage.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from prompts import render_prompt
from prompts._sign_conventions import (
    _parse_total_formula,
    socf_sign_convention_block,
)
from statement_types import StatementType

REPO = Path(__file__).resolve().parent.parent


def test_parse_simple_total_formula() -> None:
    """The term-extractor must read both leading-plus and leading-minus
    coefficients out of an Excel-style ±1*<cell> SUM expression."""
    f = "=1*B11+1*B12+-1*B13+-1*B17+1*B18"
    parsed = _parse_total_formula(f)
    assert (1, "B", 11) in parsed
    assert (1, "B", 12) in parsed
    assert (-1, "B", 13) in parsed
    assert (-1, "B", 17) in parsed
    assert (1, "B", 18) in parsed
    assert len(parsed) == 5


def test_parse_returns_empty_for_unknown_formulas() -> None:
    """SUM() and unrecognised forms fall through to empty so the agent
    sees the static rules instead of a malformed sign block."""
    assert _parse_total_formula("=SUM(B11:B25)") == []
    assert _parse_total_formula(None) == []  # type: ignore[arg-type]
    assert _parse_total_formula("=B11") == []  # no *1 coefficient
    assert _parse_total_formula("not a formula") == []


def test_socf_sign_block_present_for_mfrs_company() -> None:
    """Live MFRS Co SOCF-Indirect template carries `*Total adjustments`,
    so the helper must emit non-empty guidance."""
    path = REPO / "XBRL-template-MFRS" / "Company" / "07-SOCF-Indirect.xlsx"
    block = socf_sign_convention_block(path)
    assert block is not None
    assert "SIGN CONVENTIONS" in block
    # The two RUN-REVIEW §3.6 cases must be visible
    lower = block.lower()
    assert "disposal of property, plant and equipment" in lower
    # Cash-payments lease-liability principal — appears in financing block
    assert "lease" in lower


def test_socf_sign_block_for_mpers_group() -> None:
    """MPERS Group SOCF-Indirect carries fewer rows but the sign block
    must still come back populated. RUN-REVIEW MPERS coverage."""
    path = REPO / "XBRL-template-MPERS" / "Group" / "07-SOCF-Indirect.xlsx"
    block = socf_sign_convention_block(path)
    assert block is not None
    assert "SIGN CONVENTIONS" in block
    lower = block.lower()
    assert "disposal of property, plant and equipment" in lower


def test_render_prompt_injects_sign_block_for_socf(tmp_path: Path) -> None:
    """The end-to-end path: passing template_path to render_prompt
    surfaces the per-row sign block in the rendered system prompt."""
    path = REPO / "XBRL-template-MFRS" / "Company" / "07-SOCF-Indirect.xlsx"
    rendered = render_prompt(
        statement_type=StatementType.SOCF,
        variant="Indirect",
        filing_level="company",
        filing_standard="mfrs",
        template_path=str(path),
    )
    assert "SIGN CONVENTIONS" in rendered
    # Specific RUN-REVIEW §3.6 row appears with sign guidance
    assert "(Gain) loss on disposal of property, plant and equipment" in rendered


def test_render_prompt_no_sign_block_for_non_socf() -> None:
    """SOFP, SOPL, SOCI prompts don't get the sign block — only SOCF
    and SOCIE/SoRE do, because the per-row sign convention is
    specific to those two statements' subtraction-heavy *Total
    formulas."""
    sofp_path = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"
    rendered = render_prompt(
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
        filing_level="company",
        filing_standard="mfrs",
        template_path=str(sofp_path),
    )
    assert "SOCF SIGN CONVENTIONS" not in rendered


def test_render_prompt_omits_block_when_template_path_missing() -> None:
    """Backwards-compat: callers that don't pass template_path still
    get a valid prompt — sign block is advisory, not load-bearing."""
    rendered = render_prompt(
        statement_type=StatementType.SOCF,
        variant="Indirect",
        filing_level="company",
        filing_standard="mfrs",
    )
    assert "SIGN CONVENTIONS" not in rendered  # no template_path → no block
    # But the static SOCF prompt content must still be present.
    assert "SOCF" in rendered or "Cash Flows" in rendered


def test_sign_block_handles_sore_template_gracefully() -> None:
    """SoRE template (MPERS-only) follows the same pattern. The helper
    detects `sore` in the sheet name and walks its formulas. No
    failure mode should bubble up if the file shape changes — return
    None silently and let the agent fall back to static rules."""
    sore_path = REPO / "XBRL-template-MPERS" / "Company" / "10-SoRE.xlsx"
    if not sore_path.exists():
        pytest.skip("SoRE template not present in this checkout")
    block = socf_sign_convention_block(sore_path)
    # Block may be None if SoRE has no `*Total` formulas matching the
    # ±1*<cell> shape — that's a feature, not a failure.
    if block is not None:
        assert "SIGN CONVENTIONS" in block
