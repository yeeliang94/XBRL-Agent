"""Tests for mtool/notes_decorate.py — the backend port of the (mTool-render-
proven) clipboard decorator. Mirrors web/src/__tests__/clipboard.test.ts so the
Python fill path and the TS copy path stay in lock-step (gotcha #16 sibling).
"""
import re

from mtool.notes_decorate import (
    NotesTableStyle, decorate_notes_html, is_numeric_cell_text,
    should_right_align_cell)


# --- numeric detection (parity with tableAlign.ts) --------------------------
def test_numeric_cell_detection():
    for yes in ("1,595", "(95)", "-", "—", "1.5", "-42", "16,330"):
        assert is_numeric_cell_text(yes), yes
    for no in ("Freehold land", "2024 note", "Total assets", "N/A"):
        assert not is_numeric_cell_text(no), no


def test_row_label_column_stays_left_even_if_numeric():
    # First cell of a multi-column row is the label column — left even when it
    # reads like a number (a "2024" period label).
    assert should_right_align_cell("2024", 0, 3) is False
    assert should_right_align_cell("1,500", 1, 3) is True
    # A bare single-cell numeric row still right-aligns.
    assert should_right_align_cell("1,500", 0, 1) is True


# --- prose decoration -------------------------------------------------------
def test_prose_gets_arial_and_paragraph_spacing():
    out = decorate_notes_html("<p>First paragraph.</p>")
    assert re.search(r'<p[^>]*style="[^"]*font-family: Arial[^"]*">First', out)
    assert re.search(r'<p[^>]*style="[^"]*font-size: 10pt', out)
    assert re.search(r'<p[^>]*style="[^"]*margin: 0 0 8px 0', out)
    # Wrapping container carries the face so bare <strong>/loose text inherits.
    assert re.match(r'<div[^>]*style="[^"]*font-family: Arial', out)


def test_bold_and_inline_marks_are_preserved():
    out = decorate_notes_html("<p>a <strong>bold</strong> <em>it</em></p>")
    assert "<strong>bold</strong>" in out
    assert "<em>it</em>" in out


# --- table decoration -------------------------------------------------------
def test_table_gets_borders_font_and_legacy_attrs():
    out = decorate_notes_html(
        "<table><tbody><tr><td>Land</td><td>1,595</td></tr></tbody></table>")
    assert re.search(r'<table[^>]*style="[^"]*border-collapse: collapse', out)
    assert re.search(r'<table[^>]*border="1"', out)
    assert re.search(r'<table[^>]*cellpadding="4"', out)
    assert re.search(r'<td[^>]*style="[^"]*border: 1px solid', out)
    assert re.search(r'<td[^>]*style="[^"]*padding: 4px 8px', out)
    assert re.search(r'<td[^>]*style="[^"]*font-family: Arial[^"]*font-size: 10pt', out)


def test_header_cells_get_fill_and_bold():
    out = decorate_notes_html(
        "<table><thead><tr><th>Item</th><th>2024</th></tr></thead></table>")
    assert re.search(r'<th[^>]*style="[^"]*background: #f3f4f6', out)
    assert re.search(r'<th[^>]*style="[^"]*font-weight: 600', out)


def test_numeric_columns_right_align_label_column_left():
    out = decorate_notes_html(
        "<table><tbody>"
        "<tr><td>Total</td><td>(95)</td><td>-</td><td>1,125</td></tr>"
        "</tbody></table>")
    assert re.search(r'<td[^>]*style="[^"]*text-align: left[^"]*">Total<', out)
    assert re.search(r'<td[^>]*style="[^"]*text-align: right[^"]*">\(95\)<', out)
    assert re.search(r'<td[^>]*style="[^"]*text-align: right[^"]*">-<', out)
    assert re.search(r'<td[^>]*style="[^"]*text-align: right[^"]*">1,125<', out)


# --- persisted (WYSIWYG) styles win -----------------------------------------
def test_persisted_cell_style_wins_over_decorator_defaults():
    # A user-applied cell colour + border must survive; the decorator only adds
    # what the cell does not already control (family-aware for border).
    out = decorate_notes_html(
        '<table><tbody><tr>'
        '<td style="color: red; border: 2px solid #000">x</td>'
        '</tr></tbody></table>')
    assert "color: red" in out
    assert "2px solid #000" in out
    # decorator's own 1px border must NOT be appended (cell owns the family)
    assert "1px solid #999" not in out


# --- hidden-border → white translation (mTool TX accommodation) -------------
def test_formatter_cleared_border_becomes_white_not_hidden():
    # The AI formatter clears a border with per-side `1px hidden #000000`
    # (format_patch clear_border). mTool's TX renderer draws hidden as a grey
    # line, so the decorator substitutes an invisible white border.
    cleared = ("border-top: 1px hidden #000000; "
               "border-right: 1px hidden #000000; "
               "border-bottom: 1px hidden #000000; "
               "border-left: 1px hidden #000000")
    out = decorate_notes_html(
        f'<table><tbody><tr><td style="{cleared}">x</td></tr></tbody></table>')
    assert "hidden" not in out
    assert out.lower().count("1px solid #ffffff") == 4


def test_border_none_becomes_white():
    out = decorate_notes_html(
        '<table><tbody><tr>'
        '<td style="border: none">x</td>'
        '</tr></tbody></table>')
    assert "border: none" not in out
    assert "1px solid #ffffff" in out.lower()


