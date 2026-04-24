"""HTML → Excel-plaintext renderer for the notes rich-editor pipeline.

Every notes cell is authored as HTML (per docs/PLAN-NOTES-RICH-EDITOR.md).
Excel does not preserve HTML formatting when pasted from a cell, so the
canonical payload lives in the DB and the download path flattens HTML to
plain text at write time. This module is the flattener.

Rules (locked by test_notes_html_to_text.py):

  - <p>        → block; separated by a blank line from surrounding blocks.
  - <br>       → single newline (soft break inside a block).
  - <ul>/<ol>  → one bullet per line; `- item` for unordered, `1. item`
                  (1-indexed) for ordered.
  - <table>    → each row joined with ` | `, rows joined with `\n`.
                  Nested tables render in-place inside their parent cell.
  - <h1>-<h6>  → standalone block; same spacing as a paragraph.
  - Inline tags (<b>/<strong>/<em>/<i>/...) contribute only their text.
  - Unknown/unsafe tags simply have their text extracted.
  - Missing / empty input returns "".

Pure functions; no imports from other notes modules so it can be reused
by the DB-download path and the post-run editor layer without coupling.

**Drift watch (peer-review #11):** the frontend clipboard copy in
``web/src/lib/clipboard.ts`` implements a near-identical flattener for
the "Copy rich text" button. The two surfaces serve different targets —
Excel cells vs M-Tool paste — so their whitespace handling is
intentionally different (JS collapses inline ``\\s+``; Python preserves
verbatim). If you change either flattener, check the other and the
fixture list in ``tests/test_notes_html_to_text.py`` /
``web/src/__tests__/clipboard.test.ts`` to see whether the divergence
is still intentional.
"""
from __future__ import annotations

from typing import Iterable, Optional

from bs4 import BeautifulSoup, NavigableString, Tag


# Tag buckets that drive block-level spacing. Keeping them as module-level
# frozensets makes the render loop cheap and documents the contract.
_BLOCK_TAGS = frozenset({
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "table", "blockquote", "pre",
})
_LIST_TAGS = frozenset({"ul", "ol"})


def _render_node(node: Tag | NavigableString) -> str:
    """Recursively render a bs4 node to plain text.

    Block-level tags own their own newline discipline — the caller joins
    blocks with a single "\n\n" so the output has exactly one blank line
    between them. Inline tags contribute only their text content.
    """
    if isinstance(node, NavigableString):
        # Preserve whitespace verbatim; collapsing is the parent's job.
        return str(node)

    name = (node.name or "").lower()

    if name == "br":
        return "\n"

    if name == "table":
        return _render_table(node)

    if name in _LIST_TAGS:
        return _render_list(node, ordered=(name == "ol"))

    if name in _BLOCK_TAGS:
        # Block — recursively render children and stitch.
        inner = _render_children(node)
        return inner.strip()

    # Inline / unknown — pass through the text content of children.
    return _render_children(node)


def _render_children(node: Tag) -> str:
    """Render children and join. Block children are separated by "\n\n";
    inline children are concatenated verbatim so mixed content reads
    naturally (e.g. `<p>Hello <b>world</b></p>` → `Hello world`)."""
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
            continue
        child_name = (child.name or "").lower()
        rendered = _render_node(child)
        if not rendered and child_name not in _BLOCK_TAGS:
            continue
        if child_name == "br":
            # Soft break — single newline without flanking blank line.
            parts.append(rendered)
        elif child_name in _BLOCK_TAGS:
            # Block boundary — flush with a blank line both before and
            # after so adjacent blocks always separate cleanly. The
            # outer join collapses runs of >2 newlines.
            if parts and not parts[-1].endswith("\n"):
                parts.append("\n\n")
            parts.append(rendered)
            parts.append("\n\n")
        else:
            parts.append(rendered)
    joined = "".join(parts)
    # Normalise runs of >2 newlines down to exactly 2 — the "\n\n"
    # separators we inserted above can stack against literal newlines
    # in source content.
    while "\n\n\n" in joined:
        joined = joined.replace("\n\n\n", "\n\n")
    return joined


def _render_list(node: Tag, *, ordered: bool) -> str:
    items: list[str] = []
    index = 1
    for child in node.children:
        if not isinstance(child, Tag):
            continue
        if (child.name or "").lower() != "li":
            continue
        text = _render_children(child).strip()
        # A blank li is still an entry — show it as a bare marker so the
        # structure survives even if the content is empty.
        prefix = f"{index}. " if ordered else "- "
        items.append(f"{prefix}{text}")
        index += 1
    return "\n".join(items)


def _render_table(node: Tag) -> str:
    # Walk every <tr> that belongs to THIS table — not to a table
    # nested inside one of this table's <td>. Authoring tools are
    # inconsistent about whether they emit <thead>/<tbody> wrappers,
    # so we accept <tr> either at the top level or wrapped in any
    # of the standard section tags.
    rows: list[str] = []
    for tr in _own_table_rows(node):
        cells: list[str] = []
        for cell in tr.find_all(["th", "td"], recursive=False):
            # Render each cell's children with the full pipeline so a
            # nested table flattens in place.
            cells.append(_render_children(cell).strip())
        rows.append(" | ".join(cells))
    return "\n".join(rows)


