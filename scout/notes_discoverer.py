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

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from scout.toc_parser import TocEntry

logger = logging.getLogger(__name__)

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
class SubNoteInventoryEntry:
    """A sub-numbered heading nested under a top-level NoteInventoryEntry.

    Phase 1b — captures sub-note structure (2.1, 2.2, 2.14, (a), (b))
    for prompt-rendering context only. Sub-notes do NOT participate in
    Sheet-12 fan-out / coverage assignment — they're nested under their
    parent precisely to make that invariant structurally impossible to
    violate (see `tests/test_sheet12_ignores_subnotes.py`).

    ``subnote_ref`` is a string because sub-references take many shapes
    in real filings: numeric like "2.1" / "2.14" / "2.1.3", or alpha
    like "(a)" / "(b)(i)". Keeping it str avoids the type-coercion
    minefield that promoting ``note_num`` to str would have caused.
    """
    subnote_ref: str
    title: str
    page_range: tuple[int, int]

    def __post_init__(self) -> None:
        if not self.subnote_ref or not str(self.subnote_ref).strip():
            raise ValueError("SubNoteInventoryEntry.subnote_ref must be non-empty")
        if not isinstance(self.page_range, tuple) or len(self.page_range) != 2:
            raise ValueError(
                f"SubNoteInventoryEntry.page_range must be a 2-tuple, got {self.page_range!r}"
            )
        start, end = self.page_range
        if start < 1 or end < 1:
            raise ValueError(
                f"SubNoteInventoryEntry.page_range must be >= 1, got ({start}, {end})"
            )


@dataclass
class NoteInventoryEntry:
    """One note as discovered by walking the notes section of a PDF.

    `page_range` is inclusive on both ends (first-page, last-page).

    ``suggested_row_label`` (Phase 6.2) is an optional hint scout MAY
    populate with the Sheet-12 template row label this note most likely
    matches (e.g. note title "Cash and bank balances" → "Disclosure of
    cash and cash equivalents"). The LLM still makes the final match
    decision — the hint is purely soft guidance. Left as None by the
    current deterministic PyMuPDF-regex discoverer; future phases may
    populate it without changing the schema.

    ``subnotes`` (Phase 1b) is the nested list of sub-headings under
    this top-level note (e.g. Note 2 → 2.1, 2.2, … 2.14). Always
    iterate the top-level ``NoteInventoryEntry`` list for Sheet-12 fan-
    out / coverage / batching — sub-notes are display-only metadata
    used by the prompt renderer to give agents structural context.
    """
    note_num: int
    title: str
    page_range: tuple[int, int]
    suggested_row_label: Optional[str] = None
    subnotes: list = field(default_factory=list)


# Numbered heading: "4. PROPERTY, PLANT AND EQUIPMENT" or
# "4. Property, plant and equipment". Matches at start of line; number +
# period + whitespace + title that starts with a letter. Both ALL CAPS and
# Title Case are accepted because Malaysian annual reports use both.
_NUMBERED_HEADER_RE = re.compile(
    r"^\s*(\d{1,3})\.\s+([A-Za-z][A-Za-z0-9 ,/&()\-']{3,})$",
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


# Phase 1b — sub-numbered heading patterns. Used to detect rows like
# "2.1 Basis of preparation", "2.14 Employee benefits", or alpha forms
# like "(a) Short term benefits" that nest under the currently-active
# top-level note. The numeric form is required to start with a digit
# matching the parent note's number — that prevents "1.1" sub-notes
# leaking into a Note 3 that's currently active.
_NUMERIC_SUBNOTE_RE = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){1,3})\s+([A-Za-z][^\n]{2,120})$",
    re.MULTILINE,
)
# Alpha sub-section heading: "(a) Title" / "(b)(i) Title". Conservative —
# requires the parenthesised marker at the start of the line and at
# least one alpha character title.
_ALPHA_SUBNOTE_RE = re.compile(
    r"^\s*(\([a-z]+\)(?:\([ivx]+\))?)\s+([A-Za-z][^\n]{2,120})$",
    re.MULTILINE,
)


