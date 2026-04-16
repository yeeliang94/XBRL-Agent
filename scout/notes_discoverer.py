"""Note page discovery for the scout agent.

Extracts note references (e.g. "Note 4") from a statement's face page
text, then maps them to likely PDF page ranges using the TOC's notes
start page. For scanned PDFs, the face page text comes from LLM vision;
for text-based PDFs, from PyMuPDF.

A.2 adds a second, more precise pass: `build_notes_inventory` walks the
notes section page-by-page, splits it on note headers (e.g. "4. PROPERTY,
PLANT AND EQUIPMENT" or "NOTE 4 - REVENUE"), and returns a structured
inventory that downstream notes agents consume instead of raw page lists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
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


# ---------------------------------------------------------------------------
# Notes-inventory extraction (A.2)
# ---------------------------------------------------------------------------

@dataclass
class NoteInventoryEntry:
    """One note as discovered by walking the notes section of a PDF.

    `page_range` is inclusive on both ends (first-page, last-page).
    """
    note_num: int
    title: str
    page_range: tuple[int, int]


# Numbered heading: "4. PROPERTY, PLANT AND EQUIPMENT" (must be at start of
# a line; number + period + whitespace + uppercase-looking title).
_NUMBERED_HEADER_RE = re.compile(
    r"^\s*(\d{1,3})\.\s+([A-Z][A-Z0-9 ,/&()\-']{3,})$",
    re.MULTILINE,
)

# Prefixed heading: "NOTE 4 - REVENUE" or "Note 12: Finance costs".
_PREFIXED_HEADER_RE = re.compile(
    r"^\s*NOTES?\s+(\d{1,3})[\s\-:]+([^\n]{3,})$",
    re.MULTILINE | re.IGNORECASE,
)


def _detect_note_header(page_text: str) -> Optional[tuple[int, str]]:
    """Find the first note-header match on a page and return (num, title).

    Returns None if no header looks like a note boundary.
    """
    m = _PREFIXED_HEADER_RE.search(page_text)
    if m:
        return int(m.group(1)), _clean_title(m.group(2))
    m = _NUMBERED_HEADER_RE.search(page_text)
    if m:
        return int(m.group(1)), _clean_title(m.group(2))
    return None


def _clean_title(raw: str) -> str:
    t = raw.strip().strip(".:-").strip()
    # Normalise all-caps headers to Sentence case for readability, but keep
    # acronyms / mixed-case titles as-is.
    if t.isupper():
        t = t.capitalize()
    return t


def extract_inventory_from_pages(
    pages: list[tuple[int, str]],
) -> list[NoteInventoryEntry]:
    """Build an inventory from an ordered list of (page_num, text) tuples.

    Splitter: when a page contains a note-header match, it starts a new
    entry. Pages without a header extend the current entry's range.
    """
    entries: list[NoteInventoryEntry] = []
    current: Optional[NoteInventoryEntry] = None

    for page_num, text in pages:
        header = _detect_note_header(text)
        if header is not None:
            num, title = header
            if current is not None:
                entries.append(current)
            current = NoteInventoryEntry(
                note_num=num,
                title=title,
                page_range=(page_num, page_num),
            )
        elif current is not None:
            current = NoteInventoryEntry(
                note_num=current.note_num,
                title=current.title,
                page_range=(current.page_range[0], page_num),
            )
        # No header and no current: page precedes any note — skip.

    if current is not None:
        entries.append(current)
    return entries


def build_notes_inventory(
    pdf_path: str,
    notes_start_page: int,
    pdf_length: Optional[int] = None,
) -> list[NoteInventoryEntry]:
    """Walk the notes section of a PDF and return a structured inventory.

    For text-based PDFs this is a deterministic pass over PyMuPDF-extracted
    text. Scanned PDFs will yield an empty inventory here — the scout LLM
    must fall back to vision to populate `notes_inventory` in that case.

    Args:
        pdf_path: filesystem path to the PDF.
        notes_start_page: 1-indexed page where the Notes section begins.
        pdf_length: total pages in the PDF (optional; inferred from PyMuPDF).
    """
    import fitz  # local import — keeps test-only users off PyMuPDF

    doc = fitz.open(pdf_path)
    try:
        last_page = pdf_length if pdf_length is not None else len(doc)
        pages: list[tuple[int, str]] = []
        for pn in range(notes_start_page, last_page + 1):
            if 1 <= pn <= len(doc):
                pages.append((pn, doc[pn - 1].get_text()))
    finally:
        doc.close()

    return extract_inventory_from_pages(pages)
