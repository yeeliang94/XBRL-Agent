"""The AI's table description compiles to formatting, or fails without changing geometry."""
import re

import pytest
from bs4 import BeautifulSoup

from notes.format_verify import verify_format_only
from notes.rule_map import table_rules
from notes.table_layout import (
    LayoutError, LineSpec, RowSpec, TableLayout, compile_layouts, validate_layouts,
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
    assert align(grid[1][0][1]) == "left" and "<strong>2024</strong>" in out
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


def test_source_bold_is_never_removed():
    html = ROLL.replace("<td>At 31 December</td>", "<td><strong>At 31 December</strong></td>")
    out = compile_layouts(html, [TableLayout(table=0, columns=["label", "amount", "amount"])])
    assert "<strong>At 31 December</strong>" in out


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
