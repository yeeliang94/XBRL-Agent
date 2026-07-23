"""Reviewer-specific history compaction — plan agent-efficiency Phase 1A.

The reviewer registers NO history processors by default, so every page image
it views is re-billed verbatim on every later model request. The Step 0.2
inventory measured that as the dominant reviewer cost: 468 MB of page images
introduced across 17 passes, re-billed to 916 MB (2.0x overall; up to 8.5x
on heavy passes).

This module is deliberately NOT a copy of ``extraction/history_processors``.
Extraction's stripper keys on a domain event (first successful write) and
encodes the run-126 scanned-PDF thrash fixes for a loop that reads many
pages before writing anything. The reviewer's shape is different — it views
a page or two to verify a specific figure, acts, and moves on — so its rule
is positional, not event-driven:

    Keep the most recent ``KEEP_RECENT_IMAGE_BATCHES`` (2) view results
    verbatim; strip page images from older ones.

Two batches, not one, because a reviewer legitimately cross-references a
face page against a note page in consecutive calls. The median pass makes
exactly 2 view calls, so median passes see NO stripping — the saving lands
on the heavy tail, which is where the waste is.

Gated by ``XBRL_REVIEWER_COMPACT_CONTEXT`` (default OFF), read at agent
creation in ``create_reviewer_agent``. Flag off ⇒ the agent factory output
is byte-identical to today (pinned by test).

Purity contract (same as extraction's): never mutate the input messages —
rebuild changed parts with ``dataclasses.replace`` on copied lists. The
processed history IS what gets persisted to traces (gotcha #6); the
placeholder text says the page was seen in full when fresh.
"""

from __future__ import annotations

import dataclasses
import os
from typing import List

from pydantic_ai.messages import ModelMessage

# Generic pure utilities shared with extraction — message-shape helpers only,
# no extraction policy rides along.
from extraction.history_processors import (
    _nearest_page_number,
    _part_has_image,
    _replace_part,
    _tool_return_parts,
)

# How many most-recent image-bearing tool results stay verbatim. Two: the
# reviewer's cross-reference pattern is face page vs note page in consecutive
# calls.
KEEP_RECENT_IMAGE_BATCHES = 2


def reviewer_compact_context_enabled() -> bool:
    """``XBRL_REVIEWER_COMPACT_CONTEXT`` (default off), read at call time."""
    return os.environ.get(
        "XBRL_REVIEWER_COMPACT_CONTEXT", ""
    ).strip().lower() in ("1", "true", "yes", "on")


def strip_stale_reviewer_images(
    messages: List[ModelMessage],
) -> List[ModelMessage]:
    """Replace page images in all but the newest N view results.

    The placeholder keeps the ``=== Page N ===`` marker context and invites a
    re-view only when the reviewer genuinely must re-verify — unlike
    extraction's wording it cannot promise "the data is captured in the
    workbook", because the reviewer has no such write guarantee.
    """
    from pydantic_ai.messages import BinaryContent

    image_parts = [
        (mi, pi, part)
        for mi, pi, part in _tool_return_parts(messages)
        if _part_has_image(part)
    ]
    if len(image_parts) <= KEEP_RECENT_IMAGE_BATCHES:
        return messages

    protected = {mi for mi, _pi, _p in image_parts[-KEEP_RECENT_IMAGE_BATCHES:]}
    out = messages
    for mi, pi, part in image_parts:
        if mi in protected:
            continue
        content = part.content
        if not isinstance(content, list):
            continue
        new_content: List[object] = []
        for idx, item in enumerate(content):
            if isinstance(item, BinaryContent):
                page = _nearest_page_number(content, idx)
                new_content.append(
                    f"Page {page} was viewed in full on an earlier turn; the "
                    f"image was removed here to save cost. Call "
                    f"view_pdf_pages again ONLY if you must re-verify a "
                    f"figure on it."
                )
            else:
                new_content.append(item)
        new_part = dataclasses.replace(part, content=new_content)
        out = _replace_part(out, mi, pi, new_part)
    return out
