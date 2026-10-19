"""The AI's table description compiles to formatting, or fails without changing geometry."""
import re

import pytest
from bs4 import BeautifulSoup

from notes.format_verify import verify_format_only
from notes.rule_map import table_rules
from notes.table_layout import (
    CellOverride, LayoutError, LineSpec, ParagraphOverride, RowSpec, TableLayout, TextEmphasis,
    compile_layouts, layout_manifest, validate_layouts,
)

ROLL = ("<table><tr><th></th><th>2024<br>RM</th><th>2023<br>RM</th></tr>"
        "<tr><th colspan=\"3\">2024</th></tr>"
        "<tr><td>At 1 January</td><td>233,646</td><td>140,343</td></tr>"
        "<tr><td>Recognised in profit or loss</td><td>46,885</td><td>93,303</td></tr>"
        "<tr><td>At 31 December</td><td>280,531</td><td>233,646</td></tr></table>")


def cells(html, table=0):
    t = BeautifulSoup(html, "html.parser").find_all("table")[table]
    return [[(c.get_text(" ", strip=True), c.get("style") or "") for c in tr.find_all(["th", "td"], recursive=False)]
            for tr in t.find_all("tr")]


def align(style):
    found = re.search(r"text-align: (\w+)", style)
    return found.group(1) if found else None


def roll_layout(**extra):
    return TableLayout(table=0, columns=["label", "amount", "amount"], **{
        "rows": [RowSpec(row=2, role="section", bold=True), RowSpec(row=5, role="total", bold=True)],
        "lines": [LineSpec(after_row=4, from_col=2, to_col=3), LineSpec(after_row=5, from_col=2, to_col=3, kind="double")],
        **extra})


def test_roles_drive_alignment_and_lines_are_one_physical_edge():
    out = compile_layouts(ROLL, [roll_layout()])
    grid = cells(out)
    assert align(grid[0][1][1]) == "right" and align(grid[0][0][1]) == "left"
    # Full-width "2024" section row stays left (the decorator used to push it right).
    assert align(grid[1][0][1]) == "left" and ">2024</strong>" in out
    assert align(grid[2][1][1]) == "right" and align(grid[2][0][1]) is None
    assert [(r["below_row"], r["cells"], r["kind"]) for r in table_rules(out)] == [
        (4, [2, 3], "single"), (5, [2, 3], "double")]
    assert verify_format_only(ROLL, out).ok


def test_unconventional_columns_follow_the_ai_not_the_text():
    html = ("<table><tr><th>Standard</th><th>Effective</th><th>Ref</th></tr>"
            "<tr><td>MFRS 18</td><td>1 January 2027</td><td>2027</td></tr></table>")
    out = compile_layouts(html, [TableLayout(table=0, columns=["label", "date", "text"])])
    grid = cells(out)
    assert align(grid[1][1][1]) == "right"
    # A number-looking cell in a text column is pinned left so fallbacks cannot guess.
    assert align(grid[1][2][1]) == "left"


def test_line_above_first_row_uses_top_edge():
    out = compile_layouts(ROLL, [TableLayout(table=0, columns=["label", "amount", "amount"],
                                             lines=[LineSpec(after_row=0, from_col=1, to_col=3)])])
    assert [(r["below_row"], r["cells"]) for r in table_rules(out)] == [(0, [1, 2, 3])]


def test_recompile_replaces_previous_formatter_styling():
    first = compile_layouts(ROLL, [roll_layout(rows=[RowSpec(row=3, shaded=True, indent=2)])])
    assert "background-color" in first and "padding: 3px 3px 3px 35px" in first
    second = compile_layouts(first, [TableLayout(table=0, columns=["label", "amount", "amount"])])
    assert "background-color" not in second and "padding" not in second
    assert table_rules(second) == []