def _detect_subnotes_for_parent(
    page_text: str,
    parent_num: int,
    page_num: int,
) -> list[SubNoteInventoryEntry]:
    """Scan ``page_text`` for sub-numbered headings belonging to ``parent_num``.

    Returns sub-notes whose numeric ref starts with ``{parent_num}.`` plus
    any alpha-shaped sub-section headings on the page (those don't carry
    a parent-number indicator in their text, so we attach them to the
    currently-active parent on the assumption auditors don't span alpha
    sections across notes). Both lists are merged in document order.
    """
    subnotes: list[SubNoteInventoryEntry] = []
    parent_prefix = f"{parent_num}."

    # Walk both patterns and keep them in line-order so the prompt
    # renderer displays them as they appear on the page.
    matches: list[tuple[int, str, str]] = []
    for m in _NUMERIC_SUBNOTE_RE.finditer(page_text):
        ref = m.group(1)
        # Only accept refs that nest under the active parent. A "3.1"
        # appearing under Note 2's range is almost always the next
        # top-level note bleeding in via a layout artefact — leave it
        # to the top-level walker.
        if not ref.startswith(parent_prefix):
            continue
        matches.append((m.start(), ref, _clean_title(m.group(2))))
    for m in _ALPHA_SUBNOTE_RE.finditer(page_text):
        matches.append((m.start(), m.group(1), _clean_title(m.group(2))))

    matches.sort(key=lambda t: t[0])
    for _, ref, title in matches:
        try:
            subnotes.append(SubNoteInventoryEntry(
                subnote_ref=ref,
                title=title,
                page_range=(page_num, page_num),
            ))
        except ValueError:
            # Defensive: a future tightening of the validator must not
            # crash the whole inventory build.
            continue
    return subnotes


def extract_inventory_from_pages(
    pages: list[tuple[int, str]],
) -> list[NoteInventoryEntry]:
    """Build an inventory from an ordered list of (page_num, text) tuples.

    Splitter: when a page contains a note-header match, it starts a new
    entry. Pages without a header extend the current entry's range.

    Phase 1b — every page processed while a top-level note is active is
    also scanned for sub-numbered headings, which accumulate into the
    parent's ``subnotes`` list. Sheet-12 fan-out iterates only the
    top-level entries, so this is purely display-time enrichment.
    """
    entries: list[NoteInventoryEntry] = []
    current: Optional[NoteInventoryEntry] = None
    # Sub-notes accumulate on a side list because NoteInventoryEntry is
    # mutated below by re-construction — we splice them into the final
    # parent when it gets pushed onto ``entries``.
    current_subnotes: list[SubNoteInventoryEntry] = []

    def _commit(parent: NoteInventoryEntry) -> NoteInventoryEntry:
        """Attach accumulated subnotes to a parent before pushing."""
        if current_subnotes:
            parent.subnotes = list(current_subnotes)
        return parent

    for page_num, text in pages:
        header = _detect_note_header(text)
        if header is not None:
            num, title = header
            if current is not None:
                entries.append(_commit(current))
            current = NoteInventoryEntry(
                note_num=num,
                title=title,
                page_range=(page_num, page_num),
            )
            current_subnotes = _detect_subnotes_for_parent(text, num, page_num)
        elif current is not None:
            current = NoteInventoryEntry(
                note_num=current.note_num,
                title=current.title,
                page_range=(current.page_range[0], page_num),
            )
            # Look for more sub-notes on this continuation page; their
            # numeric refs must still nest under the parent.
            current_subnotes.extend(
                _detect_subnotes_for_parent(text, current.note_num, page_num)
            )
        # No header and no current: page precedes any note — skip.

    if current is not None:
        entries.append(_commit(current))
    return entries


