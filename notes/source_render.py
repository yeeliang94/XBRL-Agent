"""Build a notes cell from source blocks — plan Phase 6, Step 6.3.

The point of link-only mapping is that the agent chooses WHICH parts of the
source a note is made of, and ordinary code decides what the cell says. So this
module must be deterministic: the same block ids render the same bytes every
time, and the rendered text equals the source text.

Three rules it enforces:

* **Reading order wins.** Blocks render in the order they appear in the
  document, whatever order the agent named them in. An agent that reorders
  paragraphs is not selecting content, it is rewriting it.
* **Table groups rejoin.** A table Word split across a page break is one
  disclosure; rendering half of it and calling the note complete is the failure
  the group id exists to prevent.
* **Formatting never blocks content.** Invalid `format_ops` degrade the cell to
  plain and are reported, exactly as on the agent write path
  (docs/PLAN-notes-format-sidecar.md).

Oversized notes: a render above `CELL_CHAR_LIMIT` is REFUSED, not truncated.
Per the Step 0.6 decision, notes over the cap stay on the authoring path and
are flagged for review — a silently short cell that reports complete is the
false-green this feature exists to prevent.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from bs4 import BeautifulSoup

from notes.html_sanitize import sanitize_notes_html
from notes.html_to_text import html_to_excel_text, rendered_length
from notes.source_models import ContentOrigin, SourceBlock
from notes.writer import (
    CELL_CHAR_LIMIT,
    _strip_non_table_styles,
    _style_cell_html,
)

# Bump when the render changes shape, so a stored `source_rendered_sha256`
# from an older build is recognisably stale rather than silently compared.
RENDER_VERSION = "src-render-1"

_TABLE_OPEN_RE = re.compile(r"<table\b[^>]*>", re.IGNORECASE)


class BlockSelectionError(ValueError):
    """The agent named a block that is not available to it."""


@dataclass
class RenderedCell:
    html: str
    text: str
    rendered_chars: int
    block_ids: list[str]
    style_source: str
    content_origin: ContentOrigin
    source_rendered_sha256: str
    oversized: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """Whether this render may be written to a cell."""
        return not self.oversized and bool(self.html.strip())


def select_blocks(
    available: Sequence[SourceBlock], block_ids: Iterable[str]
) -> list[SourceBlock]:
    """Resolve ids to blocks, in READING ORDER, refusing anything unknown.

    Validating in code rather than trusting the agent is the whole contract:
    a fabricated id would create a cell whose content traces to nothing, and
    a duplicate would render the same paragraph twice.
    """
    by_id = {b.block_id: b for b in available}
    wanted = list(block_ids)
    unknown = [bid for bid in wanted if bid not in by_id]
    if unknown:
        raise BlockSelectionError(
            f"unknown block id(s): {', '.join(sorted(unknown))}. Use the ids "
            "returned by the source tools for this run."
        )
    seen: set[str] = set()
    chosen: list[SourceBlock] = []
    for bid in wanted:
        if bid in seen:
            continue
        seen.add(bid)
        chosen.append(by_id[bid])
    return sorted(chosen, key=lambda b: b.reading_order)


def _merge_table_group(parts: list[str]) -> str:
    """Concatenate the rows of several `<table>` fragments into one table.

    The first fragment's opening tag wins, so its `style=` / `colgroup` carry
    the group's shape. Anything outside a `<table>` in a later fragment is
    dropped — by construction a grouped block IS a table.
    """
    if len(parts) == 1:
        return parts[0]
    first = parts[0]
    m = _TABLE_OPEN_RE.search(first)
    if not m:
        return "".join(parts)
    open_tag = m.group(0)
    inner: list[str] = []
    for part in parts:
        soup = BeautifulSoup(part, "html.parser")
        table = soup.find("table")
        if table is None:
            inner.append(part)
            continue
        inner.append("".join(str(c) for c in table.contents))
    return f"{open_tag}{''.join(inner)}</table>"


def _assemble(blocks: Sequence[SourceBlock]) -> str:
    """Concatenate blocks, rejoining any table group into a single table."""
    out: list[str] = []
    pending_group: Optional[str] = None
    pending_parts: list[str] = []

    def flush() -> None:
        nonlocal pending_group, pending_parts
        if pending_parts:
            out.append(_merge_table_group(pending_parts))
        pending_group, pending_parts = None, []

    for b in blocks:
        group = b.table_group_id
        if group and group == pending_group:
            pending_parts.append(b.canonical_html)
            continue
        flush()
        if group:
            pending_group, pending_parts = group, [b.canonical_html]
        else:
            out.append(b.canonical_html)
    flush()
    return "".join(out)


def render_blocks(
    available: Sequence[SourceBlock],
    block_ids: Iterable[str],
    *,
    format_ops: Optional[list] = None,
    row_label: str = "",
    cap: int = CELL_CHAR_LIMIT,
) -> RenderedCell:
    """Build one cell from the named blocks. Deterministic and total.

    Never raises for formatting reasons; raises only when a block id is not
    available, which is a contract violation rather than a quality problem.
    """
    chosen = select_blocks(available, block_ids)
    warnings: list[str] = []
    raw = _assemble(chosen)

    cleaned, sanitizer_warnings = sanitize_notes_html(raw)
    warnings.extend(sanitizer_warnings)
    # Gotcha #16: table markup keeps its inline declarations verbatim; prose
    # does not. `_strip_non_table_styles` is the same gate the agent write path
    # uses, so a source-linked cell and an authored one obey one rule.
    cleaned = _strip_non_table_styles(cleaned)
    styled, style_source = _style_cell_html(cleaned, format_ops, row_label, warnings)

    text = html_to_excel_text(styled)
    length = rendered_length(styled)
    oversized = length > cap
    if oversized:
        warnings.append(
            f"{row_label or 'cell'}: the selected source runs to {length:,} "
            f"rendered characters, over the {cap:,} cell limit. It is left for "
            "the authoring path and flagged for review rather than cut short."
        )

    return RenderedCell(
        html=styled,
        text=text,
        rendered_chars=length,
        block_ids=[b.block_id for b in chosen],
        style_source=style_source,
        content_origin=ContentOrigin.SOURCE_EXACT,
        source_rendered_sha256=render_sha256(styled),
        oversized=oversized,
        warnings=warnings,
    )


def render_sha256(html: str) -> str:
    """Hash of a rendered cell, version-stamped.

    The version is inside the hash on purpose: a render-shape change must read
    as "different", not as a human edit that diverged from source.
    """
    return hashlib.sha256(
        f"{RENDER_VERSION}\n{html}".encode("utf-8")
    ).hexdigest()


def source_text_of(blocks: Sequence[SourceBlock]) -> str:
    """The plain text the given blocks carry, for comparing against a render."""
    return html_to_excel_text(_assemble(list(blocks)))
