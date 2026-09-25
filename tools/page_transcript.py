"""Page transcripts from document preparation, for agents on scanned PDFs.

Preparation already transcribes every page (``preparation.json``), but scanned
PDFs carry no text layer, so text search found nothing and agents read every
figure from page images. This module exposes that transcript:

* :func:`transcript_page_texts` — plain text per page, used by
  ``tools.pdf_search`` when the PDF itself has no text layer.
* :func:`read_page_text` — the agent tool body: the transcribed HTML of the
  requested pages, each labelled with its capture status.

The transcript is model-written and never certified exact (gotcha #31). Pages
the preparation step could only capture on a best-effort basis are labelled
with the recorded reasons so the agent confirms them against the page image.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

_ORIGINAL_NAME = "uploaded.pdf"
_MAX_PAGES_PER_CALL = 10
_CACHE_MAX = 8
_cache: "OrderedDict[tuple, Optional[dict[int, dict]]]" = OrderedDict()
_cache_lock = threading.Lock()


def _load(pdf_path: str) -> Optional[dict[int, dict]]:
    """``{page: {html, text, verified, reasons}}`` or None when unavailable.

    Uses the sanctioned ``read_prepared_document`` reader against the original
    upload, so only a succeeded preparation whose digests match is exposed.
    Memoised on the file signature (inode, size, mtime, ctime) of every input
    that reader checks — the metadata, the upload and the prepared outputs — so
    replacing any of them forces revalidation instead of a stale cache hit.
    """
    from ingest.document_preparation import (
        PREPARATION_NAME,
        _file_signature,
        read_prepared_document,
    )

    folder = Path(pdf_path).parent
    metadata = folder / PREPARATION_NAME
    original = folder / _ORIGINAL_NAME
    inputs = [metadata, *sorted(folder.glob("uploaded.*")), *sorted(folder.glob("prepared-*"))]
    try:
        key = (str(folder), tuple((path.name, _file_signature(path)) for path in inputs))
    except OSError:
        return None
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    prepared = read_prepared_document(original) if original.exists() else None
    pages: Optional[dict[int, dict]] = None
    if prepared is not None:
        pages = {}
        for page in prepared.pages:
            html = page.get("html") or ""
            reasons = [
                str(u.get("reason", "")).strip()
                for u in page.get("uncertainties") or []
                if isinstance(u, dict) and u.get("reason")
            ]
            pages[int(page["page"])] = {
                "html": html,
                "text": BeautifulSoup(html, "html.parser").get_text(" ", strip=True),
                "verified": page.get("verified") is True,
                "reasons": reasons,
            }
    with _cache_lock:
        _cache[key] = pages
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return pages


def transcript_available(pdf_path: Optional[str]) -> bool:
    return bool(pdf_path) and bool(_load(pdf_path))


def transcript_page_texts(pdf_path: str, page_count: int) -> Optional[list[str]]:
    """Plain transcript text for pages 1..page_count, or None when unavailable."""
    pages = _load(pdf_path)
    if not pages:
        return None
    return [pages.get(n, {}).get("text", "") for n in range(1, page_count + 1)]


def read_page_text(pdf_path: str, pages: list[int]) -> str:
    """Agent tool body: transcribed HTML for up to ten pages."""
    transcript = _load(pdf_path)
    if not transcript:
        return (
            "No page transcript is available for this document. Read the pages "
            "with view_pdf_pages instead."
        )
    requested = sorted({p for p in pages if isinstance(p, int)})
    parts: list[str] = []
    if len(requested) > _MAX_PAGES_PER_CALL:
        parts.append(
            f"Returned the first {_MAX_PAGES_PER_CALL} of {len(requested)} pages; "
            "request the rest in another call."
        )
        requested = requested[:_MAX_PAGES_PER_CALL]
    invalid = [p for p in requested if p not in transcript]
    if invalid:
        parts.append(
            f"Skipped page(s) {invalid}: valid pages are 1-{max(transcript)}."
        )
    for number in requested:
        page = transcript.get(number)
        if page is None:
            continue
        if page["verified"]:
            status = "transcript checked during preparation"
        else:
            reasons = "; ".join(page["reasons"]) or "capture uncertain"
            status = (
                "BEST-EFFORT transcript — confirm figures with view_pdf_pages "
                f"before writing. Reason: {reasons}"
            )
        parts.append(f"=== Page {number} ({status}) ===\n{page['html']}")
    return "\n\n".join(parts) if parts else "No pages were requested."
