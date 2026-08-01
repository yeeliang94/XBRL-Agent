"""Table index — PLAN-notes-source-integrity-build Phase 2, Steps 2.1 / 2.4.

The review surface needs to talk about ONE table inside a cell that may hold
several. Two constraints shape this module:

1. **The index must match `format_ops`.** `notes/format_patch.py` resolves
   `target.table` with a flat, document-order `soup.find_all("table")`
   including nested tables. If the review UI numbered tables differently,
   "table 2" would mean two different tables in the two places and an operator
   restyling from the UI would hit the wrong one.

2. **Style is per CELL in the database, not per table.** `notes_cells` carries
   one `style_source` and one `source_pages` for the whole cell. So per-table
   style state is DERIVED from the markup here, and page evidence stays
   cell-level until block provenance lands (plan Phase 4).
"""
import pytest

from notes.table_index import index_tables

_SOURCE_STYLED = (
    '<table data-source-styled="true">'
    '<tr><td style="border-bottom:1px solid #000">A</td><td>1,000</td></tr>'
    "</table>"
)
_PLAIN = "<table><tr><td>A</td><td>1,000</td></tr></table>"
_STYLED = (
    '<table><tr><td style="border-bottom:1px solid #000">A</td>'
    '<td style="background-color:#eee">1,000</td></tr></table>'
)


def test_no_tables_gives_no_entries():
    assert index_tables("<p>Just prose.</p>") == []
    assert index_tables("") == []
    assert index_tables(None) == []


def test_index_matches_format_patch_enumeration():
    """Flat document order, zero-based, nested tables included — the same
    list `format_patch._resolve_target` walks."""
    from bs4 import BeautifulSoup

    html = (
        "<table><tr><td>outer-a</td></tr></table>"
        "<table><tr><td><table><tr><td>inner</td></tr></table></td></tr></table>"
    )
    entries = index_tables(html)
    soup_count = len(BeautifulSoup(html, "html.parser").find_all("table"))
    assert [e.table_index for e in entries] == list(range(soup_count))
    assert soup_count == 3


def test_depth_is_reported_separately_from_index():
    html = (
        "<table><tr><td>outer</td></tr></table>"
        "<table><tr><td><table><tr><td>inner</td></tr></table></td></tr></table>"
    )
    entries = index_tables(html)
    assert [e.depth for e in entries] == [0, 0, 1]


def test_geometry_counts_rows_and_columns():
    html = (
        "<table>"
        "<tr><td>h1</td><td>h2</td><td>h3</td></tr>"
        "<tr><td>a</td><td>b</td><td>c</td></tr>"
        "</table>"
    )
    e = index_tables(html)[0]
    assert (e.rows, e.cols, e.cells) == (2, 3, 6)


def test_colspan_counts_towards_column_width():
    html = (
        "<table>"
        '<tr><td colspan="3">spanning header</td></tr>'
        "<tr><td>a</td><td>b</td><td>c</td></tr>"
        "</table>"
    )
    e = index_tables(html)[0]
    assert e.cols == 3, "a 3-wide colspan row is 3 columns, not 1"
    assert "ragged_rows" not in e.flags


def test_nested_table_cells_are_not_counted_in_the_parent():
    html = (
        "<table><tr><td>a</td>"
        "<td><table><tr><td>x</td><td>y</td></tr></table></td>"
        "</tr></table>"
    )
    outer, inner = index_tables(html)
    assert outer.cells == 2, "the parent owns its own 2 cells, not the child's"
    assert inner.cells == 2


# --------------------------------------------------------------------------
# style state — derived from markup, because the DB has one verdict per cell
# --------------------------------------------------------------------------

def test_source_styled_table_is_reported_as_source():
    e = index_tables(_SOURCE_STYLED)[0]
    assert e.style_state == "source"
    assert e.source_styled is True


def test_plain_table_is_reported_as_plain():
    e = index_tables(_PLAIN)[0]
    assert e.style_state == "plain"
    assert e.has_inline_borders is False
    assert e.has_fills is False


