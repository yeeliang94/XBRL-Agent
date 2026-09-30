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

from bs4 import BeautifulSoup, Tag

from notes.html_sanitize import sanitize_notes_html
from notes.html_to_text import html_to_excel_text, rendered_length
from notes.source_models import ContentOrigin, INPUT_KIND_PREPARED, SourceBlock
from notes.writer import (
    CELL_CHAR_LIMIT,
    _strip_non_table_styles,
    _style_cell_html,
)

# Bump when the render changes shape, so a stored `source_rendered_sha256`
# from an older build is recognisably stale rather than silently compared.
RENDER_VERSION = "src-render-5"


def uses_subnote_sections(input_kind: str, render_version: str | None) -> bool:
    """Use the numbered-section shape only for current prepared-source cells."""
    return input_kind == INPUT_KIND_PREPARED and render_version == RENDER_VERSION

_TABLE_OPEN_RE = re.compile(r"<table\b[^>]*>", re.IGNORECASE)
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?:Note\s+)?(\d+(?:\.\d+)*)(?=\s|[.):]|$)", re.IGNORECASE,
)


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
    # A continued paragraph retains one logical paragraph. Fragment text
    # includes its verified boundary whitespace; do not invent or normalize it.
    from dataclasses import replace
    assembled = []
    positions = {}
    for block in blocks:
        prior_index = positions.get(block.continues_block_id)
        if prior_index is not None and block.block_kind == "paragraph":
            prior = assembled[prior_index]
            left = BeautifulSoup(prior.canonical_html, "html.parser")
            right = BeautifulSoup(block.canonical_html, "html.parser")
            lp, rp = left.find("p"), right.find("p")
            if lp is not None and rp is not None and len(left.find_all("p")) == len(right.find_all("p")) == 1:
                separator = (block.locator or {}).get("continuation_separator", "")
                if separator not in ("", " "):
                    raise BlockSelectionError("invalid verified continuation separator")
                if separator:
                    lp.append(separator)
                for child in list(rp.contents):
                    lp.append(child.extract())
                assembled[prior_index] = replace(prior, canonical_html=str(left))
                positions[block.block_id] = prior_index
                continue
        positions[block.block_id] = len(assembled)
        assembled.append(block)
    blocks = assembled
    out: list[str] = []
    pending_group: Optional[str] = None
    pending_parts: list[str] = []

    def flush() -> None:
        nonlocal pending_group, pending_parts
        if pending_parts:
            out.append(_merge_table_group(pending_parts))
        pending_group, pending_parts = None, []

    previous_note_id: Optional[str] = None
    for b in blocks:
        group = b.table_group_id
        if group and group == pending_group:
            pending_parts.append(b.canonical_html)
            continue
        flush()
        if (previous_note_id and b.source_note_id
                and b.source_note_id != previous_note_id):
            out.append("\n\n")
        if b.source_note_id:
            previous_note_id = b.source_note_id
        if group:
            pending_group, pending_parts = group, [b.canonical_html]
        else:
            html = b.canonical_html
            if (b.locator or {}).get("verified_title") and b.block_kind == "paragraph":
                title = BeautifulSoup(html, "html.parser")
                paragraph = title.find("p")
                if paragraph is not None and len(title.find_all("p")) == 1:
                    paragraph.name = "h3"
                    html = str(title)
            out.append(html)
    flush()
    return "".join(out)


def _wrap_numbered_subnotes(html: str) -> str:
    """Keep each prepared sub-note heading with its following source blocks."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('div[data-note-section="1"]'):
        return html
    root = soup.new_tag("div")
    parent_number: str | None = None
    sections: list[tuple[int, Tag]] = []
    for node in list(soup.contents):
        heading = (node if isinstance(node, Tag) and re.fullmatch(r"h[1-6]", node.name or "")
                   else None)
        match = _NUMBERED_HEADING_RE.match(heading.get_text(" ", strip=True)) if heading else None
        if match:
            number = match.group(1)
            if "." not in number:
                parent_number = number
                sections.clear()
            else:
                if parent_number is None:
                    parent_number = number.split(".", 1)[0]
                if not number.startswith(parent_number + "."):
                    sections.clear()
                    parent_number = number.split(".", 1)[0]
                depth = number.count(".")
                while sections and sections[-1][0] >= depth:
                    sections.pop()
                section = soup.new_tag("div", attrs={"data-note-section": "1"})
                (sections[-1][1] if sections else root).append(section)
                sections.append((depth, section))
        (sections[-1][1] if sections else root).append(node.extract())
    return root.decode_contents()


def render_blocks(
    available: Sequence[SourceBlock],
    block_ids: Iterable[str],
    *,
    format_ops: Optional[list] = None,
    row_label: str = "",
    cap: int = CELL_CHAR_LIMIT,
    wrap_subnotes: bool = False,
) -> RenderedCell:
    """Build one cell from the named blocks. Deterministic and total.

    Never raises for formatting reasons; raises only when a block id is not
    available, which is a contract violation rather than a quality problem.
    """
    chosen = select_blocks(available, block_ids)
    warnings: list[str] = []
    raw = _assemble(chosen)
    if wrap_subnotes:
        raw = _wrap_numbered_subnotes(raw)

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
        content_origin=(ContentOrigin.VISION_TRANSCRIBED
                        if any((b.locator or {}).get("capture_uncertain") or
                               (b.locator or {}).get("capture_method") == "reconstructed" for b in chosen)
                        else ContentOrigin.SOURCE_EXACT),
        source_rendered_sha256=render_sha256(styled),
        oversized=oversized,
        warnings=warnings,
    )


def render_sha256(html: str) -> str:
    """Plain content digest of a rendered cell.

    **The render version is deliberately NOT in this hash.** It used to be,
    which meant a source render and a human edit hashed the same bytes
    differently, so editing a cell back to exactly its source text could never
    clear the divergence mark (peer review, 2026-08-01). The version is stored
    beside the hash instead (`notes_cells.source_render_version`, v37), where a
    shape change is still visible without breaking the comparison.
    """
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def source_text_of(blocks: Sequence[SourceBlock]) -> str:
    """The plain text the given blocks carry, for comparing against a render."""
    return html_to_excel_text(_assemble(list(blocks)))
