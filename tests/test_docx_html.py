"""Tests for ingest.docx_html (mammoth sidecar, PLAN-word-input Phase 2 Step 7)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ingest import docx_html
from tests._docx_fixture import build_minimal_docx, build_styled_docx

mammoth = pytest.importorskip("mammoth")


def test_extract_preserves_tables_and_prose(tmp_path: Path):
    src = build_minimal_docx(tmp_path / "fs.docx")
    html = docx_html.extract_docx_html(src)
    assert "<table" in html.lower()  # the note-4 table survived
    assert "1,595" in html  # a table value survived
    assert "property, plant and equipment" in html.lower()  # heading text survived


def test_write_source_html_creates_sidecar(tmp_path: Path):
    src = build_minimal_docx(tmp_path / "fs.docx")
    session = tmp_path / "session"
    session.mkdir()
    out = docx_html.write_source_html(src, session)
    assert out == session / docx_html.SOURCE_HTML_NAME
    assert out.exists() and out.read_text(encoding="utf-8").strip()


def test_write_source_html_is_best_effort_on_bad_input(tmp_path: Path):
    bad = tmp_path / "not-a-docx.docx"
    bad.write_bytes(b"this is not a zip")
    session = tmp_path / "session"
    session.mkdir()
    # Must not raise — returns None and writes nothing.
    assert docx_html.write_source_html(bad, session) is None
    assert not (session / docx_html.SOURCE_HTML_NAME).exists()


def test_extract_refuses_oversized_document_xml(tmp_path: Path, monkeypatch):
    # The 50MB upload cap bounds only the COMPRESSED size; a docx can inflate to
    # gigabytes (zip bomb). The guard reads the zip central directory's declared
    # UNCOMPRESSED sizes and refuses before mammoth decompresses a byte.
    src = build_minimal_docx(tmp_path / "fs.docx")
    monkeypatch.setattr(docx_html, "_MAX_DOCUMENT_XML_BYTES", 100)
    with pytest.raises(RuntimeError):
        docx_html.extract_docx_html(src)


def test_extract_refuses_oversized_total(tmp_path: Path, monkeypatch):
    src = build_minimal_docx(tmp_path / "fs.docx")
    monkeypatch.setattr(docx_html, "_MAX_UNCOMPRESSED_TOTAL_BYTES", 10)
    with pytest.raises(RuntimeError):
        docx_html.extract_docx_html(src)


def test_write_source_html_skips_zip_bomb(tmp_path: Path, monkeypatch):
    # A guard trip must degrade to None (no sidecar), never raise into the
    # upload path — extraction is best-effort.
    src = build_minimal_docx(tmp_path / "fs.docx")
    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.setattr(docx_html, "_MAX_DOCUMENT_XML_BYTES", 100)
    assert docx_html.write_source_html(src, session) is None
    assert not (session / docx_html.SOURCE_HTML_NAME).exists()


def test_write_source_html_caps_output_size(tmp_path: Path, monkeypatch):
    # A pathological (non-bomb) document must not produce a source.html big
    # enough to hurt when read_note_snippet re-reads it whole on every call.
    src = build_minimal_docx(tmp_path / "fs.docx")
    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.setattr(docx_html, "_MAX_SOURCE_HTML_CHARS", 20)
    out = docx_html.write_source_html(src, session)
    assert out is not None
    assert len(out.read_text(encoding="utf-8")) <= 20


# --- Step 5: source-style injection -----------------------------------------
def test_injection_carries_word_table_styling_into_html(tmp_path: Path):
    """mammoth strips visual styling; Step-5 injection puts the real Word
    borders/alignment/double-rule back onto the cells so the agent can COPY
    them instead of inferring from a bare skeleton."""
    src = build_styled_docx(tmp_path / "styled.docx")
    html = docx_html.extract_docx_html(src)
    # right-aligned amount column
    assert "text-align: right" in html
    # the total row's double rule survived
    assert "3px double #000000" in html
    # a single top rule survived
    assert "1px solid #000000" in html


def test_injected_props_are_all_sanitiser_acceptable_or_reference_only(
    tmp_path: Path,
):
    """Every property injected onto a cell must be Tier-1 (sanitiser-accepted on
    write) or an explicitly documented reference-only prop — never a silent
    third category that would be stripped on write with no plan to support it."""
    import re

    from ingest.docx_styles import REFERENCE_ONLY_PROPS, TIER1_CELL_PROPS

    src = build_styled_docx(tmp_path / "styled.docx")
    html = docx_html.extract_docx_html(src)
    known = TIER1_CELL_PROPS | REFERENCE_ONLY_PROPS
    for style_attr in re.findall(r'<td[^>]*style="([^"]*)"', html):
        for decl in style_attr.split(";"):
            decl = decl.strip()
            if not decl:
                continue
            prop = decl.split(":", 1)[0].strip()
            assert prop in known, f"unexpected injected prop {prop!r}"


def test_injection_is_best_effort_on_table_count_mismatch(tmp_path: Path,
                                                          monkeypatch):
    """If mammoth's table count doesn't match the docx reader's, injection is
    skipped entirely (a bare skeleton, never a mis-aligned style) — and the
    HTML still comes back."""
    src = build_styled_docx(tmp_path / "styled.docx")

    def _fake_maps(_src):
        from ingest.docx_styles import DocxStyleMaps
        # Claim TWO tables when the doc has one → count mismatch.
        m = DocxStyleMaps()
        m.tables = [[[]], [[]]]
        return m

    monkeypatch.setattr("ingest.docx_styles.extract_style_maps", _fake_maps)
    html = docx_html.extract_docx_html(src)
    assert "<table" in html.lower()          # HTML still returned
    assert "3px double" not in html          # no styling injected


def test_embedded_images_are_stripped():
    # Filing logos / signature scans are noise to a table/prose formatting
    # channel and would waste snippet budget as base64 data URIs. Any residual
    # <img> tag is stripped from the extracted HTML.
    assert docx_html._IMG_TAG_RE.sub("", '<p>a<img src="data:x">b</p>') == "<p>ab</p>"
    assert docx_html._IMG_TAG_RE.sub("", "<IMG SRC='y'/>x") == "x"
