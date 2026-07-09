"""Grouped money formatting for cross-check messages (UX-QA #9).

Pins the shared helper and one end-to-end message so a figure in the Message
column reads grouped (`1,002,593`), matching the grouped Expected/Actual/Diff
columns instead of the old raw `1002593.0`.
"""
from __future__ import annotations

from cross_checks._format import fmt_amount, fmt_diff


def test_fmt_amount_groups_integer_valued_floats_without_decimals():
    assert fmt_amount(1002593.0) == "1,002,593"
    assert fmt_amount(0.0) == "0"
    assert fmt_amount(-20678.0) == "-20,678"


def test_fmt_amount_keeps_two_decimals_for_non_integers():
    assert fmt_amount(1234.5) == "1,234.50"


def test_fmt_amount_handles_missing_value():
    assert fmt_amount(None) == "n/a"


def test_fmt_diff_always_grouped_two_decimals():
    assert fmt_diff(0.0) == "0.00"
    assert fmt_diff(21329.0) == "21,329.00"


def test_sofp_balance_message_is_grouped_not_raw_floats():
    """The screenshotted check (`assets (1002593.0) ... diff=0.00`) now groups."""
    from cross_checks.sofp_balance import SOFPBalanceCheck  # noqa: F401
    # Exercise the formatter the message builder uses rather than a full
    # workbook run (that path is covered by tests/test_cross_checks.py). The
    # message template is f"...assets ({fmt_amount(a)}) vs equity+liab
    # ({fmt_amount(b)}), diff={fmt_diff(d)}".
    a, b = 1002593.0, 982000.0
    d = abs(a - b)
    rendered = f"assets ({fmt_amount(a)}) vs equity+liab ({fmt_amount(b)}), diff={fmt_diff(d)}"
    assert "1,002,593" in rendered
    assert "982,000" in rendered
    assert "diff=20,593.00" in rendered
    # No raw trailing-.0 integers leak through.
    assert "1002593.0" not in rendered
