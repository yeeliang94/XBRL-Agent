"""CSS-whitelist sanitiser tests — notes WYSIWYG formatting (Phase 1).

`sanitize_notes_html` now permits a *validated* inline `style=` on table
tags so the accountant's cell fill / per-side borders / alignment persist to
the DB (docs/PRD-notes-wysiwyg-formatting.md). These tests pin:

  * whitelisted properties with valid values survive on table cells;
  * the explicit RESET values (`transparent`, `border: none`) survive — that
    is how "no fill" / "no border" are stored (peer-review #2);
  * plausible-but-invalid values are rejected (peer-review #1, e.g.
    `font-weight: heavy`, `url(...)`, `position: fixed`);
  * `style=` is still stripped wholesale OFF the table (gotcha #16 for prose);
  * table structure attributes (`colspan`/`rowspan`) round-trip (peer-review #6).
"""
from __future__ import annotations

from notes.html_sanitize import ALLOWED_CSS_PROPERTIES, sanitize_notes_html


def _clean(html: str) -> str:
    cleaned, _warnings = sanitize_notes_html(html)
    return cleaned


# --- the whitelist is the editor's contract (peer-review #3) ---------------

def test_allowed_css_properties_is_exactly_the_editor_set() -> None:
    """Pins the public CSS whitelist to the editor's Format-bar controls
    (web/src/lib/cellFormatting.ts: fill + per-side borders). Widen this set
    only when the editor gains a matching control, or a persisted style gets
    silently dropped on the next re-save."""
    assert ALLOWED_CSS_PROPERTIES == frozenset({
        "background-color",
        "border-top",
        "border-right",
        "border-bottom",
        "border-left",
    })


# --- whitelisted styles survive on table cells -----------------------------

def test_background_color_survives_on_td() -> None:
    out = _clean('<table><tr><td style="background-color: #eee">x</td></tr></table>')
    assert "background-color: #eee" in out.lower()


def test_per_side_border_survives_on_td() -> None:
    out = _clean(
        '<table><tr><td style="border-bottom: 1px solid #000">x</td></tr></table>'
    )
    assert "border-bottom: 1px solid #000" in out.lower()


def test_multiple_declarations_kept_in_order() -> None:
    out = _clean(
        '<table><tr>'
        '<td style="background-color: #f4f4f4; border-bottom: 1px solid #999">x</td>'
        '</tr></table>'
    )
    low = out.lower()
    assert "background-color: #f4f4f4" in low
    assert "border-bottom: 1px solid #999" in low


def test_contract_narrowed_to_editor_set() -> None:
    """The whitelist mirrors the editor exactly (peer-review #3): properties the
    Format bar can't produce/round-trip are rejected so they can't be silently
    dropped on a later re-save. `text-align`, `color`, `font-weight`, and the
    all-sides `border` shorthand are NOT accepted."""
    for prop, value in [
        ("text-align", "right"),
        ("color", "#123456"),
        ("font-weight", "bold"),
        ("border", "1px solid #000"),  # all-sides shorthand — UI uses per-side
    ]:
        cleaned, warnings = sanitize_notes_html(
            f'<table><tr><td style="{prop}: {value}">x</td></tr></table>'
        )
        assert prop not in cleaned.lower(), f"{prop} should be rejected now"
        assert any(prop in w.lower() for w in warnings)


# --- reset values (no-fill / no-border) survive (peer-review #2) -----------

def test_transparent_fill_reset_survives() -> None:
    out = _clean('<table><tr><td style="background-color: transparent">x</td></tr></table>')
    assert "background-color: transparent" in out.lower()


def test_per_side_border_none_reset_survives() -> None:
    """"No border" persists as per-side `border-*: none` (the editor sets four
    sides), the reset that overrides the panel's default grid (peer-review #2)."""
    for side in ("top", "right", "bottom", "left"):
        out = _clean(
            f'<table><tr><th style="border-{side}: none">x</th></tr></table>'
        )
        assert f"border-{side}: none" in out.lower()


# --- invalid values rejected (peer-review #1) ------------------------------

def test_invalid_border_value_is_rejected() -> None:
    cleaned, warnings = sanitize_notes_html(
        '<table><tr><td style="border-bottom: 1px wobbly #000">x</td></tr></table>'
    )
    assert "wobbly" not in cleaned.lower()
    assert any("border-bottom" in w.lower() for w in warnings)


def test_url_value_is_rejected() -> None:
    cleaned, warnings = sanitize_notes_html(
        '<table><tr><td style="background-color: url(javascript:alert(1))">x</td></tr></table>'
    )
    assert "url(" not in cleaned.lower()
    assert "javascript" not in cleaned.lower()


