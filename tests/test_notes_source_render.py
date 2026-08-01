"""Deterministic rendering from source blocks — plan Phase 6, Step 6.3.

The claim this module has to earn is "the cell says what the source says". So
the tests check text equality against the source, byte-stability across two
renders, and that the three ways a render could quietly lie — reordering,
half a split table, a silent truncation — all fail instead.
"""
from __future__ import annotations

import pytest

from notes import source_render as sr
from notes.html_to_text import rendered_length
from notes.source_models import ContentOrigin, SourceBlock


def _b(i: int, html: str, *, group=None, kind="paragraph") -> SourceBlock:
    return SourceBlock(
        block_id=f"b{i:03d}", block_kind=kind, reading_order=i,
        canonical_html=html, table_group_id=group,
    )


BLOCKS = [
    _b(0, "<h3>5. Receivables</h3>", kind="heading"),
    _b(1, "<p>Trade receivables are stated at cost.</p>"),
    _b(2, '<table><tr><td>Trade</td><td>1,595</td></tr></table>', kind="table"),
    _b(3, "<p>No amounts are past due.</p>"),
]


def test_blocks_render_in_reading_order_not_the_order_named():
    """An agent that reorders paragraphs is rewriting the note, not selecting
    from it — so the renderer, not the agent, decides sequence."""
    out = sr.render_blocks(BLOCKS, ["b003", "b000", "b001"])
    assert out.block_ids == ["b000", "b001", "b003"]
    assert out.text.index("Receivables") < out.text.index("past due")


def test_the_same_selection_renders_identically_twice():
    a = sr.render_blocks(BLOCKS, ["b000", "b001", "b002"])
    b = sr.render_blocks(BLOCKS, ["b000", "b001", "b002"])
    assert a.html == b.html
    assert a.source_rendered_sha256 == b.source_rendered_sha256


def test_a_different_selection_hashes_differently():
    a = sr.render_blocks(BLOCKS, ["b000", "b001"])
    b = sr.render_blocks(BLOCKS, ["b000", "b001", "b003"])
    assert a.source_rendered_sha256 != b.source_rendered_sha256


def test_rendered_text_matches_the_source_text():
    ids = [b.block_id for b in BLOCKS]
    out = sr.render_blocks(BLOCKS, ids)
    assert out.text.split() == sr.source_text_of(BLOCKS).split()


def test_an_unknown_block_id_is_refused():
    with pytest.raises(sr.BlockSelectionError):
        sr.render_blocks(BLOCKS, ["b000", "b999"])


def test_a_duplicate_id_renders_once():
    out = sr.render_blocks(BLOCKS, ["b001", "b001"])
    assert out.block_ids == ["b001"]
    assert out.text.count("stated at cost") == 1


def test_content_origin_is_source_exact():
    out = sr.render_blocks(BLOCKS, ["b001"])
    assert out.content_origin is ContentOrigin.SOURCE_EXACT


# --------------------------------------------------------------------------
# table groups
# --------------------------------------------------------------------------

GROUPED = [
    _b(0, '<table style="width:100%"><tr><td>a</td><td>1</td></tr>'
          "</table>", group="tg-1", kind="table"),
    _b(1, "<table><tr><td>b</td><td>2</td></tr></table>", group="tg-1",
       kind="table"),
    _b(2, "<p>after</p>"),
]


def test_a_split_table_rejoins_into_one_table():
    out = sr.render_blocks(GROUPED, ["b000", "b001", "b002"])
    assert out.html.count("<table") == 1
    assert out.html.count("<tr") == 2
    assert "a" in out.text and "b" in out.text


def test_the_first_fragments_table_attributes_survive_the_join():
    """The opening tag of the first fragment wins, so the group keeps its
    shape. (Only sanitiser-permitted table properties survive, per gotcha #16 —
    `width` is on that list.)"""
    out = sr.render_blocks(GROUPED, ["b000", "b001"])
    assert "width" in out.html


def test_ungrouped_tables_stay_separate():
    blocks = [
        _b(0, "<table><tr><td>a</td></tr></table>", kind="table"),
        _b(1, "<table><tr><td>b</td></tr></table>", kind="table"),
    ]
    out = sr.render_blocks(blocks, ["b000", "b001"])
    assert out.html.count("<table") == 2


# --------------------------------------------------------------------------
# formatting and gotcha #16
# --------------------------------------------------------------------------

def test_a_source_styled_table_keeps_its_declarations_and_is_marked():
    blocks = [_b(0, '<table><tr><td style="border-bottom:1px solid #000">x</td>'
                    "</tr></table>", kind="table")]
    out = sr.render_blocks(blocks, ["b000"])
    assert "border-bottom" in out.html
    assert 'data-source-styled="true"' in out.html
    assert out.style_source == "source"


def test_prose_inline_styles_are_stripped_but_table_ones_are_not():
    """Gotcha #16 — the narrowing is TABLES ONLY, enforced in code."""
    blocks = [
        _b(0, '<p style="text-align:center">centred prose</p>'),
        _b(1, '<table><tr><td style="text-align:right">1</td></tr></table>',
           kind="table"),
    ]
    out = sr.render_blocks(blocks, ["b000", "b001"])
    assert "centred prose" in out.text
    assert "text-align" not in out.html.split("<table")[0]
    assert "text-align" in out.html.split("<table", 1)[1]


def test_invalid_format_ops_degrade_to_plain_and_never_raise():
    out = sr.render_blocks(
        BLOCKS, ["b002"], format_ops=[{"op": "nonsense"}], row_label="Note 5",
    )
    assert out.html
    assert out.style_source in ("unstyled", "source")
    assert any("format_ops" in w or "renders plain" in w for w in out.warnings)


def test_valid_format_ops_are_applied():
    ops = [{
        "op": "set_cell_style",
        "target": {"table": 0, "rows": [1]},   # rows are 1-based
        "style": {
            "border_bottom": {"width": "1px", "style": "solid", "color": "#000000"}
        },
    }]
    out = sr.render_blocks(BLOCKS, ["b002"], format_ops=ops)
    assert out.style_source == "ops"
    assert "border-bottom" in out.html


# --------------------------------------------------------------------------
# the oversized-note decision (Step 0.6)
# --------------------------------------------------------------------------

def test_an_oversized_render_is_flagged_and_never_cut_short():
    """The chosen path: cap the new route, flag the rest. A short cell that
    reports complete is exactly what this feature exists to stop."""
    big = [_b(0, "<p>" + ("word " * 9000) + "</p>")]
    out = sr.render_blocks(big, ["b000"])
    assert out.oversized is True
    assert out.usable is False
    assert rendered_length(out.html) == out.rendered_chars
    assert out.rendered_chars > sr.CELL_CHAR_LIMIT, "not truncated"
    assert any("over the" in w for w in out.warnings)


def test_a_note_under_the_cap_is_usable():
    out = sr.render_blocks(BLOCKS, ["b000", "b001"])
    assert out.oversized is False
    assert out.usable is True


def test_an_empty_selection_is_not_usable():
    out = sr.render_blocks(BLOCKS, [])
    assert out.usable is False


def test_the_cap_is_configurable_for_callers_that_need_a_tighter_one():
    out = sr.render_blocks(BLOCKS, ["b000", "b001"], cap=10)
    assert out.oversized is True


def test_the_render_hash_changes_with_the_render_version(monkeypatch):
    """A render-shape change must read as 'the render moved', not as a human
    edit that diverged from source."""
    first = sr.render_sha256("<p>x</p>")
    monkeypatch.setattr(sr, "RENDER_VERSION", "src-render-99")
    assert sr.render_sha256("<p>x</p>") != first
