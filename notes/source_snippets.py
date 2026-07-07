"""Locate the source HTML for a single note (notes source-formatting channel).

PLAN-word-input.md Phase 2, Step 8. When a Word file was uploaded, its body was
extracted to ``source.html`` (see ingest.docx_html). This module slices that
HTML into per-note chunks so a notes agent can fetch the real source formatting
for "Note 4" and mirror its table structure/styling.

This is a **navigation aid**, exactly like scout page hints (CLAUDE.md gotcha
#13): the code only *locates* a chunk by note number; the agent decides what to
do with it and verifies against the PDF. No deterministic label-matching enters
the notes pipeline — the note-heading detection here mirrors the existing
scout.notes_discoverer regex approach and is intentionally simple.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ingest.docx_html import SOURCE_HTML_NAME

# Cap the returned chunk so a runaway note (or a mis-detected boundary that
# swallows the rest of the document) can't blow up the agent's context. Applied
# to the rendered HTML string.
_SNIPPET_CHAR_CAP = 60_000
_TRUNCATION_MARKER = "\n<!-- [truncated — read the remaining pages in the PDF] -->"

# A block whose text begins a note. Two forms, gated on whether the block is a
# heading element:
#   - STRICT (prose blocks): optional "NOTE", the number, then PUNCTUATION
#     (. ) - :) then text. Requiring punctuation stops a prose paragraph that
#     opens with a bare number ("12 months ended 31 December 2024") from being
#     mistaken for a note boundary.
#   - LOOSE (heading tags <h1>-<h6>): the same, but a whitespace separator is
#     also allowed, so a Word-styled heading like "4 Property, plant and
#     equipment" (number, space, text — no punctuation) is found. Headings are
#     trustworthy, so the looser rule is safe there.
# Both anchored to the block start so "at 4.5% interest" mid-paragraph never
# triggers. Loose (LLM verifies) — mirrors scout.notes_discoverer._NOTE_REF_RE.
_NOTE_HEADING_STRICT_RE = re.compile(
    r"^\s*(?:note\s+)?(\d{1,3})\s*[\.\)\-:]\s*\S",
    re.IGNORECASE,
)
_NOTE_HEADING_LOOSE_RE = re.compile(
    r"^\s*(?:note\s+)?(\d{1,3})[\.\)\-:\s]+\S",
    re.IGNORECASE,
)
_HEADING_TAG_RE = re.compile(r"^\s*<h[1-6]\b", re.IGNORECASE)

# Top-level block tags mammoth emits. We split on these to find note
# boundaries; anything else rides inside its block. A regex can't balance
# nested tags (Malaysian FS tables use merged cells → mammoth emits nested
# <table>), so we scan with a depth counter instead — see _split_top_level_blocks.
_OPEN_BLOCK_RE = re.compile(r"<(p|h[1-6]|table|ul|ol)\b[^>]*>", re.IGNORECASE)

_TAG_RE = re.compile(r"<[^>]+>")


def _split_top_level_blocks(html: str) -> list[str]:
    """Return mammoth's top-level block elements as raw HTML strings.

    Depth-aware so a nested ``<table>`` (merged cells) is captured whole rather
    than truncated at the first inner ``</table>`` — the failure mode a
    non-greedy ``.*?`` regex has. Linear scan, no catastrophic backtracking.
    """
    blocks: list[str] = []
    pos = 0
    while True:
        m = _OPEN_BLOCK_RE.search(html, pos)
        if not m:
            break
        tag = m.group(1).lower()
        close_re = re.compile(rf"<(/?){re.escape(tag)}\b[^>]*>", re.IGNORECASE)
        depth = 1
        idx = m.end()
        while depth > 0:
            tm = close_re.search(html, idx)
            if not tm:
                idx = len(html)  # unbalanced source — take the rest
                break
            depth += -1 if tm.group(1) == "/" else 1
            idx = tm.end()
        blocks.append(html[m.start():idx])
        pos = idx
    return blocks


def source_html_path_for(pdf_path: str | Path) -> Path:
    """The conventional source.html location for a run: next to uploaded.pdf."""
    return Path(pdf_path).parent / SOURCE_HTML_NAME


def has_source_html(pdf_path: str | Path) -> bool:
    """True when a non-empty source.html sidecar exists for this run."""
    p = source_html_path_for(pdf_path)
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _block_text(block_html: str) -> str:
    """Plain text of a block, tags stripped and whitespace collapsed."""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", block_html)).strip()


def _heading_note_num(block_html: str) -> Optional[int]:
    """The note number a block starts, or None if it isn't a note heading.

    Heading tags (<h1>-<h6>) use the looser rule (a bare-whitespace separator
    after the number is allowed); other blocks require punctuation so prose that
    opens with a number isn't read as a boundary.
    """
    pat = (
        _NOTE_HEADING_LOOSE_RE
        if _HEADING_TAG_RE.match(block_html)
        else _NOTE_HEADING_STRICT_RE
    )
    m = pat.match(_block_text(block_html))
    return int(m.group(1)) if m else None


def extract_note_snippet(html: str, note_num: int) -> str:
    """Return the HTML for note ``note_num`` — from its heading block up to (but
    excluding) the next note heading — or "" if the note isn't found.

    Boundary detection keys on note headings only; content between two headings
    (prose, tables, lists) is returned verbatim so the agent sees the real
    source structure. Capped at ~60k chars.
    """
    if not html or note_num is None:
        return ""

    blocks = _split_top_level_blocks(html)
    if not blocks:
        return ""

    start: Optional[int] = None
    for i, block in enumerate(blocks):
        if _heading_note_num(block) == note_num:
            start = i
            break
    if start is None:
        return ""

    end = len(blocks)
    for j in range(start + 1, len(blocks)):
        nn = _heading_note_num(blocks[j])
        if nn is not None and nn != note_num:
            end = j
            break

    # Cap at a whole-block boundary so we never hand the agent a snippet cut
    # mid-tag. If the very first block already exceeds the cap, hard-cut it
    # (rare; a single pathological note) rather than return nothing.
    selected = blocks[start:end]
    out: list[str] = []
    total = 0
    truncated = False
    for b in selected:
        if out and total + len(b) > _SNIPPET_CHAR_CAP:
            truncated = True
            break
        out.append(b)
        total += len(b)
    if not out:  # first block alone over the cap
        return selected[0][:_SNIPPET_CHAR_CAP] + _TRUNCATION_MARKER
    snippet = "".join(out).strip()
    if truncated:
        snippet += _TRUNCATION_MARKER
    return snippet


def read_note_snippet(pdf_path: str | Path, note_num: int) -> str:
    """Read source.html for this run and return the chunk for ``note_num``.

    Returns "" when there is no sidecar or the note isn't found — the caller
    treats an empty result as "no source formatting available, read the PDF".
    """
    path = source_html_path_for(pdf_path)
    try:
        html = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return extract_note_snippet(html, note_num)