def _own_table_rows(table: Tag):
    """Yield every ``<tr>`` that belongs directly to ``table`` — i.e.
    NOT the ones nested inside a ``<table>`` inside one of this table's
    cells. Peer-review finding #4 (the previous ``find_all("tr")`` was
    recursive and duplicated nested rows).

    Walks direct ``<tr>`` children, plus ``<tr>`` inside any immediate
    ``<thead>``/``<tbody>``/``<tfoot>`` child. That covers the two HTML
    shapes authors write without descending into nested tables.
    """
    # Direct <tr> children.
    for child in table.children:
        if isinstance(child, Tag) and (child.name or "").lower() == "tr":
            yield child
        elif isinstance(child, Tag) and (child.name or "").lower() in {
            "thead", "tbody", "tfoot",
        }:
            for tr in child.children:
                if isinstance(tr, Tag) and (tr.name or "").lower() == "tr":
                    yield tr


def html_to_excel_text(
    html: Optional[str],
    source_pages: Optional[Iterable[int]] = None,  # noqa: ARG001
) -> str:
    """Flatten an HTML string to Excel-friendly plain text.

    ``source_pages`` is accepted for symmetry with the truncation helper
    but is unused here — flattening never appends a footer.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    # Render every top-level node; bs4's `soup` is effectively a virtual
    # document root so we can reuse `_render_children` against it.
    rendered = _render_children(soup).strip()
    # Any trailing block-boundary padding survives the inner normaliser —
    # strip once more here so callers see a clean string.
    return rendered


def rendered_length(html: Optional[str]) -> int:
    """Return the character length of the Excel-plaintext rendering.

    Used by the 30k char cap (the limit applies to rendered text, not the
    raw HTML with tag overhead).
    """
    if not html:
        return 0
    return len(html_to_excel_text(html))


def truncate_html_to_rendered_length(
    html: str,
    max_rendered: int,
    source_pages: Optional[Iterable[int]] = None,
) -> str:
    """Truncate ``html`` so its rendered length fits under ``max_rendered``.

    Preserves HTML well-formedness and as much original content as
    possible. Walks the top-level block children in order, keeping
    whole blocks while they fit; when the next block would overflow
    the budget, splits it at a text boundary and keeps a clipped copy
    of just the head of its text content. Appends an HTML footer
    (``<p><em>[truncated -- see PDF pages …]</em></p>``) pointing at
    ``source_pages``.

    The intra-block split path matters: a disclosure note often lives
    in a single long ``<p>``; dropping the whole block (the previous
    behaviour) used to leave the reader with footer-only output.

    ``source_pages`` may be empty/None → the footer renders "n/a".
    """
    if rendered_length(html) <= max_rendered:
        return html

    pages_list = list(source_pages) if source_pages is not None else []
    pages_str = ", ".join(str(p) for p in pages_list) if pages_list else "n/a"
    footer_plain = f"[truncated -- see PDF pages {pages_str}]"
    footer_html = f"<p><em>{footer_plain}</em></p>"
    # Reserve space for the footer's rendered length (one "\n\n" block
    # boundary + the bracketed text). `rendered_length` of the footer
    # strips whitespace so we add the separator back manually.
    footer_rendered = len(footer_plain) + 2
    budget = max(0, max_rendered - footer_rendered)

    soup = BeautifulSoup(html, "html.parser")
    kept_html: list[str] = []
    used = 0
    for child in list(soup.children):
        if isinstance(child, NavigableString):
            text = str(child)
            chunk_len = len(text.strip())
            sep = 2 if kept_html else 0
            if used + sep + chunk_len <= budget:
                kept_html.append(text)
                used += sep + chunk_len
                continue
            # Clip the string — loose top-level text has no tag
            # wrapping, so a simple slice is safe.
            remaining = max(0, budget - used - sep)
            if remaining > 0:
                kept_html.append(text[:remaining])
            break
        if not isinstance(child, Tag):
            continue
        chunk_rendered = html_to_excel_text(str(child))
        sep = 2 if kept_html else 0
        if used + sep + len(chunk_rendered) <= budget:
            kept_html.append(str(child))
            used += sep + len(chunk_rendered)
            continue
        # This block overflows. Keep as much of its content as fits by
        # clipping its rendered text; wrap the clipped text in the same
        # block tag as the original so well-formedness survives.
        remaining = max(0, budget - used - sep)
        if remaining > 0:
            clipped = _clip_block_to_length(child, remaining)
            if clipped:
                kept_html.append(clipped)
        break

    return "".join(kept_html) + footer_html


def _clip_block_to_length(block: Tag, max_len: int) -> str:
    """Return an HTML fragment of ``block`` whose rendered length is
    ≤ ``max_len``.

    Strategy: render the block's children to plain text, slice the
    result to the budget, then wrap the slice in the same tag as the
    original block. Losing inline formatting on the clipped tail is
    an acceptable trade — the footer immediately follows and makes
    clear the reader should open the PDF for the remainder.
    """
    if max_len <= 0:
        return ""
    rendered = _render_children(block).strip()
    if not rendered:
        return ""
    clipped = rendered[:max_len]
    # Prefer cutting at a whitespace boundary near the end so the
    # footer doesn't read like mid-word hash-collision output.
    last_ws = clipped.rfind(" ")
    if last_ws >= max_len - 80 and last_ws > 0:
        clipped = clipped[:last_ws]
    tag_name = (block.name or "p").lower()
    # Only keep the tag wrapper if it's in the same block family as
    # the original; otherwise default to <p> so the output stays valid.
    if tag_name not in _BLOCK_TAGS:
        tag_name = "p"
    return f"<{tag_name}>{clipped}</{tag_name}>"
