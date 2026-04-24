"""Sanitiser trust-model guard (peer-review S-2).

`notes/html_sanitize.py`'s docstring lists its trust-model gaps — the
vectors it explicitly refuses to whitelist. These tests pin that the
sanitiser does in fact strip each documented gap, so a regression that
silently admits one of them is caught before it ships.

The sanitiser is called from two places (server PATCH endpoint + writer
path), both of which trust its output enough to persist the result to
the DB and render into Excel. An opening in any of these vectors would
bypass every downstream guard.
"""
from __future__ import annotations

from notes.html_sanitize import sanitize_notes_html


def _clean(html: str) -> str:
    """Small helper — we only care about the cleaned HTML, not warnings."""
    cleaned, _warnings = sanitize_notes_html(html)
    return cleaned


def test_script_tag_and_contents_removed():
    """`<script>` is in the decompose list — tag AND inner text must go."""
    out = _clean("<p>safe</p><script>alert('xss')</script>")
    assert "script" not in out.lower()
    assert "alert" not in out
    assert "safe" in out


def test_style_tag_and_contents_removed():
    """`<style>` decompose — tag AND inner CSS text must go."""
    out = _clean(
        "<p>safe</p><style>body{background: url('javascript:alert(1)')}</style>"
    )
    assert "style" not in out.lower()
    assert "background" not in out
    assert "javascript" not in out


def test_iframe_tag_and_contents_removed():
    """`<iframe>` decompose — tag plus any inner content must not leak."""
    out = _clean("<p>ok</p><iframe src='evil.html'>hidden</iframe>")
    assert "iframe" not in out.lower()
    assert "evil" not in out
    assert "hidden" not in out


def test_svg_tag_is_not_whitelisted():
    """`<svg>` can embed `<script>` inside — it's not on the whitelist
    and must be stripped (tag + inner markup that isn't separately
    whitelisted). Pinned explicitly because the docstring calls this
    out as a known gap the sanitiser must defend against."""
    out = _clean(
        "<p>before</p>"
        "<svg><script>alert('svg-xss')</script><circle r='5'/></svg>"
        "<p>after</p>"
    )
    assert "svg" not in out.lower()
    # The script tag inside the svg is a decompose target even when
    # the outer svg is only on the strip list — so the alert text
    # must not survive either.
    assert "svg-xss" not in out
    assert "before" in out and "after" in out


def test_math_tag_is_not_whitelisted():
    """`<math>` (MathML) similarly can carry scriptable elements."""
    out = _clean("<p>ok</p><math><mtext>x</mtext></math>")
    assert "<math" not in out.lower()
    assert "mtext" not in out.lower()


def test_onclick_attribute_stripped_from_whitelisted_tag():
    """A whitelisted tag (e.g. `<p>`) carrying a dangerous event handler
    should keep the tag and drop the handler. `onclick` is the canonical
    example; pin it explicitly so a misconfigured attribute whitelist
    can't slip through."""
    out = _clean("<p onclick=\"alert('x')\">hello</p>")
    assert "<p" in out.lower()
    assert "onclick" not in out.lower()
    assert "alert" not in out
    assert "hello" in out


def test_style_attribute_stripped_from_whitelisted_tag():
    """Inline `style=` can embed JS via `expression()` in legacy IE or
    `url(javascript:...)` in older browsers. The sanitiser drops the
    attribute entirely even when the containing tag is whitelisted."""
    out = _clean("<p style=\"background: url(javascript:alert(1))\">hi</p>")
    assert "<p" in out.lower()
    assert "style" not in out.lower()
    assert "javascript" not in out
    assert "hi" in out


def test_javascript_href_stripped_from_disallowed_anchor():
    """`<a>` is NOT in the whitelist. A `javascript:` href inside an
    anchor should be fully removed — both the tag and the URL text."""
    out = _clean("<p>safe</p><a href=\"javascript:alert('href-xss')\">click</a>")
    # <a> isn't whitelisted — the tag must be stripped.
    assert "<a" not in out.lower()
    # The href content should not persist as rendered text or attribute.
    assert "javascript" not in out
    assert "href-xss" not in out


def test_class_attribute_stripped_from_whitelisted_tag():
    """Inline `class=` isn't in the allowed-attribute whitelist. Even
    though <p> is permitted, the class attribute must not persist —
    otherwise operator-controlled CSS could alter the rendered cell
    post-sanitisation."""
    out = _clean('<p class="sneaky">legit</p>')
    assert "<p" in out.lower()
    assert "class" not in out.lower()
    assert "sneaky" not in out
    assert "legit" in out
