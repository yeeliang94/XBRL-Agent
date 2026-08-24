"""Phase 3, Step 8 — end-to-end proof for Word-upload formatting fidelity.

The full chain: a styled .docx is uploaded -> mammoth writes source.html with
real Word styling -> the per-note slicer returns that styled chunk -> the notes
writer preserves the table markup and marks it as source-styled.

Extraction agents no longer translate styling into a second representation.
The source table itself is the fidelity contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ingest import docx_html
from notes import source_snippets as ss
from notes.format_patch import apply_cell_operations
from notes.payload import NotesPayload
from notes.writer import _sanitize_payload, _style_cell_html
from tests._docx_fixture import build_styled_docx

mammoth = pytest.importorskip("mammoth")


def _source_html_for(tmp_path: Path) -> str:
    src = build_styled_docx(tmp_path / "styled.docx")
    session = tmp_path / "session"
    session.mkdir()
    out = docx_html.write_source_html(src, session)
    assert out is not None
    return out.read_text(encoding="utf-8")


def test_styled_docx_flows_to_source_html_and_writer_passthrough(tmp_path: Path):
    # 1. styled docx -> source.html with real styling
    source_html = _source_html_for(tmp_path)

    # 2. per-note slice keeps the styling
    snippet = ss.extract_note_snippet(source_html, 4)
    assert "3px double #000000" in snippet          # totals double rule
    assert "text-align: right" in snippet           # amount column

    # 3. source markup passes through the ordinary writer sanitiser.
    payload = NotesPayload(
        chosen_row_label="Property, plant and equipment",
        content=snippet,
        evidence="source document",
        source_pages=[4],
        note_num=4,
        source_note_refs=["4"],
        parent_note={"number": "4", "title": "Property, plant and equipment"},
    )
    warnings: list[str] = []
    sanitized = _sanitize_payload(payload, warnings)
    styled, style_source = _style_cell_html(
        sanitized.content, None, payload.chosen_row_label, warnings,
    )

    assert style_source == "source"
    assert 'data-source-styled="true"' in styled
    assert "3px double #000000" in styled
    assert "text-align: right" in styled
    assert not any("style" in warning.lower() for warning in warnings)


def test_legacy_ops_remain_reproducible():
    """The retired sidecar remains readable for legacy stored payloads."""
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
