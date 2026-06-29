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
    """Pins the CSS property vocabulary to the editor's controls (notes editor
    v2): cell fill + per-side borders, plus text colour (Color → span) and
    paragraph/cell alignment (TextAlign). Widen this set only when the editor
    gains a matching control, or a persisted style gets silently dropped on the
    next re-save. Note this is the full *vocabulary* — which tag may carry each
    is gated separately by `_STYLE_PROPS_BY_TAG`."""
    assert ALLOWED_CSS_PROPERTIES == frozenset({
        "background-color",
        "border-top",
        "border-right",
        "border-bottom",
        "border-left",
        # The all-sides + grouped forms the browser collapses uniform per-side
        # borders into on getHTML() — never authored by the editor, always
        # produced by it (real-Chrome incident, 2026-06-23).
        "border",
        "border-width",
        "border-style",
        "border-color",
        "color",
        "text-align",
        "width",        # column width on <table>/<col> (resizable table)
        "min-width",    # TipTap's always-emitted layout width on <table>/<col>
        "margin-left",  # paragraph indent
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


def test_hidden_border_survives_on_td() -> None:
    """The notes editor's eraser writes `hidden` (not `none`) on a side so it
    truly disappears in the collapsed table — `none` loses the shared-edge
    conflict to a neighbour's grid line, `hidden` wins. The sanitiser must keep
    `hidden`, both as a per-side longhand and bare, or erasing an edge reverts
    to the default grey grid on the next save."""
    out = _clean(
        '<table><tr><td style="border-right: hidden">x</td></tr></table>'
    )
    assert "border-right: hidden" in out.lower()
    bare = _clean('<table><tr><td style="border-top: 1px hidden #000">x</td></tr></table>')
    assert "border-top: 1px hidden #000" in bare.lower()


def test_browser_serialised_rgb_border_survives_on_td() -> None:
    """Browser colour-picker output includes spaces inside rgb(). The
    sanitiser must treat that as one valid colour token, not three fragments."""
    out = _clean(
        '<table><tr><td style="border-top: 1px solid rgb(255, 255, 255); '
        'border-right: 1px solid rgb(255, 255, 255); '
        'border-bottom: 1px solid rgb(255, 255, 255); '
        'border-left: 1px solid rgb(255, 255, 255)">x</td></tr></table>'
    )
    assert out.lower().count("rgb(255, 255, 255)") == 4


def test_browser_collapsed_border_shorthand_survives_on_td() -> None:
    """Real Chrome collapses four uniform per-side borders ("Border all") into
    the `border:` shorthand on `editor.getHTML()`. The sanitiser must keep it,
    or the all-border cell is stripped on save and reverts to the default grid
    (the symptom jsdom unit tests missed — jsdom keeps the longhands)."""
    out = _clean(
        '<table><tr><td style="border: 1px solid rgb(24, 95, 165)">x</td>'
        "</tr></table>"
    )
    assert "border: 1px solid rgb(24, 95, 165)" in out.lower()


def test_border_collapse_longhand_groups_and_none_survive() -> None:
    """The other forms the browser collapses uniform borders into: the
    `border-width`/`border-style`/`border-color` grouped longhands, plus the
    `border: none` / `border-style: none` resets."""
    for decl in (
        "border-width: 1px",
        "border-style: solid",
        "border-color: rgb(24, 95, 165)",
        "border: none",
        "border-style: none",
        "border-color: rgb(0, 0, 0) rgb(0, 0, 0) rgb(24, 95, 165) rgb(0, 0, 0)",
    ):
        out = _clean(f'<table><tr><td style="{decl}">x</td></tr></table>')
        prop = decl.split(":")[0]
        assert prop in out.lower(), decl


def test_malformed_border_shorthands_are_rejected() -> None:
    for decl in (
        "border: 1px solid url(x)",
        "border: 99px solidx rgb(9, 9, 9)",
        "border-color: rgb(999, 0, 0)",
        "border-style: wiggly",
        "border-width: 1px 2px 3px 4px 5px",  # > 4 tokens
    ):
        cleaned, warnings = sanitize_notes_html(
            f'<table><tr><td style="{decl}">x</td></tr></table>'
        )
        prop = decl.split(":")[0]
        assert prop not in cleaned.lower(), decl


def test_valid_rgba_and_percentage_colours_survive() -> None:
    out = _clean(
        '<table><tr><td style="background-color: rgba(12, 34, 56, 0.5); '
        'border-bottom: 1px solid rgb(100%, 50%, 0%)">x</td></tr></table>'
    )
    low = out.lower()
    assert "background-color: rgba(12, 34, 56, 0.5)" in low
    assert "border-bottom: 1px solid rgb(100%, 50%, 0%)" in low


def test_malformed_or_out_of_range_function_colours_are_rejected() -> None:
    for value in [
        "rgb(999, 0, 0)",
        "rgb(1, 2)",
        "rgba(1, 2, 3)",
        "rgba(1, 2, 3, 1.1)",
        "rgb(1,,3)",
    ]:
        cleaned, warnings = sanitize_notes_html(
            '<table><tr><td style="background-color: '
            f'{value}">x</td></tr></table>'
        )
        assert "background-color" not in cleaned.lower(), value
        assert any("background-color" in warning.lower() for warning in warnings)


def test_multiple_declarations_kept_in_order() -> None:
    out = _clean(
        '<table><tr>'
        '<td style="background-color: #f4f4f4; border-bottom: 1px solid #999">x</td>'
        '</tr></table>'
    )
    low = out.lower()
    assert "background-color: #f4f4f4" in low
    assert "border-bottom: 1px solid #999" in low


def test_table_cell_rejects_props_it_cannot_round_trip() -> None:
    """On a `<td>`, the editor produces fill, borders, and alignment. Properties
    it can't round-trip on a cell are rejected so they can't be silently dropped
    on a later re-save: `color` (cells have no text-colour control — that's a
    <span> concern) and `font-weight`. NOTE: the all-sides `border` shorthand is
    now ACCEPTED — the browser collapses the editor's four uniform per-side
    borders into it on getHTML() (see
    test_browser_collapsed_border_shorthand_survives_on_td)."""
    for prop, value in [
        ("color", "#123456"),
        ("font-weight", "bold"),
    ]:
        cleaned, warnings = sanitize_notes_html(
            f'<table><tr><td style="{prop}: {value}">x</td></tr></table>'
        )
        assert prop not in cleaned.lower(), f"{prop} should be rejected on <td>"
        assert any(prop in w.lower() for w in warnings)


def test_text_align_now_allowed_on_table_cell() -> None:
    """v2 widening: cell alignment is a real control (per-column align), so
    `text-align` survives on a `<td>` (it was rejected in v1)."""
    out = _clean('<table><tr><td style="text-align: right">x</td></tr></table>')
    assert "text-align: right" in out.lower()


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


# --- v2 inline marks: colour / highlight / alignment -----------------------

def test_inline_mark_tags_survive() -> None:
    """The human-applied marks the editor produces (underline, strikethrough,
    super/subscript) round-trip through the sanitiser."""
    out = _clean(
        "<p>a<u>b</u><s>c</s>d<sup>1</sup>e<sub>2</sub></p>"
    )
    low = out.lower()
    for tag in ("<u>", "<s>", "<sup>", "<sub>"):
        assert tag in low, f"{tag} should survive"


def test_text_colour_survives_on_span() -> None:
    """TipTap Color emits `<span style="color: …">`; the colour survives."""
    out = _clean('<p><span style="color: #185fa5">blue</span></p>')
    assert "color: #185fa5" in out.lower()


def test_highlight_survives_on_mark() -> None:
    """TipTap Highlight emits `<mark style="background-color: …">` (+ an inert
    `color: inherit`); the highlight fill survives."""
    out = _clean(
        '<p><mark style="background-color: #fac775; color: inherit">hi</mark></p>'
    )
    low = out.lower()
    assert "background-color: #fac775" in low
    assert "<mark" in low


def test_paragraph_alignment_survives() -> None:
    """TipTap TextAlign emits `text-align` on the paragraph/heading."""
    out = _clean('<p style="text-align: center">centred</p>')
    assert "text-align: center" in out.lower()


def test_colour_is_rejected_off_its_tag() -> None:
    """`color` is a <span> concern only — it must NOT survive on a paragraph
    (the tag-aware gate, not just a global property whitelist)."""
    cleaned, _ = sanitize_notes_html('<p style="color: #123456">x</p>')
    assert "color" not in cleaned.lower()
    assert "x" in cleaned


def test_dangerous_value_still_rejected_on_span_colour() -> None:
    """The value gate still applies to the new properties: a `url()` / script
    payload in a span colour is dropped."""
    cleaned, _ = sanitize_notes_html(
        '<p><span style="color: url(javascript:alert(1))">x</span></p>'
    )
    low = cleaned.lower()
    assert "url(" not in low
    assert "javascript" not in low


# --- v2 column width / indent / cell alignment -----------------------------

def test_column_width_colgroup_survives() -> None:
    """TipTap's resizable table emits `<colgroup><col style="width: …">` +
    `<table style="width: …">`; those must survive so widths round-trip and
    paste faithfully."""
    out = _clean(
        '<table style="width: 320px">'
        '<colgroup><col style="width: 120px"><col style="width: 200px"></colgroup>'
        '<tbody><tr><td colwidth="120">a</td><td colwidth="200">b</td></tr></tbody>'
        "</table>"
    )
    low = out.lower()
    assert "<colgroup>" in low
    assert "width: 120px" in low
    assert "width: 200px" in low
    assert "width: 320px" in low  # table overall width
    assert "colwidth=\"120\"" in low or "colwidth='120'" in low


def test_resizable_table_min_width_round_trips() -> None:
    """TipTap's resizable table emits `min-width` on EVERY un-sized table/col
    (verified). The sanitiser must keep it, or it strips its own editor output
    and re-triggers a setContent() reconcile on every table save. Mirrors a
    real un-sized resizable table's serialisation."""
    html = (
        '<table style="min-width: 50px">'
        '<colgroup><col style="min-width: 25px"><col style="min-width: 25px"></colgroup>'
        '<tbody><tr><td colspan="1" rowspan="1">a</td>'
        '<td colspan="1" rowspan="1">b</td></tr></tbody>'
        "</table>"
    )
    cleaned, warnings = sanitize_notes_html(html)
    low = cleaned.lower()
    assert low.count("min-width: 50px") == 1
    assert low.count("min-width: 25px") == 2
    # No churn signal: nothing was stripped from the editor's own output.
    assert warnings == []


def test_min_width_rejected_off_table_and_col() -> None:
    """`min-width` is a table-layout concern only — not on a paragraph/cell."""
    para, _ = sanitize_notes_html('<p style="min-width: 50px">x</p>')
    assert "min-width" not in para.lower()
    cell, _ = sanitize_notes_html(
        '<table><tr><td style="min-width: 50px">x</td></tr></table>'
    )
    assert "min-width" not in cell.lower()


def test_invalid_width_value_rejected() -> None:
    """A non-length width (e.g. `calc()` / junk) is dropped."""
    cleaned, _ = sanitize_notes_html(
        '<table><colgroup><col style="width: calc(100% - 5px)"></colgroup>'
        "<tbody><tr><td>x</td></tr></tbody></table>"
    )
    assert "calc" not in cleaned.lower()


def test_paragraph_indent_survives() -> None:
    """Paragraph indentation persists as `margin-left` (em)."""
    out = _clean('<p style="margin-left: 4em">indented</p>')
    assert "margin-left: 4em" in out.lower()


def test_indent_off_tag_and_bad_value_rejected() -> None:
    """`margin-left` is a block concern only (not on a cell), and only a
    positive em/px length is accepted."""
    # Not allowed on a table cell.
    cell, _ = sanitize_notes_html(
        '<table><tr><td style="margin-left: 4em">x</td></tr></table>'
    )
    assert "margin-left" not in cell.lower()
    # A non-length value is dropped on a paragraph.
    bad, _ = sanitize_notes_html('<p style="margin-left: 50vw">x</p>')
    assert "margin-left" not in bad.lower()


def test_cell_text_align_survives() -> None:
    """Per-cell alignment persists as `text-align` on the `<td>` (distinct from
    the paragraph-level mark)."""
    out = _clean('<table><tr><td style="text-align: right">1,595</td></tr></table>')
    assert "text-align: right" in out.lower()


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


def test_valid_tiptap_colwidth_round_trips() -> None:
    out = _clean(
        '<table><tr><td colspan="2" colwidth="120,0">x</td></tr></table>'
    )
    low = out.lower()
    assert 'colspan="2"' in low or "colspan='2'" in low
    assert 'colwidth="120,0"' in low or "colwidth='120,0'" in low


def test_invalid_table_structure_values_are_removed() -> None:
    cleaned, warnings = sanitize_notes_html(
        '<table><tr><td colspan="0" rowspan="10000" '
        'colwidth="120,evil">x</td></tr></table>'
    )
    low = cleaned.lower()
    assert "colspan" not in low
    assert "rowspan" not in low
    assert "colwidth" not in low
    assert sum("invalid value" in warning.lower() for warning in warnings) == 3


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