def test_cell_alignment_and_targeted_paragraph_indent_survive_prepared_output():
    from mtool.notes_exporter import prepare_note_output

    html = ('<table><tr><th rowspan="2">Company</th><th>2025</th></tr>'
            '<tr><td>RM</td></tr><tr><td><p>Ordinary <strong>shares</strong></p>'
            '<p>At 1 January/31 December</p></td><td>10</td></tr></table>')
    layout = TableLayout(table=0, columns=["label", "amount"],
                         overrides=[CellOverride(row=1, cell=1, vertical_align="bottom", reason="Merged header")],
                         paragraphs=[ParagraphOverride(row=3, cell=1, paragraph=2, indent=1,
                                                       text_align="left", reason="Indented balance label")])
    manifest = layout_manifest(html)[0]["rows"][2]["cells"][0]
    assert [p["paragraph"] for p in manifest["paragraphs"]] == [1, 2]
    out = compile_layouts(html, [layout])
    assert verify_format_only(html, out).ok
    for representation in (out, prepare_note_output(out)["html"]):
        soup = BeautifulSoup(representation, "html.parser")
        assert "vertical-align: bottom" in soup.find("th").get("style", "")
        first, second = soup.find_all("p")
        assert "margin-left" not in first.get("style", "")
        assert "margin-left: 2em" in second.get("style", "")
        assert soup.find("strong").get_text() == "shares"
    replaced = compile_layouts(out, [TableLayout(table=0, columns=["label", "amount"])])
    assert "vertical-align" not in replaced and "margin-left" not in replaced
    assert verify_format_only(html, replaced).ok
    cleared = compile_layouts(out, [TableLayout(table=0, columns=["label", "amount"], paragraphs=[
        ParagraphOverride(row=3, cell=1, paragraph=2, indent=0, reason="Clear previous indent")])])
    assert "margin-left" not in cleared and verify_format_only(html, cleared).ok


@pytest.mark.parametrize("paragraph", [1, 2])
def test_paragraph_targets_cannot_address_hard_break_lines_or_nested_table_paragraphs(paragraph):
    html = ('<table><tr><td>Ordinary shares<br>At 1 January/31 December'
            '<table><tr><td><p>Nested paragraph</p></td></tr></table></td></tr></table>')
    assert layout_manifest(html)[0]["rows"][0]["cells"][0]["paragraphs"] == []
    layouts = [TableLayout(table=0, columns=["label"], paragraphs=[
        ParagraphOverride(row=1, cell=1, paragraph=paragraph, indent=1, reason="Source indent")]),
        TableLayout(table=1, columns=["label"])]
    with pytest.raises(LayoutError, match="paragraph .* does not exist"):
        compile_layouts(html, layouts)


def test_duplicate_paragraph_overrides_are_rejected():
    html = '<table><tr><td><p>Label</p></td></tr></table>'
    paragraph = ParagraphOverride(row=1, cell=1, paragraph=1, indent=1, reason="Source indent")
    with pytest.raises(LayoutError, match="described twice"):
        compile_layouts(html, [TableLayout(table=0, columns=["label"], paragraphs=[paragraph, paragraph])])


def test_source_bold_is_never_removed():
    html = ROLL.replace("<td>At 31 December</td>", "<td><strong>At 31 December</strong></td>")
    out = compile_layouts(html, [TableLayout(table=0, columns=["label", "amount", "amount"])])
    assert "<strong>At 31 December</strong>" in out


def test_selective_bold_and_widths_preserve_content_through_prepared_output():
    from mtool.notes_exporter import prepare_note_output

    html = ('<table><tr><th colspan="2">Capital</th></tr><tr><td><p>Ordinary '
            '<em>shares</em> issued</p></td><td>100</td></tr></table>')
    layout = TableLayout(table=0, columns=["label", "amount"], width_percent=80,
                         column_widths=[300, 100],
                         overrides=[CellOverride(row=2, cell=2, bold=True, reason="Source amount is bold")],
                         emphasis=[TextEmphasis(row=2, cell=1, text="Ordinary shares", reason="Source emphasis")])
    out = compile_layouts(html, [layout])
    assert verify_format_only(html, out).ok
    for representation in (out, prepare_note_output(out)["html"]):
        soup = BeautifulSoup(representation, "html.parser")
        assert "width: 80%" in soup.table["style"]
        assert [col["style"] for col in soup.find_all("col")] == ["width: 300px", "width: 100px"]
        label, amount = soup.find_all("td")
        assert "".join(mark.get_text() for mark in label.find_all("strong")) == "Ordinary shares"
        assert label.em.get_text() == "shares" and "issued" not in label.find("strong").get_text()
        assert amount.strong.get_text() == "100"
    assert compile_layouts(out, [layout]) == out
    # The sizing survives the sanitiser and reaches TX as legacy attributes.
    from mtool.notes_decorate import decorate_notes_html
    from notes.html_sanitize import sanitize_notes_html
    decorated = BeautifulSoup(decorate_notes_html(sanitize_notes_html(out)[0]), "html.parser")
    assert decorated.table["width"] == "80%"
    assert [td.get("width") for td in decorated.find_all("td")] == ["75%", "25%"]
    # A layout that states no sizing keeps the current widths and every mark.
    kept = compile_layouts(out, [TableLayout(table=0, columns=["label", "amount"])])
    kept_soup = BeautifulSoup(kept, "html.parser")
    assert [col["style"] for col in kept_soup.find_all("col")] == ["width: 300px", "width: 100px"]
    assert "width: 80%" in kept_soup.table["style"]
    assert verify_format_only(out, kept).ok
    # Stated widths replace the previous ones.
    resized = compile_layouts(out, [TableLayout(table=0, columns=["label", "amount"],
                                                column_widths=[200, 120])])
    assert [col["style"] for col in BeautifulSoup(resized, "html.parser").find_all("col")] == [
        "width: 200px", "width: 120px"]


