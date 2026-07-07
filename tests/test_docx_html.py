"""Tests for ingest.docx_html (mammoth sidecar, PLAN-word-input Phase 2 Step 7)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ingest import docx_html
from tests._docx_fixture import build_minimal_docx

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


def test_embedded_images_are_stripped():
    # Filing logos / signature scans are noise to a table/prose formatting
    # channel and would waste snippet budget as base64 data URIs. Any residual
    # <img> tag is stripped from the extracted HTML.
    assert docx_html._IMG_TAG_RE.sub("", '<p>a<img src="data:x">b</p>') == "<p>ab</p>"
    assert docx_html._IMG_TAG_RE.sub("", "<IMG SRC='y'/>x") == "x"