def test_styled_table_is_reported_as_styled():
    e = index_tables(_STYLED)[0]
    assert e.style_state == "styled"
    assert e.has_inline_borders is True
    assert e.has_fills is True


def test_one_styled_table_does_not_make_its_neighbour_styled():
    """The cell-level `style_source` cannot express this; the whole point of
    deriving per table is that a cell with a styled and a plain table shows
    both truthfully."""
    entries = index_tables(_STYLED + _PLAIN)
    assert [e.style_state for e in entries] == ["styled", "plain"]


def test_source_styled_wins_over_incidental_inline_style():
    e = index_tables(_SOURCE_STYLED)[0]
    assert e.style_state == "source", "a copied table is 'source', not 'styled'"


def test_source_styled_marker_must_be_true():
    """The sanitiser value-checks this attribute; a stray value is not a
    verbatim-copy claim."""
    e = index_tables('<table data-source-styled="maybe"><tr><td>a</td></tr></table>')[0]
    assert e.source_styled is False


# --------------------------------------------------------------------------
# advisory flags (Step 2.4) — surfaced, never enforced
# --------------------------------------------------------------------------

def test_ragged_rows_are_flagged():
    html = (
        "<table>"
        "<tr><td>a</td><td>b</td><td>c</td></tr>"
        "<tr><td>a</td><td>b</td></tr>"
        "</table>"
    )
    assert "ragged_rows" in index_tables(html)[0].flags


def test_single_row_table_is_flagged():
    assert "single_row" in index_tables("<table><tr><td>a</td></tr></table>")[0].flags


def test_table_with_no_numbers_is_flagged():
    html = "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"
    assert "no_numeric_cells" in index_tables(html)[0].flags


def test_table_with_numbers_is_not_flagged():
    html = (
        "<table><tr><td>Trade receivables</td><td>1,595</td></tr>"
        "<tr><td>Prepayments</td><td>(240)</td></tr></table>"
    )
    assert "no_numeric_cells" not in index_tables(html)[0].flags


def test_a_clean_table_carries_no_flags():
    html = (
        "<table>"
        "<tr><td>Item</td><td>2021</td></tr>"
        "<tr><td>Trade receivables</td><td>1,595</td></tr>"
        "<tr><td>Total</td><td>1,595</td></tr>"
        "</table>"
    )
    assert index_tables(html)[0].flags == []


def test_oversized_table_is_flagged():
    big = "<table><tr><td>" + ("x" * 31_000) + "</td></tr></table>"
    assert "oversized" in index_tables(big)[0].flags


def test_malformed_html_does_not_raise():
    """Cell HTML comes from a model and from a human editor. An index that
    crashes takes the whole Notes tab down with it."""
    for bad in ["<table><tr><td>unclosed", "<table>", "<<>>", "<table><td>x</td>"]:
        index_tables(bad)  # must not raise


# --------------------------------------------------------------------------
# Review finding: an explicitly-blank table is not a styled table.
#
# gotcha #16 records two deliberate conventions that a naive substring match
# gets exactly backwards:
#   * "no fill" persists as `background-color: transparent`, NOT as an absent
#     attribute;
#   * erasing an edge uses `1px hidden`, NOT `none`, because under
#     border-collapse a neighbour's grid line out-prioritises `none`.
# So the markup that means "this table is deliberately blank" contains the
# words `background-color` and `border`. Reading those as "styled" hides the
# table from the needs-a-look filter — the one place it should appear.
# --------------------------------------------------------------------------

def test_transparent_fill_is_not_a_fill():
    html = ('<table><tr><td style="background-color: transparent">a</td>'
            "<td>1</td></tr><tr><td>b</td><td>2</td></tr></table>")
    e = index_tables(html)[0]
    assert e.has_fills is False
    assert e.style_state == "plain"


def test_hidden_border_is_not_a_border():
    html = ('<table><tr><td style="border-bottom: 1px hidden #000000">a</td>'
            "<td>1</td></tr><tr><td>b</td><td>2</td></tr></table>")
    e = index_tables(html)[0]
    assert e.has_inline_borders is False
    assert e.style_state == "plain"