def test_layout_without_widths_keeps_an_editor_resize():
    html = ('<table style="width: 380px"><colgroup><col style="width: 300px"><col style="width: 80px">'
            '</colgroup><tr><td colwidth="300">Label</td><td colwidth="80">1,000</td></tr></table>')
    out = compile_layouts(html, [TableLayout(table=0, columns=["label", "amount"])])
    soup = BeautifulSoup(out, "html.parser")
    assert [col["style"] for col in soup.find_all("col")] == ["width: 300px", "width: 80px"]
    assert [td["colwidth"] for td in soup.find_all("td")] == ["300", "80"]
    assert "width: 380px" in soup.table["style"]


def test_a_replacement_layout_removes_formatter_bold_but_never_source_bold():
    html = ('<table><tr><td><strong>Capital</strong> reserve</td><td>100</td></tr>'
            '<tr><td>Ordinary shares issued</td><td>200</td></tr></table>')
    bolded = compile_layouts(html, [TableLayout(
        table=0, columns=["label", "amount"], rows=[RowSpec(row=1, bold=True)],
        overrides=[CellOverride(row=2, cell=2, bold=True, reason="Source amount is bold")],
        emphasis=[TextEmphasis(row=2, cell=1, text="Ordinary shares", reason="Source emphasis")])])
    assert BeautifulSoup(bolded, "html.parser").find_all(attrs={"data-formatter-emphasis": "true"})
    plain = compile_layouts(bolded, [TableLayout(table=0, columns=["label", "amount"])])
    assert verify_format_only(bolded, plain).ok
    soup = BeautifulSoup(plain, "html.parser")
    assert [mark.get_text() for mark in soup.find_all("strong")] == ["Capital"]
    assert soup.get_text() == BeautifulSoup(html, "html.parser").get_text()
    # Restating the bold brings back the same output.
    assert compile_layouts(plain, [TableLayout(
        table=0, columns=["label", "amount"], rows=[RowSpec(row=1, bold=True)],
        overrides=[CellOverride(row=2, cell=2, bold=True, reason="Source amount is bold")],
        emphasis=[TextEmphasis(row=2, cell=1, text="Ordinary shares", reason="Source emphasis")])]
    ) == bolded


def test_removing_unmarked_bold_still_fails_the_format_gate():
    before = '<table><tr><td><strong>Capital</strong></td></tr></table>'
    after = '<table><tr><td>Capital</td></tr></table>'
    assert not verify_format_only(before, after).ok
    marked = '<table><tr><td><strong data-formatter-emphasis="true">Capital</strong></td></tr></table>'
    assert verify_format_only(marked, after).ok


@pytest.mark.parametrize("content,quote", [
    ("Total Total", "Total"),
    ("Total", "Missing"),
    ("<p>Ordinary</p><p>shares</p>", "Ordinaryshares"),
    ("Ordinary<br>shares", "Ordinaryshares"),
    ("<table><tr><td>Nested</td></tr></table>", "Nested"),
])
def test_selective_bold_refuses_ambiguous_missing_or_cross_boundary_text(content, quote):
    html = f"<table><tr><td>{content}</td></tr></table>"
    layouts = [TableLayout(table=0, columns=["label"], emphasis=[
        TextEmphasis(row=1, cell=1, text=quote, reason="Source bold")])]
    if "<table>" in content:
        layouts.append(TableLayout(table=1, columns=["label"]))
    with pytest.raises(LayoutError, match="exactly one"):
        compile_layouts(html, layouts)


def test_column_widths_must_match_the_expanded_grid():
    with pytest.raises(LayoutError, match="widths.*grid columns"):
        compile_layouts(ROLL, [TableLayout(table=0, columns=["label", "amount", "amount"],
                                          column_widths=[300, 100])])


