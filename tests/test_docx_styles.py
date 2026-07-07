"""Step 4 — the docx visual-style reader (ingest/docx_styles.py).

Pins: real-fixture border/alignment/double extraction; the ONE level of
table-style inheritance FINCO's direct-formatting fixture can't exercise (a
synthetic docx whose grid comes from a referenced <w:tblStyle>); the CSS
translation vocabulary; and a drift guard proving every Tier-1 property the
reader emits is one the notes sanitiser actually accepts.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from ingest.docx_styles import (
    REFERENCE_ONLY_PROPS,
    TIER1_BLOCK_PROPS,
    TIER1_CELL_PROPS,
    cell_css,
    extract_style_maps,
    para_css,
)

FINCO = Path(__file__).resolve().parents[1] / "FINCO-Audited-Financial-Statement-2021.docx"

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _make_docx(document_body: str, styles_xml: str | None = None) -> bytes:
    """Minimal in-memory .docx carrying just the parts extract_style_maps reads
    (word/document.xml [+ word/styles.xml]). Not a mammoth-valid document —
    this exercises the XML reader only."""
    doc = (
        f'<w:document {_W}><w:body>{document_body}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", doc)
        if styles_xml is not None:
            zf.writestr("word/styles.xml", styles_xml)
    return buf.getvalue()


def _cell(borders_xml: str = "", jc: str = "", tc_extra: str = "") -> str:
    ppr = f"<w:pPr>{jc}</w:pPr>" if jc else ""
    return (
        f"<w:tc><w:tcPr>{borders_xml}{tc_extra}</w:tcPr>"
        f"<w:p>{ppr}<w:r><w:t>x</w:t></w:r></w:p></w:tc>"
    )


# --- real FINCO fixture ------------------------------------------------------
@pytest.mark.skipif(not FINCO.exists(), reason="FINCO docx fixture absent")
def test_finco_borders_alignment_and_double_rule_extracted():
    maps = extract_style_maps(FINCO)
    assert len(maps.tables) == 21
    seen_double = seen_single = seen_align = False
    for table in maps.tables:
        for row in table:
            for cell in row:
                css = cell_css(cell)
                seen_double |= "3px double #000000" in css
                seen_single |= "solid #000000" in css
                seen_align |= "text-align: right" in css
    assert seen_double and seen_single and seen_align


# --- table-style inheritance (the path FINCO's direct formatting can't hit) --
def test_cell_inherits_borders_from_referenced_table_style():
    styles = (
        f'<w:styles {_W}><w:style w:type="table" w:styleId="Grid">'
        "<w:tblPr><w:tblBorders>"
        '<w:top w:val="single" w:color="000000" w:sz="4"/>'
        '<w:bottom w:val="single" w:color="000000" w:sz="4"/>'
        "</w:tblBorders></w:tblPr></w:style></w:styles>"
    )
    body = (
        '<w:tbl><w:tblPr><w:tblStyle w:val="Grid"/></w:tblPr>'
        f"<w:tr>{_cell()}</w:tr></w:tbl>"
    )
    maps = extract_style_maps(_bytes_docx(body, styles))
    css = cell_css(maps.tables[0][0][0])
    # The cell carries NO direct borders — they come from the table style.
    assert "border-top: 1px solid #000000" in css
    assert "border-bottom: 1px solid #000000" in css


def test_direct_cell_border_overrides_table_style():
    styles = (
        f'<w:styles {_W}><w:style w:type="table" w:styleId="Grid">'
        "<w:tblPr><w:tblBorders>"
        '<w:bottom w:val="single" w:color="000000" w:sz="4"/>'
        "</w:tblBorders></w:tblPr></w:style></w:styles>"
    )
    cell = _cell(
        '<w:tcBorders><w:bottom w:val="double" w:color="000000" w:sz="6"/>'
        "</w:tcBorders>")
    body = (
        '<w:tbl><w:tblPr><w:tblStyle w:val="Grid"/></w:tblPr>'
        f"<w:tr>{cell}</w:tr></w:tbl>"
    )
    maps = extract_style_maps(_bytes_docx(body, styles))
    css = cell_css(maps.tables[0][0][0])
    assert "border-bottom: 3px double #000000" in css  # cell wins


def test_nil_border_emits_nothing():
    cell = _cell(
        '<w:tcBorders><w:top w:val="nil"/><w:bottom w:val="nil"/>'
        "<w:left w:val=\"nil\"/><w:right w:val=\"nil\"/></w:tcBorders>")
    body = f"<w:tbl><w:tr>{cell}</w:tr></w:tbl>"
    maps = extract_style_maps(_bytes_docx(body))
    assert cell_css(maps.tables[0][0][0]) == ""  # no border, no fill, no align


def test_alignment_and_fill_mapping():
    cell = _cell(
        jc='<w:jc w:val="center"/>',
        tc_extra='<w:shd w:fill="D9D9D9"/>')
    body = f"<w:tbl><w:tr>{cell}</w:tr></w:tbl>"
    maps = extract_style_maps(_bytes_docx(body))
    css = cell_css(maps.tables[0][0][0])
    assert "text-align: center" in css
    assert "background-color: #d9d9d9" in css


def test_paragraph_alignment_indent_and_spacing():
    body = (
        "<w:p><w:pPr>"
        '<w:jc w:val="right"/><w:ind w:left="360"/>'
        '<w:spacing w:before="0" w:after="200"/>'
        "</w:pPr><w:r><w:t>hi</w:t></w:r></w:p>"
    )
    maps = extract_style_maps(_bytes_docx(body))
    assert len(maps.paragraphs) == 1
    css = para_css(maps.paragraphs[0])
    assert "text-align: right" in css
    assert "margin-left: 24px" in css       # 360 twips / 15
    assert "margin-bottom: 13px" in css      # 200 twips / 15 (reference-only)


# --- vocabulary drift guard --------------------------------------------------
def test_emitted_props_match_the_sanitiser_whitelist():
    """Every Tier-1 property the reader emits must be one the notes sanitiser
    accepts on a table/block tag — otherwise an injected style would be silently
    stripped on write. Reference-only props (padding/margins) are the documented
    exception (Phase 4)."""
    from notes.html_sanitize import _STYLE_PROPS_BY_TAG

    td_props = _STYLE_PROPS_BY_TAG["td"]
    block_props = _STYLE_PROPS_BY_TAG["p"]
    # Tier-1 cell props are all sanitiser-accepted on <td>.
    assert TIER1_CELL_PROPS <= td_props
    # Tier-1 block props are all sanitiser-accepted on <p>.
    assert TIER1_BLOCK_PROPS <= block_props
    # Reference-only props are NOT (yet) accepted — that's why they're flagged.
    assert not (REFERENCE_ONLY_PROPS & td_props)
    assert not (REFERENCE_ONLY_PROPS & block_props)


# convenience: build a bytes-docx and hand extract_style_maps a path-like
def _bytes_docx(body: str, styles: str | None = None):
    import tempfile
    data = _make_docx(body, styles)
    f = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    f.write(data)
    f.close()
    return f.name
