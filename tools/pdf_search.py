"""Text-layer search over the source PDF (PLAN-orchestration-hardening item 19).

Extraction, notes, and reviewer agents page through the PDF one viewport at a
time when hunting for a phrase ("amounts owing by directors", a disputed
figure's source). This adds a single batched search tool over the PyMuPDF text
layer so an agent can locate the right pages in one call, then VERIFY by
viewing them (gotcha #13 — search is a navigation aid, never a page
restriction).

Two surfaces:
* :func:`search_pdf_text` — the pure function returning a structured dict.
* :func:`search_pdf_text_json` — the agent-tool wrapper returning a JSON
  string (the calculator / lookup_definitions convention).

Scanned PDFs have no text layer, so a naive search would return an empty list
the model could misread as "term absent". When the document yields no text at
all we instead return an explicit ``scanned`` signal telling the agent to
navigate with page images + scout hints. OCR indexing is explicitly out of
scope (owner decision).
"""
from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from typing import Optional

import fitz  # PyMuPDF

# Snippet window (chars) around each hit, and the default cap on returned hits.
_SNIPPET_LEN = 200
_DEFAULT_MAX_HITS = 20
# Input clamps: a runaway agent batching hundreds of queries (or pasting whole
# paragraphs as one "phrase") gets a clipped-but-useful result plus a
# structured note, never an unbounded scan.
_MAX_QUERIES = 20
_MAX_QUERY_LEN = 200
# Per-query note when the hit list was clipped to its allocation.
_CLIPPED_NOTE = (
    "clipped — re-search this phrase alone or with a more specific phrase"
)

# Extracting + lowercasing the whole text layer is the dominant cost of a
# search, and 3 agent types call this many times per run over the SAME
# 100-300 page PDF. Memoise (page_texts, page_lowers) per (path, mtime) so the
# layer is parsed once. Keyed on mtime so a reused path self-invalidates;
# bounded so concurrent runs over distinct PDFs can't grow it without limit.
# Mirrors the tools/page_cache.py design (bounded LRU, module singleton).
_TEXT_CACHE_MAX = 8
_text_cache: "OrderedDict[tuple[str, float], tuple[list[str], list[str]]]" = (
    OrderedDict()
)
_text_cache_lock = threading.Lock()


def _load_page_texts(pdf_path: str) -> tuple[list[str], list[str]]:
    """Return ``(page_texts, page_lowers)`` for the PDF, memoised per
    ``(path, mtime)``. Sync tools dispatch onto worker threads, so the cache is
    guarded by a lock (the critical section is O(1))."""
    try:
        key: Optional[tuple[str, float]] = (pdf_path, os.path.getmtime(pdf_path))
    except OSError:
        key = None  # let fitz.open raise the real, agent-readable error
    if key is not None:
        with _text_cache_lock:
            cached = _text_cache.get(key)
            if cached is not None:
                _text_cache.move_to_end(key)
                return cached
    doc = fitz.open(pdf_path)
    try:
        page_texts = [p.get_text("text") for p in doc]
    finally:
        doc.close()
    page_lowers = [t.lower() for t in page_texts]
    if key is not None:
        with _text_cache_lock:
            _text_cache[key] = (page_texts, page_lowers)
            _text_cache.move_to_end(key)
            while len(_text_cache) > _TEXT_CACHE_MAX:
                _text_cache.popitem(last=False)
    return page_texts, page_lowers


