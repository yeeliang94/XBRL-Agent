"""Deterministic TOC page locator using PyMuPDF text extraction.

Scans the first N pages of a PDF for Table of Contents indicators:
1. Header keywords ("Table of Contents", "Contents")
2. Dotted-line page-reference patterns ("Statement of ... 42")

For scanned/image-only PDFs with no selectable text, falls back to
a heuristic range (pages 2-6) so the LLM vision agent in Step 3.3
can inspect them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import fitz

# How many pages from the start to scan for TOC candidates.
_SCAN_WINDOW = 15

# Header keywords that indicate a TOC page (case-insensitive).
_TOC_HEADER_KEYWORDS = [
    "table of contents",
    "contents",
]

# Pattern: a line that looks like a TOC entry — statement/section name
# followed by spaces/dots and a page number.
# e.g. "Statement of financial position ...... 42"
#      "Notes to the financial statements          48"
_TOC_LINE_RE = re.compile(
    r"(?:statement|note|report|director|audit)"  # common section starts
    r".{5,80}"                                    # section name (flexible)
    r"[\s.…]{3,}"                                 # dots / spaces separating name from number
    r"\d{1,4}\s*$",                               # trailing page number
    re.IGNORECASE | re.MULTILINE,
)

# Heuristic fallback pages for image-only PDFs (1-indexed, typical TOC range).
_HEURISTIC_PAGES = list(range(2, 7))  # pages 2-6


@dataclass
class TocCandidate:
    """A candidate TOC page with metadata about how it was found.

    Attributes:
        page_number: 1-indexed PDF page number.
        method: how the candidate was identified — "keyword", "pattern",
            or "heuristic" (image-only fallback).
        score: confidence score (higher = more likely to be the TOC).
            keyword match = 10, pattern match = count of matching lines,
            heuristic = 1.
    """
    page_number: int
    method: str
    score: float


def find_toc_candidate_pages(pdf_path: Path | str) -> list[TocCandidate]:
    """Scan the first pages of a PDF for likely Table of Contents pages.

    Returns candidates sorted by score (highest first). For image-only
    PDFs, returns a heuristic range so the LLM vision agent can inspect.
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    page_count = len(doc)
    scan_limit = min(_SCAN_WINDOW, page_count)

    candidates: list[TocCandidate] = []
    has_any_text = False

    for i in range(scan_limit):
        text = doc[i].get_text().strip()
        if not text:
            continue
        has_any_text = True
        page_num = i + 1  # 1-indexed

        lower = text.lower()

        # Check for TOC header keywords
        for keyword in _TOC_HEADER_KEYWORDS:
            if keyword in lower:
                candidates.append(TocCandidate(
                    page_number=page_num,
                    method="keyword",
                    score=10.0,
                ))
                break  # don't double-count keywords on same page

        # Check for dotted-line TOC entry patterns
        matches = _TOC_LINE_RE.findall(text)
        if len(matches) >= 2:
            # Multiple TOC-like lines = strong signal
            # Only add if not already added via keyword
            if not any(c.page_number == page_num for c in candidates):
                candidates.append(TocCandidate(
                    page_number=page_num,
                    method="pattern",
                    score=float(len(matches)),
                ))
            else:
                # Boost the existing keyword candidate's score
                for c in candidates:
                    if c.page_number == page_num:
                        c.score += float(len(matches))

    doc.close()

    # Fallback for image-only PDFs: return heuristic range
    if not has_any_text:
        for p in _HEURISTIC_PAGES:
            if p <= page_count:
                candidates.append(TocCandidate(
                    page_number=p,
                    method="heuristic",
                    score=1.0,
                ))

    # Sort by score descending
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates
