"""PydanticAI `history_processors` for token-cost reduction.

These are **pure functions over the model-message list** that run just before
each model call. They strip stale, re-billed payloads (old page images, the
repeated bulky template summary) out of the *outbound* request without touching
extraction logic, the in-memory conversation, or the saved traces.

Why this exists: agent runs re-send the entire conversation history on every
turn (no trimming). The dominant waste is old image blobs and the one-time
template summary being re-billed on every subsequent turn. See
`docs/Archive/PLAN-token-cost-reduction.md`.

Both processors are generic over tool name — scout's image tool is
`view_pages`, extraction/notes use `view_pdf_pages`, and `read_template`
is shared — so a single pair of processors covers all three subsystems.

Purity contract: the input message list is never mutated. PydanticAI message
parts are mutable dataclasses, so mutating them in place would also corrupt the
in-memory conversation and persisted traces, not just the outbound request.
Every changed part is rebuilt with `dataclasses.replace(...)` on copied lists.
"""

from __future__ import annotations

import dataclasses
import re
from typing import List

from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ToolReturnPart,
)

# Marker the image tools emit before each page image, e.g. "=== Page 12 ===".
_PAGE_MARKER_RE = re.compile(r"===\s*Page\s+(\d+)\s*===", re.IGNORECASE)

# The tools that commit extracted data to disk. Once one of these has
# succeeded, the workbook — not the page images — is the source of truth, so
# older images can be trimmed. Before the first successful write the agent is
# still reading and cross-referencing multiple pages, so images are kept whole.
# write_facts is the face-extraction write tool (rewrite Phase 3 renamed it
# from fill_workbook, which is kept here for back-compat with message
# histories recorded before the rename). write_notes is the notes write tool.
_WRITE_TOOL_NAMES = frozenset({"write_facts", "fill_workbook", "write_notes"})

# A write counts as a trimming boundary only when it actually COMMITTED data —
# matched by a success string carrying a non-zero count. The count matters, not
# just the prefix: notes/agent.py emits "Wrote 0 row(s) … Writer errors: …" and
# "Collected 0 payload(s) … Rejected …" on the *failure* path too (the prefix is
# unconditional), and fill_workbook's failure path returns "Failed to fill
# workbook. Errors: …". Gating on count >= 1 keeps a failed/no-op write from
# flipping the agent into post-write trimming and stripping the source pages it
# still needs to retry — the run_id=126 failure mode the stage-aware rule exists
# to prevent. A partial write ("Wrote 3 row(s) … Writer errors: 1 skipped") is
# correctly a boundary: 3 rows really landed. Coupled to the tool return strings
# in extraction/agent.py (fill_workbook) and notes/agent.py (write_notes /
# _sub_agent_sink_write); pinned by test_history_processors.py.
_WRITE_SUCCESS_PATTERNS = (
    re.compile(r"^Successfully wrote\s+(\d+)\s+field", re.IGNORECASE),
    re.compile(r"^Wrote\s+(\d+)\s+row", re.IGNORECASE),
    re.compile(r"^Collected\s+(\d+)\s+payload", re.IGNORECASE),
)

# read_template returns a summary that opens with this banner on its first
# sheet block. Used to recognise a template-summary tool return generically,
# without hard-coding the tool name.
_TEMPLATE_SUMMARY_MARKER = "=== Sheet:"