def _snippet_around(text: str, lo: int, hi: int) -> str:
    """A ≤``_SNIPPET_LEN`` snippet centred on ``[lo, hi)``, trimmed to word
    boundaries and collapsed to single spaces so it reads cleanly in the tool
    return."""
    pad = max(0, (_SNIPPET_LEN - (hi - lo)) // 2)
    start = max(0, lo - pad)
    end = min(len(text), hi + pad)
    # Snap to word boundaries when we're not at the document edge, so a snippet
    # doesn't begin/end mid-word.
    if start > 0:
        sp = text.find(" ", start)
        if sp != -1 and sp < lo:
            start = sp + 1
    if end < len(text):
        sp = text.rfind(" ", hi, end)
        if sp != -1:
            end = sp
    snippet = " ".join(text[start:end].split())
    return snippet[:_SNIPPET_LEN]


def search_pdf_text(
    pdf_path: str,
    queries: list[str],
    max_hits: int = _DEFAULT_MAX_HITS,
) -> dict:
    """Search the PDF text layer for one or more phrases, case-insensitively.

    Returns a dict::

        {
          "scanned": bool,             # True → no text layer; hits unavailable
          "message": str | None,       # set when scanned, else None
          "max_hits": int,
          "note": str | None,          # set when the QUERY LIST was clipped
          "results": [
            {"query": str, "total_matches": int,
             "hits": [{"page": int, "snippet": str}, ...],
             "note": str | None}       # set when hits clipped / query truncated
          ],
        }

    ``page`` is on the PDF-page scale the other tools use (1-based).
    ``max_hits`` is allocated PER QUERY — each query gets
    ``max(1, max_hits // len(queries))`` slots, so an early common term cannot
    starve later queries of the batch. ``total_matches`` always reports the
    true count per query; when a query's hit list was clipped to its
    allocation, its ``note`` says so and suggests re-searching that phrase
    alone. The query list is clamped to 20 entries and each query to 200
    chars, with structured notes when clipping occurs.
    """
    # Clamp inputs BEFORE opening the document so a degenerate call stays
    # cheap. Both clamps are surfaced as structured notes, never silent.
    list_note: Optional[str] = None
    if len(queries) > _MAX_QUERIES:
        list_note = (
            f"query list clipped to the first {_MAX_QUERIES} of "
            f"{len(queries)} queries — batch the rest in a follow-up call"
        )
        queries = queries[:_MAX_QUERIES]

    # Extract each page's text + lowercase form once (memoised per file).
    # "Scanned" is derived from the WHOLE document, not a front-matter sample:
    # a hybrid PDF (image-only cover/TOC, text-layer notes) must still search
    # its searchable pages rather than be written off as scanned (peer-review
    # MEDIUM).
    page_texts, page_lowers = _load_page_texts(pdf_path)

    if not any(t.strip() for t in page_texts):
        return {
            "scanned": True,
            "message": (
                "This document appears to be scanned (no text layer); text "
                "search is unavailable. Navigate with the page images and "
                "the scout's page hints instead."
            ),
            "max_hits": max_hits,
            "note": list_note,
            "results": [],
        }

    results: list[dict] = []
    # Per-query allocation — every query in the batch gets its own slot
    # budget, so an early common term ("the", "total") cannot consume the
    # whole cap and starve later, more specific queries.
    per_query = max(1, max(0, int(max_hits)) // max(1, len(queries)))
    for raw_q in queries:
        q = (raw_q or "").strip()
        entry: dict = {
            "query": raw_q, "total_matches": 0, "hits": [], "note": None,
        }
        query_notes: list[str] = []
        if len(q) > _MAX_QUERY_LEN:
            query_notes.append(
                f"query truncated to its first {_MAX_QUERY_LEN} chars — "
                f"search shorter, more specific phrases"
            )
            q = q[:_MAX_QUERY_LEN].strip()
        if not q:
            results.append(entry)
            continue
        needle = q.lower()
        total = 0
        remaining = per_query
        for page_idx, low in enumerate(page_lowers):
            start = 0
            while True:
                pos = low.find(needle, start)
                if pos == -1:
                    break
                total += 1
                if remaining > 0:
                    entry["hits"].append({
                        "page": page_idx + 1,
                        "snippet": _snippet_around(
                            page_texts[page_idx], pos, pos + len(needle)),
                    })
                    remaining -= 1
                start = pos + len(needle)
        entry["total_matches"] = total
        if total > len(entry["hits"]):
            query_notes.append(_CLIPPED_NOTE)
        if query_notes:
            entry["note"] = "; ".join(query_notes)
        results.append(entry)
    return {
        "scanned": False, "message": None,
        "max_hits": max_hits, "note": list_note, "results": results,
    }


def search_pdf_text_json(
    pdf_path: str,
    queries: list[str],
    max_hits: int = _DEFAULT_MAX_HITS,
) -> str:
    """Agent-tool wrapper: :func:`search_pdf_text` serialised to a JSON string.

    Never raises — a search failure (e.g. an unreadable PDF) is reported as a
    JSON ``error`` field so the agent reads one consistent contract and keeps
    going rather than dying on a tool exception.
    """
    try:
        return json.dumps(search_pdf_text(pdf_path, queries, max_hits))
    except Exception as exc:  # noqa: BLE001 — report, don't crash the agent loop
        return json.dumps({
            "scanned": False,
            "error": f"{type(exc).__name__}: {exc}",
            "results": [],
        })
