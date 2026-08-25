from __future__ import annotations

import server


def test_pdf_auto_format_gate_defaults_off(tmp_path, monkeypatch):
    monkeypatch.delenv("XBRL_PDF_NOTES_AUTO_FORMAT", raising=False)
    assert server._should_auto_format_pdf_notes(
        tmp_path, merge_succeeded=True, has_notes_result=True,
    ) is False


def test_pdf_auto_format_gate_accepts_scanned_and_text_pdfs(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_NOTES_AUTO_FORMAT", "true")
    (tmp_path / "uploaded.pdf").write_bytes(b"%PDF")
    assert server._should_auto_format_pdf_notes(
        tmp_path, merge_succeeded=True, has_notes_result=True,
    ) is True


def test_pdf_auto_format_gate_requires_the_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_NOTES_AUTO_FORMAT", "true")
    assert server._should_auto_format_pdf_notes(
        tmp_path, merge_succeeded=True, has_notes_result=True,
    ) is False


def test_pdf_auto_format_gate_never_touches_word_uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_NOTES_AUTO_FORMAT", "true")
    (tmp_path / "uploaded.pdf").write_bytes(b"%PDF")
    (tmp_path / "uploaded.docx").write_bytes(b"PK")
    assert server._should_auto_format_pdf_notes(
        tmp_path, merge_succeeded=True, has_notes_result=True,
    ) is False
