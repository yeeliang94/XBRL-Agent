"""Peer-review #8 regression — `_looks_like_html` is the writer's
cheap HTML-vs-plaintext gate. The old implementation fired on any
``<`` followed by an ASCII letter, which would mis-classify
``<CAPEX>`` or ``<Other Asset>`` as HTML. That used to be safe
because downstream code re-routes through `html_to_excel_text` which
extracts text, but a tighter check is sturdier and documents intent.

The check now only fires on ``<`` followed by a known allowed tag
name (same whitelist as the sanitiser plus ``/`` for close tags).
"""
from __future__ import annotations

from notes.writer import _looks_like_html


class TestLooksLikeHtml:
    def test_recognises_allowed_tags(self) -> None:
        assert _looks_like_html("<p>hello</p>")
        assert _looks_like_html("<em>x</em>")
        assert _looks_like_html("<ul><li>a</li></ul>")
        assert _looks_like_html("<table><tr><td>x</td></tr></table>")
        assert _looks_like_html("<h3>heading</h3>")
        # Self-closing / whitespace variants still match.
        assert _looks_like_html("line one<br/>line two")
        assert _looks_like_html("<strong >bold</strong>")

    def test_ignores_angle_brackets_around_non_tag_content(self) -> None:
        # Accounting-style placeholders that the legacy check would
        # have misread as HTML.
        assert not _looks_like_html("<CAPEX> summary placeholder")
        assert not _looks_like_html("see <Other Asset> on p 12")
        assert not _looks_like_html("pre-tax profit < 10m")
        assert not _looks_like_html("a << b")
        assert not _looks_like_html("plain text with no angle at all")

    def test_ignores_unknown_tag_names(self) -> None:
        # Tags outside the whitelist don't trigger HTML-handling —
        # they'd be stripped by the sanitiser anyway, and treating
        # them as HTML would mis-path the writer's plain-text branch.
        assert not _looks_like_html("<blink>retro</blink>")
        assert not _looks_like_html("<custom-element>hi</custom-element>")

    def test_close_tag_alone_counts(self) -> None:
        # `</p>` on its own still signals HTML — an open tag could be
        # earlier in a multi-paragraph cell.
        assert _looks_like_html("bare close </p> tail")
