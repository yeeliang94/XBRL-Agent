"""Shared money formatting for cross-check messages (UX-QA #9).

The ValidatorTab already groups the Expected/Actual/Diff columns
(`1,002,593`), but the Message column printed raw floats
(`assets (1002593.0) ... diff=0.00`) — for a numbers-first audience the two
sit side by side and read as unfinished. One helper, one convention: grouped
thousands, integers without a trailing `.0`, non-integers to 2dp.
"""
from __future__ import annotations

from typing import Optional


def fmt_amount(value: Optional[float]) -> str:
    """Grouped, human-readable amount for a message string.

    - ``None`` → ``"n/a"`` (missing value; callers previously printed ``None``).
    - Integer-valued floats → grouped with no decimals (``1002593.0`` →
      ``"1,002,593"``).
    - Anything else → grouped to 2dp (``1234.5`` → ``"1,234.50"``).
    """
    if value is None:
        return "n/a"
    if float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def fmt_diff(value: float) -> str:
    """A difference, always grouped to 2dp so ``diff=`` keeps its fixed shape
    (``0.00``, ``21,329.00``) — only grouping is added vs the old ``:.2f``."""
    return f"{value:,.2f}"