@pytest.mark.parametrize("layout,message", [
    (TableLayout(table=0, columns=["label", "amount"]), "2 roles but the table has 3"),
    (TableLayout(table=0, columns=["label", "amount", "amount"], rows=[RowSpec(row=9)]), "row 9 does not exist"),
    (TableLayout(table=0, columns=["label", "amount", "amount"],
                 lines=[LineSpec(after_row=3, from_col=1, to_col=3), LineSpec(after_row=3, from_col=3, to_col=3)]),
     "overlaps"),
    (TableLayout(table=0, columns=["label", "amount", "amount"], lines=[LineSpec(after_row=6, from_col=1, to_col=1)]),
     "outside the table"),
])
def test_invalid_layouts_fail_with_specific_messages(layout, message):
    assert any(message in error for error in validate_layouts(ROLL, [layout]))
    with pytest.raises(LayoutError):
        compile_layouts(ROLL, [layout])


def test_line_cannot_cross_a_row_spanning_cell():
    html = ('<table><tr><td rowspan="2">Lease</td><td>1</td></tr><tr><td>2</td></tr></table>')
    errors = validate_layouts(html, [TableLayout(table=0, columns=["label", "amount"],
                                                 lines=[LineSpec(after_row=1, from_col=1, to_col=2)])])
    assert any("merged across rows" in e for e in errors)
    # The amount column alone is a valid partial line beside the rowspan.
    ok = TableLayout(table=0, columns=["label", "amount"], lines=[LineSpec(after_row=1, from_col=2, to_col=2)])
    assert table_rules(compile_layouts(html, [ok]))[0]["cells"] == [2]


def test_partial_line_beside_a_colspan_header():
    html = ('<table><tr><th></th><th colspan="2">Company</th><th>Note</th></tr>'
            "<tr><td>Cash</td><td>1</td><td>2</td><td>5</td></tr></table>")
    layout = TableLayout(table=0, columns=["label", "amount", "amount", "text"],
                         lines=[LineSpec(after_row=1, from_col=2, to_col=3)])
    rules = table_rules(compile_layouts(html, [layout]))
    assert [(r["below_row"], r["cells"]) for r in rules] == [(1, [2])]
    # Half the merged heading: drawn on the whole cell below instead.
    narrow = layout.model_copy(update={"lines": [LineSpec(after_row=1, from_col=2, to_col=2)]})
    assert [r["cells"] for r in table_rules(compile_layouts(html, [narrow]))] == [[2]]


def test_amount_underline_above_a_full_width_section_row():
    # Live run 348 row 71: the subtotal underline sits above a merged "2024" row.
    out = compile_layouts(ROLL, [TableLayout(table=0, columns=["label", "amount", "amount"],
                                             lines=[LineSpec(after_row=1, from_col=2, to_col=3)])])
    assert [(r["below_row"], r["cells"]) for r in table_rules(out)] == [(1, [2, 3])]


def test_line_cutting_merged_cells_on_both_sides_is_refused():
    html = ('<table><tr><td colspan="2">A</td><td>1</td></tr>'
            '<tr><td>B</td><td colspan="2">2</td></tr></table>')
    layout = TableLayout(table=0, columns=["label", "amount", "amount"],
                         lines=[LineSpec(after_row=1, from_col=2, to_col=2)])
    assert any("both sides" in e for e in validate_layouts(html, [layout]))


def test_every_table_needs_one_layout_and_word_tables_need_none():
    html = ROLL + '<table data-source-styled="true"><tr><td>Word</td></tr></table>' + ROLL
    one = TableLayout(table=0, columns=["label", "amount", "amount"])
    assert any("exactly one layout for each table [0, 2]" in e for e in validate_layouts(html, [one]))
    both = [one, one.model_copy(update={"table": 2})]
    assert validate_layouts(html, both) == []
    assert any("[0, 2]" in e for e in validate_layouts(html, [*both, one.model_copy(update={"table": 1})]))


def test_lines_land_on_each_cell_even_when_amounts_are_equal():
    # Live run 348 row 129: four equal amounts all resolved to the first cell.
    html = ("<table><tr><td>Issued</td><td>2,077,740</td><td>2,077,740</td><td>2,077,740</td></tr>"
            "<tr><td>Total</td><td>1</td><td>1</td><td>1</td></tr></table>")
    layout = TableLayout(table=0, columns=["label", "amount", "amount", "amount"],
                         lines=[LineSpec(after_row=1, from_col=c, to_col=c) for c in (2, 3, 4)])
    assert table_rules(compile_layouts(html, [layout]))[0]["cells"] == [2, 3, 4]


def test_boxed_grid_frame_draws_every_edge():
    html = "<table><tr><td>Nature</td><td>Services</td></tr><tr><td>Terms</td><td>30 days</td></tr></table>"
    out = compile_layouts(html, [TableLayout(table=0, columns=["label", "text"], frame="grid")])
    assert out.count("border-left: 0.75px solid #000000") >= 4
    assert [r["below_row"] for r in table_rules(out)] == [0, 1, 2]
