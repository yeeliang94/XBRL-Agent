"""One physical line per shared edge, named by the row it sits below."""
from notes.rule_map import rule_map_text, table_rules


def test_mirrored_edge_is_one_line_below_the_upper_row():
    # Runs 347-349: bottom-of-row-2 plus top-of-row-3 read as two rules.
    html = ('<table><tr><th></th><th>2024</th></tr>'
            '<tr><td>Prepayments</td><td style="border-bottom: 0.75px solid #000000">108</td></tr>'
            '<tr><td>Total</td><td style="border-top: 0.75px solid #000000">211</td></tr></table>')
    assert table_rules(html) == [{
        "table": 0, "below_row": 2, "row_label": "Prepayments | 108",
        "cells": [2], "columns": [2, 2], "kind": "single",
    }]


def test_side_by_side_single_and_double_lines_stay_separate():
    html = ('<table><tr><td>A</td><td style="border-bottom: 1px solid #000">1</td>'
            '<td style="border-bottom: 3px double #000">2</td></tr>'
            '<tr><td>B</td><td>3</td><td>4</td></tr></table>')
    assert [(r["cells"], r["kind"]) for r in table_rules(html)] == [([2], "single"), ([3], "double")]


def test_partial_line_below_a_merged_heading_keeps_its_span():
    html = ('<table><tr><th></th><th colspan="2">Company</th></tr>'
            '<tr><td>Cash</td><td style="border-top: 1px solid #000">1</td><td>2</td></tr></table>')
    [rule] = table_rules(html)
    assert rule["cells"] == [2] and rule["columns"] == [2, 2]
    assert "(grid column 2)" in rule_map_text(html)


def test_collapse_winner_hidden_white_and_double():
    html = ('<table><tr><td>A</td><td style="border-bottom: 3px double #000000">1</td></tr>'
            '<tr><td style="border-top: 0.75px solid #ffffff">B</td>'
            '<td style="border-bottom: 1px solid #000; border-top: 0.75px solid #000">2</td></tr>'
            '<tr><td>C</td><td style="border-top: 1px hidden #000000">3</td></tr></table>')
    rules = table_rules(html)
    # Double wins over a thinner opposite side; white is transport, not a line;
    # a deliberate hidden clear removes the opposite line, as it does on screen.
    assert [(r["below_row"], r["cells"], r["kind"]) for r in rules] == [(1, [2], "double")]


def test_no_rules_is_stated_explicitly():
    assert rule_map_text("<table><tr><td>A</td></tr></table>") == "no explicit lines"
