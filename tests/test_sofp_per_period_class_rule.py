"""Pin the per-period dedicated-class-row rule in the SOFP prompt (run-50).

The PPE misallocation in run-50 (Vehicles PY = 85,078 zeroed and folded into
the Office-equipment bucket while CY = 0) ties the sub-sheet total, so it is
invisible to verify_totals — the only defence is the agent reading the note's
CY and PY columns separately and filling the dedicated class row for BOTH
years. This pins that the SOFP prompt carries that per-period instruction so it
can't silently regress. The rule is generic to any sub-block with dedicated
class rows (PPE / intangibles / investments), not just Motor vehicles.
"""
from __future__ import annotations

from prompts import render_prompt
from statement_types import StatementType


def _sofp_prompt():
    return render_prompt(
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
        filing_level="company",
        filing_standard="mfrs",
    )


def test_prompt_requires_per_period_dedicated_class_fill():
    p = _sofp_prompt()
    low = p.lower()
    # The rule must explicitly span both periods, not just "fill the dedicated row".
    assert "per period" in low
    assert "cy and py" in low
    # It must warn that a misallocation ties the total (invisible to verify_totals).
    assert "ties" in low and "verify_totals" in p
    # And forbid zeroing a dedicated row in one year because it's zero in the other.
    assert "never zero a dedicated class row" in low


def test_prompt_keeps_existing_no_residual_plug_rule():
    """The per-period addition must not displace the existing no-plug rule."""
    p = _sofp_prompt()
    assert "NO-RESIDUAL-PLUG RULE" in p
    assert "Other property, plant and equipment" in p
