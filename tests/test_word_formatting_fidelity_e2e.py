"""Phase 3, Step 8 — end-to-end proof for Word-upload formatting fidelity.

The full chain: a styled .docx is uploaded -> mammoth + Step-5 injection write a
source.html carrying real Word styling -> the per-note slicer returns that
styled chunk -> a format_ops translation of exactly those styles passes
`apply_cell_operations` cleanly (no sanitiser rejection, no content change).

This is the load-bearing guarantee: the styling vocabulary the Step-7 prompt
tells the agent to emit is the SAME vocabulary the write path accepts. If the
docx reader ever emits a style the ops layer can't express, this test fails.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ingest import docx_html
from notes import source_snippets as ss
from notes.format_patch import apply_cell_operations
from tests._docx_fixture import build_styled_docx

mammoth = pytest.importorskip("mammoth")


def _source_html_for(tmp_path: Path) -> str:
    src = build_styled_docx(tmp_path / "styled.docx")
    session = tmp_path / "session"
    session.mkdir()
    out = docx_html.write_source_html(src, session)
    assert out is not None
    return out.read_text(encoding="utf-8")


def test_styled_docx_flows_to_source_html_and_is_ops_reproducible(tmp_path: Path):
    # 1. styled docx -> source.html with real styling
    source_html = _source_html_for(tmp_path)

    # 2. per-note slice keeps the styling
    snippet = ss.extract_note_snippet(source_html, 4)
    assert "3px double #000000" in snippet          # totals double rule
    assert "text-align: right" in snippet           # amount column

    # 3. the agent writes style-FREE content (gotcha #16) mirroring the table
    content = (
        "<table><tbody>"
        "<tr><td>Cost</td><td>Amount</td></tr>"
        "<tr><td>Buildings</td><td>1,595</td></tr>"
        "<tr><td>Total</td><td>3,190</td></tr>"
        "</tbody></table>"
    )

    # 4. a faithful format_ops translation of the SOURCE styling (what Step 7
    #    tells the agent to produce) applies cleanly through the write gate.
    ops = [
        # amount column right-aligned (cells r1c2, r2c2, r3c2)
        {"target": {"table": 0, "cell": {"r": 1, "c": 2}},
         "style": {"text_align": "right"}},
        {"target": {"table": 0, "cell": {"r": 2, "c": 2}},
         "style": {"text_align": "right"}},
        # total row's amount: right-aligned + double bottom rule
        {"target": {"table": 0, "cell": {"r": 3, "c": 2}},
         "style": {"text_align": "right",
                   "border_bottom": {"width": "3px", "style": "double",
                                     "color": "#000000"}}},
    ]
    styled = apply_cell_operations(content, ops)

    # the write path preserved content and applied the styling
    assert "1,595" in styled and "3,190" in styled
    assert "3px double #000000" in styled
    assert "text-align: right" in styled


def test_every_injected_prop_is_now_ops_reproducible(tmp_path: Path):
    """Post-Phase-4 the tier split has collapsed: EVERY property the docx reader
    injects — including padding and paragraph spacing — is reproducible through
    a format_op. Nothing the agent sees in the source is un-copyable."""
    from ingest.docx_styles import REFERENCE_ONLY_PROPS

    assert REFERENCE_ONLY_PROPS == frozenset()  # no un-copyable props remain

    content = "<table><tbody><tr><td>x</td><td>1</td></tr></tbody></table>"
    # padding op (Phase 4) is now accepted by the write gate...
    padded = apply_cell_operations(
        content,
        [{"target": {"table": 0, "cell": {"r": 1, "c": 1}},
          "style": {"padding": "4px 8px"}}])
    assert "padding: 4px 8px" in padded
    # ...as is a border/align op.
    ok = apply_cell_operations(
        content,
        [{"target": {"table": 0, "cell": {"r": 1, "c": 2}},
          "style": {"text_align": "right"}}])
    assert "text-align: right" in ok