def test_none_border_is_not_a_border():
    html = ('<table><tr><td style="border: none">a</td><td>1</td></tr>'
            "<tr><td>b</td><td>2</td></tr></table>")
    assert index_tables(html)[0].has_inline_borders is False


def test_a_real_border_is_still_detected():
    html = ('<table><tr><td style="border-bottom: 1px solid #000000">a</td>'
            "<td>1</td></tr><tr><td>b</td><td>2</td></tr></table>")
    e = index_tables(html)[0]
    assert e.has_inline_borders is True
    assert e.style_state == "styled"


def test_a_real_fill_is_still_detected():
    html = ('<table><tr><td style="background-color: #eeeeee">a</td>'
            "<td>1</td></tr><tr><td>b</td><td>2</td></tr></table>")
    e = index_tables(html)[0]
    assert e.has_fills is True


def test_padding_alone_is_not_styling():
    """Padding is spacing, not a visible rule or fill — it must not promote a
    plain table out of the needs-a-look filter."""
    html = ('<table><tr><td style="padding: 4px 8px">a</td><td>1</td></tr>'
            "<tr><td>b</td><td>2</td></tr></table>")
    assert index_tables(html)[0].style_state == "plain"


# --------------------------------------------------------------------------
# Peer-review finding 4: per-side border resolution.
#
# gotcha #16 records that the browser COLLAPSES uniform per-side borders into
# the grouped longhands, so these are the forms that actually arrive from a
# human edit — not a hypothetical.
# --------------------------------------------------------------------------

def _one(css: str):
    return index_tables(
        f'<table><tr><td style="{css}">a</td><td>1</td></tr>'
        "<tr><td>b</td><td>2</td></tr></table>"
    )[0]


def test_grouped_longhand_hidden_is_not_a_border():
    """`border-width:1px` reads as a real border only if you stop at width."""
    assert _one("border-width:1px; border-style:hidden; border-color:#000"
                ).has_inline_borders is False


def test_grouped_longhand_solid_is_a_border():
    assert _one("border-width:1px; border-style:solid; border-color:#000"
                ).has_inline_borders is True


def test_one_visible_side_among_hidden_ones_counts():
    """`hidden solid hidden hidden` has a real right edge — a whole-string
    test for the word 'hidden' calls this blank, which is the opposite error."""
    assert _one("border-style: hidden solid hidden hidden").has_inline_borders is True


def test_all_four_sides_hidden_is_blank():
    assert _one("border-style: hidden hidden hidden hidden"
                ).has_inline_borders is False


def test_width_without_style_paints_nothing():
    """CSS's initial border-style is `none`, so a width alone draws no line."""
    assert _one("border-width: 2px").has_inline_borders is False


def test_zero_width_with_a_visible_style_paints_nothing():
    assert _one("border-style: solid; border-width: 0").has_inline_borders is False


def test_a_later_declaration_overrides_an_earlier_one():
    assert _one("border-style: solid; border-style: hidden"
                ).has_inline_borders is False
    assert _one("border-style: hidden; border-style: solid"
                ).has_inline_borders is True


def test_per_side_shorthand_is_resolved():
    assert _one("border-bottom: 1px solid #000000").has_inline_borders is True
    assert _one("border-bottom: 1px hidden #000000").has_inline_borders is False


def test_border_colour_alone_paints_nothing():
    assert _one("border-color: rgb(0, 0, 0)").has_inline_borders is False


def test_border_radius_and_collapse_are_not_lines():
    assert _one("border-collapse: collapse; border-radius: 4px"
                ).has_inline_borders is False


def test_all_sides_shorthand_then_one_side_erased():
    """The common real shape: a boxed cell with its bottom edge removed."""
    e = _one("border: 1px solid #000000; border-bottom: 1px hidden #000000")
    assert e.has_inline_borders is True, "three sides still paint"
