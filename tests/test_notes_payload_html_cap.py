"""Step 3 — rendered-length cap enforcement in notes/writer.

The 30k char cap applies to the *rendered* text, not to the HTML payload
with tag overhead. A 25k-char paragraph wrapped in `<p>…</p><strong>` et
al. may have 32k+ raw chars but its Excel rendering still fits, so the
agent should not be forced to truncate.
"""
from __future__ import annotations

from notes.html_to_text import html_to_excel_text
from notes.writer import CELL_CHAR_LIMIT, _truncate_with_footer


def test_payload_with_html_under_30k_rendered_passes():
    # 500 paragraphs of 50 'A's wrapped in <strong><em> — raw HTML tag
    # overhead pushes len(html) well past 30k, while the RENDERED text
    # is ~26k (25k content + blank-line separators). The writer must
    # preserve the payload verbatim because the cap applies to the
    # rendered form, not the raw HTML.
    paragraph = "<p><strong><em>" + ("A" * 50) + "</em></strong></p>"
    html = "".join(paragraph for _ in range(500))
    assert len(html) > CELL_CHAR_LIMIT
    assert len(html_to_excel_text(html)) <= CELL_CHAR_LIMIT

    out = _truncate_with_footer(html, source_pages=[12])
    assert out == html


def test_payload_over_30k_rendered_truncates_with_html_footer():
    # 35k chars of rendered text — needs truncating.
    body = "Z" * 35_000
    html = f"<p>{body}</p>"
    out = _truncate_with_footer(html, source_pages=[28, 29, 30])

    # Rendered length is under the cap.
    rendered = html_to_excel_text(out)
    assert len(rendered) <= CELL_CHAR_LIMIT

    # Footer text appears in the rendered output.
    assert "[truncated -- see PDF pages 28, 29, 30]" in rendered

    # Truncation happens at a tag boundary — no stray '<' without a
    # later '>'. Equivalently: the output parses back to a clean
    # render without leaking raw tag fragments.
    for idx, ch in enumerate(out):
        if ch == "<":
            assert ">" in out[idx:], f"mid-tag split at char {idx}"


def test_existing_plaintext_payloads_still_work():
    # Backwards-compat: a content string with no HTML tags should
    # behave identically to pre-HTML-contract behaviour. 35K of text →
    # truncation + footer, total under cap.
    content = "Z" * 35_000
    out = _truncate_with_footer(content, source_pages=[12])
    assert len(out) <= CELL_CHAR_LIMIT
    assert "[truncated -- see PDF pages 12]" in out
