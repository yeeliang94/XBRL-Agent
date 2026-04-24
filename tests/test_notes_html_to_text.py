"""HTML→Excel-plaintext conversion for the notes rich-editor feature.

Step 1 of docs/PLAN-NOTES-RICH-EDITOR.md. The notes pipeline now emits
HTML as its canonical payload; Excel downloads flatten that HTML to
plain text (tables to pipe+newline). This module encodes both directions
and is the foundation every later phase depends on.
"""
from __future__ import annotations

import pytest

from notes.html_to_text import (
    html_to_excel_text,
    rendered_length,
    truncate_html_to_rendered_length,
)


def test_strips_inline_tags_to_plaintext():
    assert html_to_excel_text("<p>Hello <b>world</b></p>") == "Hello world"


def test_paragraph_breaks_become_double_newline():
    # Two <p> blocks → single blank line separating them.
    out = html_to_excel_text("<p>First</p><p>Second</p>")
    assert out == "First\n\nSecond"


def test_unordered_list_renders_as_dash_lines():
    out = html_to_excel_text("<ul><li>a</li><li>b</li></ul>")
    assert out == "- a\n- b"


def test_ordered_list_renders_as_numbered_lines():
    out = html_to_excel_text("<ol><li>x</li><li>y</li></ol>")
    assert out == "1. x\n2. y"


def test_table_flattens_to_pipe_separated_rows():
    html = (
        "<table>"
        "<thead><tr><th>H1</th><th>H2</th></tr></thead>"
        "<tbody><tr><td>A</td><td>B</td></tr></tbody>"
        "</table>"
    )
    assert html_to_excel_text(html) == "H1 | H2\nA | B"


def test_table_without_thead_still_flattens():
    # Many HTML fragments place <th> rows directly in <tbody>; flattener
    # should not require an explicit <thead>.
    html = (
        "<table><tr><th>H1</th><th>H2</th></tr>"
        "<tr><td>A</td><td>B</td></tr></table>"
    )
    assert html_to_excel_text(html) == "H1 | H2\nA | B"


def test_nested_tables_flatten_recursively():
    # Inner table becomes nested pipe lines inside the outer cell.
    html = (
        "<table><tr><td>outer1</td>"
        "<td><table><tr><td>in1</td><td>in2</td></tr></table></td></tr></table>"
    )
    out = html_to_excel_text(html)
    # The inner table is rendered in place; rely on it producing "in1 | in2"
    # somewhere in the flattened output for the nested cell.
    assert "outer1" in out
    assert "in1 | in2" in out
    # Peer-review finding #4: the inner row must NOT appear twice — once
    # inlined in the outer cell AND once as a sibling row. Count how many
    # rows of the flattened output reference the inner cells.
    lines_with_inner = [ln for ln in out.split("\n") if "in1" in ln]
    assert len(lines_with_inner) == 1, (
        f"inner table duplicated: {lines_with_inner!r}"
    )


def test_headings_get_blank_line_before_and_after():
    out = html_to_excel_text("<h3>X</h3><p>Y</p>")
    # A leading heading does not need a blank line *before* itself (no
    # preceding content), but any content after a heading is separated
    # by a blank line.
    assert out == "X\n\nY"


def test_rendered_length_helper():
    assert rendered_length("<p>" + "x" * 10 + "</p>") == 10


def test_rendered_length_counts_list_markers_and_separators():
    # The rendered form includes the "- " / "1. " prefixes because
    # they are visible to the reader. Pin the exact count so future
    # tweaks to the renderer are intentional.
    text = html_to_excel_text("<ul><li>a</li><li>b</li></ul>")
    assert rendered_length("<ul><li>a</li><li>b</li></ul>") == len(text)


def test_truncate_to_rendered_length_clips_at_tag_boundary():
    html = (
        "<p>" + "a" * 100 + "</p><p>" + "b" * 100 + "</p>"
    )
    out = truncate_html_to_rendered_length(
        html, max_rendered=60, source_pages=[12, 13],
    )
    # Must not split mid-tag — no stray '<' without a matching '>'.
    # (Heuristic: every '<' must be followed by a '>' later in the string.)
    for idx, ch in enumerate(out):
        if ch == "<":
            assert ">" in out[idx:], f"mid-tag split at char {idx}: {out!r}"
    # Footer must be present with the source pages.
    assert "[truncated -- see PDF pages 12, 13]" in out
    # Total rendered length must not exceed the cap.
    assert rendered_length(out) <= 60 + len("[truncated -- see PDF pages 12, 13]\n")


def test_truncate_preserves_content_when_single_block_exceeds_budget():
    """Peer-review finding #1: a single oversized block used to produce a
    footer-only output — all original content was dropped. Truncation
    must preserve as much leading content as fits and still land a
    footer, even when no block fits whole."""
    html = "<p>" + ("X" * 35_000) + "</p>"
    out = truncate_html_to_rendered_length(
        html, max_rendered=30_000, source_pages=[12],
    )
    rendered = html_to_excel_text(out)
    # Footer is present.
    assert "[truncated -- see PDF pages 12]" in rendered
    # At least half the budget worth of original content must survive —
    # a bare-footer output means the truncator dropped everything.
    surviving_x = rendered.count("X")
    assert surviving_x >= 15_000, (
        f"only {surviving_x} chars of original content survived; "
        "truncator is dropping content it should have kept"
    )
    # Total rendered length still under the cap.
    assert len(rendered) <= 30_000


def test_truncate_preserves_first_block_even_when_second_overflows():
    """Mixed case: first block fits, second pushes us over — the first
    block plus whatever fits of the second (or a clean stop) must
    survive, not zero-out."""
    html = "<p>KEEP THIS</p>" + "<p>" + ("Y" * 35_000) + "</p>"
    out = truncate_html_to_rendered_length(
        html, max_rendered=1_000, source_pages=[1],
    )
    rendered = html_to_excel_text(out)
    assert "KEEP THIS" in rendered
    assert "[truncated -- see PDF pages 1]" in rendered


def test_truncate_no_op_when_under_cap():
    html = "<p>short</p>"
    out = truncate_html_to_rendered_length(html, max_rendered=100, source_pages=[1])
    assert out == html


def test_empty_or_none_input_returns_empty_string():
    assert html_to_excel_text("") == ""
    assert html_to_excel_text(None) == ""  # type: ignore[arg-type]
    assert rendered_length("") == 0
    assert rendered_length(None) == 0  # type: ignore[arg-type]


def test_malformed_html_does_not_raise():
    # Unclosed tag; should still render something sensible.
    out = html_to_excel_text("<p>unclosed")
    assert "unclosed" in out
    # No exception.


def test_br_becomes_single_newline():
    # <br> is a single hard break inside a paragraph — distinct from
    # paragraph separation.
    out = html_to_excel_text("<p>line1<br/>line2</p>")
    assert out == "line1\nline2"


def test_strong_and_em_are_stripped():
    # M-Tool handles formatting via the clipboard; Excel cells see plain text.
    out = html_to_excel_text("<p><strong>bold</strong> and <em>italic</em></p>")
    assert out == "bold and italic"
