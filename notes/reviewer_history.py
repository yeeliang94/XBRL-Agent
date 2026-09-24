"""Bound stale notes-reviewer read results without losing source locators.

Only old, read-only results are compacted. The newest two results of each tool
stay intact, as do every write acknowledgement and verification result. A
breadcrumb carries note/block/row references so the reviewer can re-read the
source before relying on its wording for a later edit.
"""
from __future__ import annotations

import dataclasses
import re
from collections import defaultdict

from pydantic_ai.messages import ModelMessage

from extraction.history_processors import (
    _model_responses_after,
    _part_text,
    _replace_part,
    _tool_return_parts,
)

_READ_TOOLS = frozenset({
    "read_source_manifest", "view_source_blocks", "read_note_cells",
})
_SOURCE_ID = re.compile(r"\bp\d+-b\d+-[A-Za-z0-9]+\b")
_NOTE = re.compile(r"\bNote\s+\d+(?:\.\d+)?\b", re.I)
_ROW = re.compile(r"\brow\s+\d+\b", re.I)


def compact_stale_notes_reads(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Replace substantial old reads with explicit, re-readable locators."""
    parts = [
        (mi, pi, part) for mi, pi, part in _tool_return_parts(messages)
        if part.tool_name in _READ_TOOLS and isinstance(part.content, str)
    ]
    recent: dict[str, set[tuple[int, int]]] = defaultdict(set)
    seen: dict[str, int] = defaultdict(int)
    for mi, pi, part in reversed(parts):
        if seen[part.tool_name] < 2:
            recent[part.tool_name].add((mi, pi))
        seen[part.tool_name] += 1

    edits = []
    reclaimed = 0
    for mi, pi, part in parts:
        body = _part_text(part)
        if ((mi, pi) in recent[part.tool_name]
                or _model_responses_after(messages, mi) < 3
                or len(body) < 2000):
            continue
        locators = list(dict.fromkeys(
            [*_NOTE.findall(body), *_ROW.findall(body), *_SOURCE_ID.findall(body)]
        ))[:100]
        first_line = body.strip().splitlines()[0][:160] if body.strip() else ""
        breadcrumb = (
            f"[{part.tool_name} read from an earlier turn, {len(body)} chars. "
            "Old wording was removed to save context. Re-read the named "
            "note, rows or block IDs before relying on it for a write. "
            f"First line: {first_line}. Locators: {', '.join(locators)}]"
        )
        edits.append((mi, pi, part, breadcrumb))
        reclaimed += len(body) - len(breadcrumb)
    if reclaimed < 12_000:
        return messages
    out = messages
    for mi, pi, part, breadcrumb in edits:
        out = _replace_part(out, mi, pi, dataclasses.replace(part, content=breadcrumb))
    return out
