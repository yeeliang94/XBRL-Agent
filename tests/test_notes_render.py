"""The note picture an agent uses to see its own formatting."""
import fitz
import pytest

from notes import render

TABLE = (
    '<table style="width: 100%"><tr><td>Additions</td>'
    '<td style="{cell}">1,265,564</td></tr></table>'
)


def _image(png: bytes) -> fitz.Pixmap:
    return fitz.Pixmap(png)


@pytest.mark.parametrize("cell", [
    "border-bottom: 0.75px solid #000000",
    "text-align: right",
    "font-weight: 700",
])
def test_a_formatting_change_is_visible_in_the_picture(cell):
    plain, = render.render_html_pages(TABLE.format(cell=""))
    styled, = render.render_html_pages(TABLE.format(cell=cell))
    assert _image(plain).samples != _image(styled).samples


def test_a_long_note_continues_on_further_readable_pages():
    pages = render.render_html_pages("".join(f"<p>Paragraph {i}</p>" for i in range(120)))
    limit = render.PAGE_HEIGHT_PT * render.RENDER_DPI / 72 + 1
    assert len(pages) > 1
    assert all(_image(page).height <= limit for page in pages)


@pytest.mark.parametrize("html", [
    '<p style="font-family: Arial, sans-serif"><strong>Total</strong> <em>1,265,564</em></p>',
    "<p>Unstyled flat-tier text 1,265,564</p>",
])
def test_text_is_drawn_in_the_arial_width_font(html):
    """Another fallback font wraps figures that Arial fits on one line."""
    pdf, _ = render._note_pdf(html)
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        fonts = {font[3] for font in doc[0].get_fonts()}
    assert fonts and all(name.startswith("Liberation Sans") for name in fonts)


def test_a_saved_note_is_drawn_with_full_styling_whatever_its_size():
    note = "<h3>5. Deferred tax</h3>" + TABLE.format(cell="text-align: right")
    page, = render.render_note_pages(note)
    assert min(_image(page).samples) < 128  # ink on the page
    assert render.render_note_pages("") == []
    # A note too big for mTool's cell keeps its formatting in the picture.
    row = '<tr><td>Item</td><td style="border-bottom: 0.75px solid #000000">1,000</td></tr>'
    ruled = render.render_note_pages("<table>" + row * 400 + "</table>")
    plain = render.render_note_pages("<table>" + row.replace(' style="border-bottom: 0.75px solid #000000"', "") * 400 + "</table>")
    assert _image(ruled[0]).samples != _image(plain[0]).samples


def test_a_row_taller_than_a_page_is_refused_with_its_reason():
    cell = "<br/>".join(f"line {i}" for i in range(130))
    with pytest.raises(ValueError, match="row taller than one page"):
        render.render_html_pages(f"<table><tr><td>{cell}</td></tr></table>")


def _word_x(html: str) -> dict[str, float]:
    pdf, _ = render._note_pdf(render._fit_columns(html))
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return {word[4]: word[0] for word in doc[0].get_text("words")}


def test_a_styled_table_keeps_its_last_column_in_the_picture():
    """Cell padding and borders add to the stated widths; the right-hand
    figures must still be drawn inside the page."""
    cell = 'style="text-align: right; padding: 3px 8px; border: 0.75px solid #000000"'
    row = f'<tr><td width="60%">Total</td><td width="20%" {cell}>1,265,564</td><td width="20%" {cell}>999,111</td></tr>'
    page, = render.render_html_pages(f'<table width="100%">{row}</table>')
    picture = _image(page)
    right_edge = picture.width - int(render.MARGIN_PT * render.RENDER_DPI / 72)
    # Ink reaches the last column and stops before the page edge.
    column = lambda x: min(picture.pixel(x, y)[0] for y in range(0, picture.height, 2))
    assert min(column(x) for x in range(right_edge - 80, right_edge)) < 128
    assert min(column(x) for x in range(right_edge + 4, picture.width)) > 200


@pytest.mark.parametrize("table", [
    '<table width="100%"><tr><td width="60%">aa</td><td width="20%">bb</td><td width="20%">cc</td></tr></table>',
    # Pixel widths wider than the page keep their proportions and stay on it.
    '<table><colgroup><col style="width: 1200px"/><col style="width: 400px"/><col style="width: 400px"/></colgroup>'
    "<tr><td>aa</td><td>bb</td><td>cc</td></tr></table>",
])
def test_columns_keep_their_stated_shares_inside_the_page(table):
    x = _word_x(table)
    body = render.PAGE_WIDTH_PT - 2 * render.MARGIN_PT
    assert x["bb"] - x["aa"] == pytest.approx(0.6 * body, abs=8)
    assert x["cc"] - x["bb"] == pytest.approx(0.2 * body, abs=8)
    assert x["cc"] < render.PAGE_WIDTH_PT - render.MARGIN_PT


def test_a_table_wider_than_mtools_text_area_is_shown_whole_and_measured():
    """mTool's text area is 170 mm. A table that cannot be narrower is not cut
    off: the picture widens and the overflow is reported."""
    headers = "".join(f"<th>Heading{i}</th>" for i in range(14))
    figures = "".join("<td>1,265,564</td>" for _ in range(14))
    pages, too_wide_mm = render.render_note_preview(f"<table><tr>{headers}</tr><tr>{figures}</tr></table>")
    assert too_wide_mm > 0
    assert _image(pages[0]).width > render.PAGE_WIDTH_PT * render.RENDER_DPI / 72 + 1
    fits, none = render.render_note_preview(TABLE.format(cell=""))
    assert none == 0 and _image(fits[0]).width == pytest.approx(render.PAGE_WIDTH_PT * render.RENDER_DPI / 72, abs=2)


def test_the_preview_names_the_font_size_at_which_a_wide_table_fits():
    headers = "".join(f"<th>Heading {i}</th>" for i in range(8))
    figures = "".join("<td>1,265,564</td>" for _ in range(8))
    wide = f"<table><tr>{headers}</tr><tr>{figures}</tr></table>"
    style = render.NotesTableStyle(font_size_pt=11)
    assert render.render_note_preview(wide, style)[1] > 0
    size = render.fitting_font_size(wide, style)
    assert size is not None and 8 <= size < 11
    fitted = wide.replace("<table>", f'<table data-formatter-font-size="{size}">')
    assert render.render_note_preview(fitted, style)[1] == 0
    far_too_wide = wide.replace("<tr>", "<tr>" + "<td>1,265,564</td>" * 12)
    assert render.fitting_font_size(far_too_wide, style) is None