def _resolve_vision_range(
    pdf_path: str,
    notes_start_page: int,
    pdf_length: Optional[int],
    notes_end_page: Optional[int],
) -> tuple[Optional[tuple[int, int]], list[tuple[int, str]]]:
    """Shared preamble for the sync + async entry points.

    Returns:
        - ``(start, end)`` for the vision fallback, or ``None`` if the
          range is out-of-bounds and the caller should short-circuit to
          ``[]``. The HIGH peer-review finding: a scout mis-offset that
          pushes ``notes_start_page`` past the last PDF page must never
          raise from ``_chunk`` — we turn it into a clean empty result
          with a warning log.
        - The PyMuPDF-extracted pages tuple-list ready for the regex
          fast path.
    """
    import fitz  # local import — keeps test-only users off PyMuPDF

    doc = fitz.open(pdf_path)
    try:
        total = len(doc)
        declared_end = pdf_length if pdf_length is not None else total
        # notes_end_page caps the vision scan to the true end of the
        # Notes section so the terminal note doesn't silently absorb
        # Directors' Statement / auditor's report pages (MEDIUM peer
        # review). If unset we fall back to declared_end = pdf_length,
        # which matches today's behaviour.
        vision_end = notes_end_page if notes_end_page is not None else declared_end
        # Clamp to the PyMuPDF-derived document length so a caller
        # passing an optimistic hint can't index past the PDF.
        vision_end = min(vision_end, total)

        # Bounds check FIRST — if the caller's start page is past the
        # effective end we short-circuit without paying for any
        # get_text() calls on pages we know we'll ignore. This matters
        # most on very long filings where the scout passes a wrong TOC
        # offset (peer-review perf finding).
        if notes_start_page > vision_end:
            logger.warning(
                "notes_start_page=%d exceeds effective end=%d for %s — returning empty "
                "inventory instead of crashing the vision fallback.",
                notes_start_page, vision_end, pdf_path,
            )
            return None, []

        pages: list[tuple[int, str]] = []
        # Fast path still reads up to declared_end (pdf_length hint or
        # document length) — it matters less because the regex pass
        # silently ignores pages with no header matches, so an over-wide
        # range is harmless. The vision range is the one that can burn
        # tokens on non-notes pages, which is why it gets the tighter
        # notes_end_page clamp.
        for pn in range(notes_start_page, declared_end + 1):
            if 1 <= pn <= total:
                pages.append((pn, doc[pn - 1].get_text()))
    finally:
        doc.close()

    return (notes_start_page, vision_end), pages


def build_notes_inventory(
    pdf_path: str,
    notes_start_page: int,
    pdf_length: Optional[int] = None,
    *,
    notes_end_page: Optional[int] = None,
    vision_model: Optional[object] = None,
    force_vision: bool = False,
) -> list[NoteInventoryEntry]:
    """Walk the notes section of a PDF and return a structured inventory.

    Fast path: a deterministic PyMuPDF-text pass that matches note headers
    with regex. Text-based PDFs always take this path — zero LLM cost.

    Fallback: when the fast path yields `[]` AND `vision_model` is
    provided, render the notes section to PNG and ask a PydanticAI
    vision agent to enumerate the headers. This keeps Sheet-12 fan-out
    working on scanned PDFs where PyMuPDF extracts no text. See
    `scout.notes_discoverer_vision` for the implementation.

    Passing `vision_model=None` (the default) preserves today's
    behaviour exactly — scanned PDFs return `[]` and the Sheet-12
    coordinator loud-fails, which is what every existing caller and
    test expects.

    Args:
        pdf_path: filesystem path to the PDF.
        notes_start_page: 1-indexed page where the Notes section begins.
        pdf_length: total pages in the PDF (optional; inferred from PyMuPDF).
        notes_end_page: optional 1-indexed last page of the Notes section.
            When set, the vision fallback only scans up to this page and
            the terminal note's last_page is clamped to it — preventing
            Directors' Statement / auditor's report pages from being
            absorbed into the final note. Callers that cannot compute
            this (e.g. without a TOC walk) can leave it unset; the
            stitcher then trusts the LLM's terminal last_page instead of
            stretching to pdf_length.
        vision_model: optional PydanticAI Model. Typed as `object` here
            to avoid a hard import cycle on pydantic_ai when this module
            is used from contexts that don't ship it.
        force_vision: operator escape hatch for scanned PDFs whose PyMuPDF
            output is not empty but is garbage (rare) — or simply to skip
            the regex pass when the caller knows the PDF has no usable
            text layer. Requires ``vision_model``; without it we log and
            fall back to the regex pass so a misconfigured run still
            returns the same result as today rather than silently empty.
    """
    vision_range, pages = _resolve_vision_range(
        pdf_path, notes_start_page, pdf_length, notes_end_page,
    )

    if force_vision and vision_model is None:
        logger.warning(
            "force_vision=True but no vision_model supplied — falling back "
            "to the regex pass for %s. Pass vision_model to actually skip regex.",
            pdf_path,
        )

    use_vision_directly = force_vision and vision_model is not None and vision_range is not None
    inventory = [] if use_vision_directly else extract_inventory_from_pages(pages)
    if inventory or vision_model is None or vision_range is None:
        return inventory

    # Fast path found nothing (or was skipped) and the caller supplied a
    # vision model — fall back to the PNG-rendered vision pass. Imported
    # lazily so the base module doesn't pull in pydantic_ai for pure
    # text-PDF callers.
    from scout.notes_discoverer_vision import _vision_inventory
    import asyncio

    start, end = vision_range

    # If we're already inside an async caller, we cannot call asyncio.run.
    # Expose a clear error in that case — the async caller should use the
    # async entry point `build_notes_inventory_async` instead.
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        raise RuntimeError(
            "build_notes_inventory called from a running event loop with "
            "vision_model set. Call build_notes_inventory_async instead."
        )

    return asyncio.run(_vision_inventory(
        pdf_path=pdf_path,
        start=start,
        end=end,
        model=vision_model,
    ))


