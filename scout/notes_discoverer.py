"""Note page discovery for the scout agent.

Extracts note references (e.g. "Note 4") from a statement's face page
text, then maps them to likely PDF page ranges using the TOC's notes
start page. For scanned PDFs, the face page text comes from LLM vision;
for text-based PDFs, from PyMuPDF.
"""
from __future__ import annotations

import re
from typing import Optional

from scout.toc_parser import TocEntry

# Pattern to match note references: "Note 4", "(Note 4)", "Notes 5", "note 6"
_NOTE_REF_RE = re.compile(r"\bnotes?\s+(\d{1,3})\b", re.IGNORECASE)

# How many pages after notes_start_page to include per note reference.
# Malaysian annual reports typically have 2-4 pages per note.
_PAGES_PER_NOTE_ESTIMATE = 3

# Maximum pages to include in a note discovery result.
_MAX_NOTE_PAGES = 30


def extract_note_refs_from_text(text: str) -> list[int]:
    """Extract unique note reference numbers from statement text.

    Finds patterns like "Note 4", "(Note 4)", "Notes 5", "note 6".
    Returns sorted, deduplicated list of note numbers.
    """
    if not text:
        return []
    matches = _NOTE_REF_RE.findall(text)
    return sorted(set(int(m) for m in matches))


def find_note_page_ranges(
    note_refs: list[int],
    toc_entries: list[TocEntry],
    pdf_length: int,
    notes_start_page: Optional[int] = None,
) -> list[int]:
    """Map note reference numbers to likely PDF page ranges.

    Uses the notes_start_page (from TOC) as a base. If not available,
    tries to find it from toc_entries. If neither is available, returns
    empty (caller must handle this case — e.g. by having the sub-agent
    search for notes itself).

    Returns sorted, deduplicated list of 1-indexed page numbers.
    """
    if not note_refs:
        return []

    # Try to find notes start page from TOC entries if not provided
    if notes_start_page is None:
        for entry in toc_entries:
            name_lower = entry.statement_name.lower()
            if "note" in name_lower and ("financial" in name_lower or "statement" in name_lower):
                notes_start_page = entry.stated_page
                break

    if notes_start_page is None:
        return []

    # Generate a range of pages starting from notes_start_page.
    # We estimate ~3 pages per note, starting from the earliest note.
    # This is a heuristic — the sub-agent can refine by actually reading pages.
    min_note = min(note_refs)
    max_note = max(note_refs)

    # Rough estimate: note N starts at notes_start + (N - first_note) * pages_per_note
    pages: set[int] = set()
    for note_num in note_refs:
        estimated_offset = (note_num - min_note) * _PAGES_PER_NOTE_ESTIMATE
        start = notes_start_page + estimated_offset
        for p in range(start, start + _PAGES_PER_NOTE_ESTIMATE):
            if 1 <= p <= pdf_length:
                pages.add(p)

    # Cap at max to avoid sending too many pages
    result = sorted(pages)
    return result[:_MAX_NOTE_PAGES]


def discover_note_pages(
    face_page_text: str,
    toc_entries: list[TocEntry],
    pdf_length: int,
    notes_start_page: Optional[int] = None,
) -> list[int]:
    """End-to-end note discovery: extract refs from face page, map to pages.

    Args:
        face_page_text: text from the statement's face page (from OCR or PyMuPDF).
        toc_entries: all TOC entries (used to find notes start if not provided).
        pdf_length: total pages in the PDF.
        notes_start_page: if known, the page where notes begin.

    Returns:
        sorted list of 1-indexed PDF page numbers likely to contain notes
        for this statement. Empty if no note refs found or notes start unknown.
    """
    note_refs = extract_note_refs_from_text(face_page_text)
    if not note_refs:
        return []

    return find_note_page_ranges(
        note_refs=note_refs,
        toc_entries=toc_entries,
        pdf_length=pdf_length,
        notes_start_page=notes_start_page,
    )