def test_disallowed_property_is_rejected() -> None:
    cleaned, warnings = sanitize_notes_html(
        '<table><tr><td style="position: fixed; background-color: #eee">x</td></tr></table>'
    )
    low = cleaned.lower()
    assert "position" not in low
    # The valid sibling declaration is still kept.
    assert "background-color: #eee" in low
    assert any("position" in w.lower() for w in warnings)


def test_expression_value_is_rejected() -> None:
    cleaned, _ = sanitize_notes_html(
        '<table><tr><td style="width: expression(alert(1))">x</td></tr></table>'
    )
    assert "expression" not in cleaned.lower()


def test_invalid_hex_length_is_rejected() -> None:
    """`{3,8}` would also pass 5- and 7-digit hex, which no browser renders.
    Only the valid lengths (3/4/6/8) survive."""
    cleaned, warnings = sanitize_notes_html(
        '<table><tr><td style="background-color: #12345">x</td></tr></table>'
    )
    assert "#12345" not in cleaned.lower()
    assert any("background-color" in w.lower() for w in warnings)


# --- style still stripped off the table (gotcha #16 for prose) -------------

def test_style_on_paragraph_still_stripped() -> None:
    cleaned, _ = sanitize_notes_html('<p style="background-color: #eee">x</p>')
    assert "style" not in cleaned.lower()
    assert "background-color" not in cleaned.lower()
    assert "x" in cleaned


# --- editor <-> sanitiser contract (notes editor v2, Step 1.4) -------------

def test_editor_canonical_styles_pass_through_unchanged() -> None:
    """Contract with the editor's `buildCellStyle`
    (web/src/lib/cellFormatting.ts): it emits cell styles in ONE canonical
    shape — fixed order (fill, then top/right/bottom/left), lowercased,
    `prop: value` joined by `; `, no trailing `;`. The sanitiser must return
    that exact string untouched, or every save round-trips through the server,
    comes back reshaped, and the editor re-`setContent`s — churning the cursor
    on each keystroke. This is the single-contract that replaces v1's
    three-layers-must-match narrative."""
    canonical = (
        "background-color: #f4f4f4; "
        "border-top: 1px solid #000; "
        "border-bottom: none"
    )
    html = f'<table><tr><td style="{canonical}">x</td></tr></table>'
    cleaned, warnings = sanitize_notes_html(html)
    assert canonical in cleaned
    assert warnings == []


def test_sanitiser_is_idempotent_on_styled_tables() -> None:
    """Sanitising an already-sanitised cell is a no-op — a second save of
    unchanged content must not mutate the stored HTML (otherwise the dirty
    check would flap)."""
    html = (
        '<table><tr>'
        '<td style="background-color: #eee; border-bottom: 1px solid #000">x</td>'
        '</tr></table>'
    )
    once, _ = sanitize_notes_html(html)
    twice, _ = sanitize_notes_html(once)
    assert once == twice


# --- table structure attributes survive (peer-review #6) -------------------

def test_colspan_rowspan_round_trip() -> None:
    out = _clean(
        '<table><tr><th colspan="2" rowspan="2">x</th></tr></table>'
    )
    low = out.lower()
    assert 'colspan="2"' in low or "colspan='2'" in low
    assert 'rowspan="2"' in low or "rowspan='2'" in low


def test_table_tags_use_an_attribute_allowlist() -> None:
    """On the style-bearing table tags only `_TABLE_STRUCTURE_ATTRS` + the
    validated `style=` survive — a non-structural attribute (`data-*`,
    `align`, …) is dropped and surfaced, so the surface stays auditable
    (peer-review #6), not "whatever wasn't blacklisted"."""
    cleaned, warnings = sanitize_notes_html(
        '<table><tr><td data-foo="bar" align="center" '
        'colspan="2" style="background-color: #eee">x</td></tr></table>'
    )
    low = cleaned.lower()
    # Structural attr + validated style survive...
    assert "colspan" in low
    assert "background-color: #eee" in low
    # ...the non-structural ones are dropped + surfaced.
    assert "data-foo" not in low
    assert "align" not in low
    assert any("data-foo" in w.lower() for w in warnings)


def test_ol_type_attribute_still_kept_off_the_table() -> None:
    """The allowlist is scoped to table tags: list numbering (`type` on <ol>)
    falls through the default-keep branch and survives."""
    out = _clean('<ol type="a"><li>x</li></ol>')
    low = out.lower()
    assert 'type="a"' in low or "type='a'" in low