async def build_notes_inventory_async(
    pdf_path: str,
    notes_start_page: int,
    pdf_length: Optional[int] = None,
    *,
    notes_end_page: Optional[int] = None,
    vision_model: Optional[object] = None,
    force_vision: bool = False,
) -> list[NoteInventoryEntry]:
    """Async sibling of `build_notes_inventory` for use from inside an
    event loop (e.g. PydanticAI tool callbacks, pytest-asyncio tests).

    Same contract: fast PyMuPDF path first, vision fallback only when
    the fast path is empty and `vision_model` is supplied. See the
    sync sibling's docstring for argument semantics (including
    ``notes_end_page`` and ``force_vision``).
    """
    entries, _source = await build_notes_inventory_with_source_async(
        pdf_path,
        notes_start_page,
        pdf_length,
        notes_end_page=notes_end_page,
        vision_model=vision_model,
        force_vision=force_vision,
    )
    return entries


async def build_notes_inventory_with_source_async(
    pdf_path: str,
    notes_start_page: int,
    pdf_length: Optional[int] = None,
    *,
    notes_end_page: Optional[int] = None,
    vision_model: Optional[object] = None,
    force_vision: bool = False,
) -> tuple[list[NoteInventoryEntry], str]:
    """Like ``build_notes_inventory_async`` but also reports HOW the inventory
    was built (source-honesty, rewrite Phase 6.3):

      * ``"text"``   — the deterministic PyMuPDF-regex fast path produced it.
      * ``"vision"`` — the LLM vision/OCR fallback path ran (scanned PDF, or
        ``force_vision``), regardless of whether it found anything (the point
        is recording that hidden OCR determinism was involved + cost incurred).
      * ``"none"``   — the regex pass found nothing and no vision path was
        available (no ``vision_model`` / no resolvable range).

    ``build_notes_inventory_async`` delegates here and drops the source, so the
    existing list-only contract (and all its callers/tests) is unchanged.
    """
    vision_range, pages = _resolve_vision_range(
        pdf_path, notes_start_page, pdf_length, notes_end_page,
    )

    if force_vision and vision_model is None:
        logger.warning(
            "force_vision=True but no vision_model supplied — falling back "
            "to the regex pass for %s. Pass vision_model to actually skip regex.",
            pdf_path,
        )

    use_vision_directly = force_vision and vision_model is not None and vision_range is not None
    if not use_vision_directly:
        text_inventory = extract_inventory_from_pages(pages)
        if text_inventory:
            return text_inventory, "text"
        if vision_model is None or vision_range is None:
            # Regex found nothing and there's no vision path to fall back to.
            return text_inventory, "none"

    from scout.notes_discoverer_vision import _vision_inventory

    start, end = vision_range
    entries = await _vision_inventory(
        pdf_path=pdf_path,
        start=start,
        end=end,
        model=vision_model,
    )
    # The vision path ran (forced or as the text-empty fallback). Record it as
    # "vision" even when empty — the method, not the yield, is what we report.
    return entries, "vision"
