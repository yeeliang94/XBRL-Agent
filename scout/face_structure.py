"""Deterministic face-page structure parser.

Given the raw text PyMuPDF extracts from a financial statement's face page,
this module returns a list of ``FaceLineRef`` records — one per visible
line item — capturing the label, the cited disclosure-note number (if any),
and the section header the line sits under.

This is the **text-PDF fast path** for Phase 1a of the scout coverage push.
On scanned PDFs PyMuPDF returns no text, so ``read_face_structure`` returns
``[]`` and the scout LLM is expected to populate the structure via the
vision path (see ``scout.agent._SYSTEM_PROMPT``).

Soft advisory contract: downstream face agents are still required to verify
every line against the PDF — this parser produces hints, not facts. The
deterministic regex deliberately accepts only confident matches; ambiguous
lines roll up to whatever section is currently active rather than guessing
a fresh one.
"""
from __future__ import annotations

import re
from typing import Optional

from scout.infopack import FaceLineRef


# A line with a "Note N" cross-reference. Two shapes are common in Malaysian
# AFS face statements:
#   Property, plant and equipment   Note 4   1,234
#   Property, plant and equipment   4        1,234   (number column only)
# We anchor on "Note <num>" because the bare-number form collides with the
# value columns (which are also numbers). Bare-number references are left to
# the vision path — chasing them in regex produces too many false positives
# on the value columns.
_NOTE_REF_RE = re.compile(
    r"^(?P<label>[A-Za-z][^\n]*?)\s+Note\s+(?P<note>\d{1,3})\b",
    re.IGNORECASE,
)

# Section headers — short, no digits, no "Note N", typically all-caps or
# title-cased. Examples seen in real filings:
#   ASSETS / EQUITY AND LIABILITIES / Non-current assets / Current liabilities
# We accept anything that looks header-like: 1-5 words, optional trailing
# colon, no embedded "Note", no numeric value column.
_SECTION_HEADER_RE = re.compile(
    r"^[A-Z][A-Za-z][A-Za-z\s\-/&]{2,60}:?$",
)

# Numeric-only or pure-value lines we should ignore (column headers, totals
# emitted on their own line, page footers like "2022 RM '000").
_NUMERIC_NOISE_RE = re.compile(r"^[\s\d,.\(\)\-RM']+$")


def _looks_like_section_header(line: str) -> bool:
    """True if this line should switch the active section context.

    Section headers are short, header-shaped (no digits, no "Note"), and
    not the kind of line that carries a value. Total-rows ("Total assets")
    are intentionally NOT treated as section headers — they're terminal
    lines, not the start of a new block.
    """
    stripped = line.strip().rstrip(":").strip()
    if not stripped:
        return False
    if "Note" in stripped or "note" in stripped:
        return False
    # Reject lines that mention "Total" — those are line items, not headers
    if stripped.lower().startswith("total "):
        return False
    if not _SECTION_HEADER_RE.match(stripped):
        return False
    # Must be 1–5 words; long sentences aren't section headers
    word_count = len(stripped.split())
    if word_count < 1 or word_count > 5:
        return False
    return True


def _normalise_section(raw: str) -> str:
    """Lowercase + collapse whitespace; preserves the auditor's wording
    intent while making the section string stable for downstream prompts."""
    return " ".join(raw.strip().rstrip(":").strip().lower().split())


def read_face_structure(page_text: str) -> list[FaceLineRef]:
    """Parse a face-page's raw text into a list of FaceLineRef.

    Returns ``[]`` when ``page_text`` is empty (the explicit hand-off to
    the vision path on scanned PDFs).

    The parser walks the page line by line tracking the current section
    header. When a line matches the "<label> Note <num>" pattern it
    emits a FaceLineRef attaching the active section.

    Lines that don't match either a section header or a noted line item
    are skipped — they're either value-row continuations, page noise
    (column headers, footers), or un-noted line items (which the
    vision path may still surface). This conservative posture is
    deliberate: a noisy face-line map costs more in agent confusion
    than a sparse one costs in coverage.
    """
    if not page_text or not page_text.strip():
        return []

    refs: list[FaceLineRef] = []
    current_section: Optional[str] = None

    for raw_line in page_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Section header context-switch happens before line-item matching
        # so a header sitting on its own line correctly classifies the
        # items that follow it.
        if _looks_like_section_header(line):
            current_section = _normalise_section(line)
            continue

        # Skip pure-numeric noise lines (column headers, period rows).
        if _NUMERIC_NOISE_RE.match(line):
            continue

        m = _NOTE_REF_RE.match(line)
        if not m:
            continue

        label = m.group("label").strip()
        # Strip trailing punctuation an auditor might insert before "Note"
        label = label.rstrip(",;:").strip()
        if not label:
            continue
        try:
            note_num = int(m.group("note"))
        except ValueError:
            # Regex constrains to \d{1,3} so this can't realistically
            # fire, but cheap defensive code keeps the parser total.
            continue

        try:
            refs.append(FaceLineRef(
                label=label,
                note_num=note_num,
                section=current_section,
            ))
        except ValueError:
            # FaceLineRef rejects empty labels / non-positive notes; we
            # already filtered both, but keep the guard so a future
            # validation tightening doesn't crash the parser.
            continue

    return refs