def test_default_grey_grid_is_not_whited_out():
    # An unformatted table keeps the decorator's default grey grid — the
    # white-out only touches borders explicitly set to hidden/none.
    out = decorate_notes_html(
        "<table><tbody><tr><td>x</td></tr></tbody></table>")
    assert "1px solid #999" in out
    assert "1px solid #ffffff" not in out.lower()


def test_real_border_is_preserved_not_whited_out():
    out = decorate_notes_html(
        '<table><tbody><tr>'
        '<td style="border-bottom: 3px double #000000">x</td>'
        '</tr></tbody></table>')
    assert "3px double #000000" in out
    assert "1px solid #ffffff" not in out.lower()


def test_grouped_border_style_hidden_becomes_white():
    # Chrome collapses uniform per-side hidden borders into the grouped
    # `border-style: hidden` longhand (gotcha #16). It must still white out —
    # the shorthand-only version missed this and left a grey TX line.
    out = decorate_notes_html(
        '<table><tbody><tr>'
        '<td style="border-width: 1px; border-style: hidden; border-color: #000000">x</td>'
        '</tr></tbody></table>')
    assert "hidden" not in out
    assert out.lower().count("1px solid #ffffff") == 4


def test_mixed_grouped_border_style_whites_only_hidden_sides():
    # A partly-erased grid: top/bottom solid, right/left hidden. Only the
    # hidden sides go white; the visible rules are preserved.
    out = decorate_notes_html(
        '<table><tbody><tr>'
        '<td style="border-width: 1px; border-style: solid hidden solid hidden; '
        'border-color: #000000">x</td>'
        '</tr></tbody></table>')
    assert "hidden" not in out
    assert "border-top: 1px solid #000000" in out
    assert "border-bottom: 1px solid #000000" in out
    assert out.lower().count("1px solid #ffffff") == 2


def test_border_collapse_survives_whiteout():
    # border-collapse / border-radius are NOT border-LINE props — they must
    # never be dropped by the white-out even when a cell has a hidden border.
    out = decorate_notes_html(
        '<table style="border-collapse: collapse"><tbody><tr>'
        '<td style="border: none; border-radius: 4px">x</td>'
        '</tr></tbody></table>')
    assert "border-collapse: collapse" in out
    assert "border-radius: 4px" in out
    assert "1px solid #ffffff" in out.lower()


# --- options ----------------------------------------------------------------
def test_no_border_option_suppresses_grid_but_keeps_padding():
    out = decorate_notes_html(
        "<table><tbody><tr><td>x</td></tr></tbody></table>",
        NotesTableStyle(border_style="none"))
    assert not re.search(r'<table[^>]*border="1"', out)
    assert "border: 1px solid" not in out
    assert re.search(r'<td[^>]*style="[^"]*padding: 4px 8px', out)


def test_themed_border_colour_and_double_rule():
    out = decorate_notes_html(
        "<table><tbody><tr><td>x</td></tr></tbody></table>",
        NotesTableStyle(border_style="double", border_color="#1F3864"))
    assert "3px double #1f3864" in out.lower()


def test_lite_tier_keeps_formatting_drops_cosmetics():
    html = "<table><tbody><tr><td>x</td><td>1,234</td></tr></tbody></table>"
    full = decorate_notes_html(html)
    lite = decorate_notes_html(html, lite=True)
    # lite keeps the formatting a reader notices...
    assert "border: 1px solid" in lite
    assert "text-align: right" in lite
    assert "font-family: Arial" in lite
    # ...but drops the cosmetic-only props, so it is strictly smaller.
    assert "vertical-align: top" not in lite
    assert "overflow-wrap" not in lite
    assert "word-break" not in lite
    assert len(lite) < len(full)


def test_empty_html_passthrough():
    assert decorate_notes_html("") == ""


# --- theme mapping (from_theme) ---------------------------------------------
def test_from_theme_empty_is_default_baseline():
    assert NotesTableStyle.from_theme({}) == NotesTableStyle()
    assert NotesTableStyle.from_theme(None) == NotesTableStyle()


def test_from_theme_maps_camelcase_fields():
    style = NotesTableStyle.from_theme({
        "borderStyle": "double", "fontSizePt": 12, "cellPaddingPx": [2, 6],
        "paragraphSpacingPx": 10, "borderColor": "#1F3864",
        "headerFill": "#EEEEEE", "headerBold": False})
    assert style == NotesTableStyle(
        border_style="double", font_size_pt=12, cell_padding_px=(2, 6),
        paragraph_spacing_px=10, border_color="#1f3864",
        header_fill="#eeeeee", header_bold=False)


def test_from_theme_ignores_malformed_fields():
    style = NotesTableStyle.from_theme({
        "borderStyle": "bogus", "fontSizePt": "big",
        "cellPaddingPx": [1], "headerBold": "yes", "borderColor": 123})
    # every bad field falls back to the default
    assert style == NotesTableStyle()


def test_from_theme_drives_decorated_output():
    out = decorate_notes_html(
        "<table><tbody><tr><td>x</td></tr></tbody></table>",
        NotesTableStyle.from_theme({"borderStyle": "double",
                                    "borderColor": "#1F3864"}))
    assert "3px double #1f3864" in out.lower()
