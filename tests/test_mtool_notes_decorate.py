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
