"""Deterministic MFRS-vs-MPERS detector based on keyword scoring.

The scout runs this over whatever text it already has (TOC + first page or two
of front matter). We intentionally keep this pure and deterministic so the
Infopack scout-detection is cheap and reproducible; the UI toggle always wins
over this suggestion, so the cost of a false positive is low.

"unknown" is returned when:
- the text is empty / no keywords hit
- both standards score the same (ambiguous filing, e.g. a deck that references
  both frameworks in passing)
"""
from __future__ import annotations

import re
from typing import Literal

DetectedStandard = Literal["mfrs", "mpers", "unknown"]


# Keyword sets. Patterns are lowercase substrings — callers normalise the
# input text to lowercase before matching.
_MPERS_KEYWORDS: tuple[str, ...] = (
    "mpers",
    "malaysian private entities reporting standard",
    "private entities reporting standard",
    "statement of retained earnings",  # MPERS-specific face statement
)

_MFRS_KEYWORDS: tuple[str, ...] = (
    "mfrs",
    "malaysian financial reporting standard",
    "mfrs 101",
    "mfrs 9",
    "mfrs 15",
    "mfrs 16",
)


def _any_hit(text_lower: str, keywords: tuple[str, ...]) -> bool:
    """Return True if any keyword appears in the text."""
    return any(kw in text_lower for kw in keywords)


def detect_filing_standard(text: str) -> DetectedStandard:
    """Classify the extraction target as MFRS, MPERS, or unknown.

    Args:
        text: any front-matter text (TOC + title page is enough).

    Returns:
        "mpers" — only MPERS keywords appear.
        "mfrs" — only MFRS keywords appear.
        "unknown" — no keywords OR both frameworks appear (ambiguous filing).

    Presence-based semantics on purpose: a Malaysian filing's front-matter
    normally names one framework cleanly. Mentioning both is a caveat /
    cross-reference, and "unknown" lets the UI fall back to the user toggle
    rather than guessing.
    """
    if not text:
        return "unknown"
    lower = text.lower()
    has_mpers = _any_hit(lower, _MPERS_KEYWORDS)
    has_mfrs = _any_hit(lower, _MFRS_KEYWORDS)
    if has_mpers and has_mfrs:
        return "unknown"
    if has_mpers:
        return "mpers"
    if has_mfrs:
        return "mfrs"
    return "unknown"
