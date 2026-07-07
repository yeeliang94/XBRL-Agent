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