def _tool_return_parts(messages: List[ModelMessage]):
    """Yield (message_index, part_index, part) for every ToolReturnPart."""
    for mi, msg in enumerate(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for pi, part in enumerate(msg.parts):
            if isinstance(part, ToolReturnPart):
                yield mi, pi, part


def _part_has_image(part: ToolReturnPart) -> bool:
    """True if this tool return carries at least one image blob."""
    content = part.content
    if isinstance(content, list):
        return any(isinstance(item, BinaryContent) for item in content)
    return isinstance(content, BinaryContent)


def _nearest_page_number(content: List[object], image_index: int) -> str:
    """Find the page number from the nearest preceding `=== Page N ===` marker.

    The image tools always emit the text marker immediately before its image,
    so we scan backwards. Returns the digit string, or "?" if no marker found
    (keeps the placeholder useful even if the marker shape ever changes).
    """
    for j in range(image_index - 1, -1, -1):
        item = content[j]
        if isinstance(item, str):
            m = _PAGE_MARKER_RE.search(item)
            if m:
                return m.group(1)
    return "?"


def _replace_part(
    messages: List[ModelMessage],
    message_index: int,
    part_index: int,
    new_part: ToolReturnPart,
) -> List[ModelMessage]:
    """Return a new message list with one part swapped — no mutation in place."""
    out = list(messages)
    old_msg = out[message_index]
    new_parts = list(old_msg.parts)
    new_parts[part_index] = new_part
    out[message_index] = dataclasses.replace(old_msg, parts=new_parts)
    return out


def _part_text(part: ToolReturnPart) -> str:
    """The string portion of a tool return's content (joins list str items)."""
    content = part.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(item for item in content if isinstance(item, str))
    return ""


def _is_successful_write(part: ToolReturnPart) -> bool:
    """True only for a write tool return that actually committed >= 1 row/field.

    Gates on a success string with a non-zero count, not just the tool name — a
    failed or no-op write (malformed JSON, refused fill, "Wrote 0 row(s)",
    "Collected 0 payload(s)") must NOT count as a boundary, or its earlier
    source pages get stripped before the agent can retry.
    """
    if part.tool_name not in _WRITE_TOOL_NAMES:
        return False
    text = _part_text(part).lstrip()
    for pattern in _WRITE_SUCCESS_PATTERNS:
        m = pattern.match(text)
        if m and int(m.group(1)) >= 1:
            return True
    return False


def _last_write_message_index(messages: List[ModelMessage]) -> int | None:
    """Message index of the most recent successful write tool return, or None."""
    last: int | None = None
    for mi, _pi, part in _tool_return_parts(messages):
        if _is_successful_write(part):
            last = mi
    return last


def strip_stale_images(messages: List[ModelMessage]) -> List[ModelMessage]:
    """Trim re-billed page images that the agent no longer needs to *see*.

    The rule is stage-aware, not a fixed window — this is the fix for the
    run_id=126 regression where a one-batch window made multi-page agents
    thrash (re-fetching the same pages 10+ times and never writing):

    - **Before the first successful write** (`fill_workbook` / `write_notes`),
      keep *every* image. This is the discovery/extraction phase where the
      agent legitimately needs several pages visible at once to cross-reference
      them; stripping here is what caused the loop.
    - **After a write**, the workbook is the source of truth, so strip images
      from batches that *precede* the most recent write. The most recent image
      batch is always kept so the agent is never fully blinded, and any images
      viewed since the last write (the current fix cycle) are kept too.

    Stripped `BinaryContent` blobs become a one-line placeholder that preserves
    the `=== Page N ===` markers and actively discourages re-fetching.

    Generic over tool name: matches any `ToolReturnPart` whose content carries
    `BinaryContent`, so it covers `view_pdf_pages` (extraction/notes) and
    `view_pages` (scout).
    """
    image_parts = [
        (mi, pi, part)
        for mi, pi, part in _tool_return_parts(messages)
        if _part_has_image(part)
    ]
    if len(image_parts) <= 1:
        # Nothing stale yet — the only (or zero) image batch is the current one.
        return messages

    last_write_idx = _last_write_message_index(messages)
    if last_write_idx is None:
        # Discovery/extraction phase — no data committed yet. Keep all images
        # so the agent can hold multiple pages in view (run_id=126 fix).
        return messages

    # Protect the single most recent image batch regardless of where it sits.
    newest_image_idx = image_parts[-1][0]

    out = messages
    for mi, pi, part in image_parts:
        # Keep: the newest batch, and anything viewed at/after the last write
        # (the current fix cycle). Strip only images that predate the write.
        if mi >= last_write_idx or mi == newest_image_idx:
            continue
        content = part.content
        if not isinstance(content, list):
            continue
        new_content: List[object] = []
        for idx, item in enumerate(content):
            if isinstance(item, BinaryContent):
                page = _nearest_page_number(content, idx)
                new_content.append(
                    f"Page {page} was viewed earlier and its data is already "
                    f"captured in the workbook; do not re-open it just to "
                    f"refresh context."
                )
            else:
                new_content.append(item)
        new_part = dataclasses.replace(part, content=new_content)
        out = _replace_part(out, mi, pi, new_part)

    return out


def _is_template_summary(part: ToolReturnPart) -> bool:
    """True if this tool return looks like a read_template structure summary."""
    content = part.content
    if isinstance(content, str):
        return _TEMPLATE_SUMMARY_MARKER in content
    if isinstance(content, list):
        return any(
            isinstance(item, str) and _TEMPLATE_SUMMARY_MARKER in item
            for item in content
        )
    return False


def strip_duplicate_template(messages: List[ModelMessage]) -> List[ModelMessage]:
    """Collapse repeated read_template summaries to a one-line pointer.

    The ~12k-token template summary is otherwise re-billed on every turn once
    the agent has called `read_template` more than once. The first copy is kept
    intact; every later copy is replaced with a short pointer back to it. This
    is the per-turn token removal that works regardless of provider (caching is
    a separate, later concern).
    """
    summary_parts = [
        (mi, pi, part)
        for mi, pi, part in _tool_return_parts(messages)
        if _is_template_summary(part)
    ]
    if len(summary_parts) <= 1:
        return messages

    # Keep the first; replace the rest with a pointer.
    out = messages
    for mi, pi, _part in summary_parts[1:]:
        new_part = dataclasses.replace(
            _part, content="Template structure already provided above."
        )
        out = _replace_part(out, mi, pi, new_part)

    return out
