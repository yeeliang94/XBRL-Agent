"""run.py CLI stages a .docx input to uploaded.pdf — PLAN-word-input Step 4."""
from pathlib import Path

import pytest

import run
from ingest import word_convert
from ingest.word_convert import WordConversionError
from tests._docx_fixture import build_minimal_docx


def test_stage_docx_converts_and_keeps_both(tmp_path, monkeypatch):
    session_dir = tmp_path / "run_001"
    session_dir.mkdir()
    src = build_minimal_docx(tmp_path / "client.docx")

    monkeypatch.setattr(
        word_convert, "_run_conversion",
        lambda s, d: d.write_bytes(b"%PDF-1.7 converted"),
    )
    run._stage_input_document(str(src), session_dir)

    assert (session_dir / "uploaded.docx").exists()
    assert (session_dir / "uploaded.pdf").exists()
    assert (session_dir / "source.html").exists()  # mammoth ran on the real docx


def test_stage_docx_propagates_conversion_error(tmp_path, monkeypatch):
    # Gotcha #29: the CLI lets a conversion failure PROPAGATE (fail loudly),
    # unlike the web endpoint which turns it into a 422. Pin that contract.
    session_dir = tmp_path / "run_003"
    session_dir.mkdir()
    src = build_minimal_docx(tmp_path / "client.docx")

    def _boom(s, d):
        raise WordConversionError("converter blew up")

    monkeypatch.setattr(word_convert, "_run_conversion", _boom)
    with pytest.raises(WordConversionError):
        run._stage_input_document(str(src), session_dir)


def test_stage_pdf_copies_directly(tmp_path):
    session_dir = tmp_path / "run_002"
    session_dir.mkdir()
    src = tmp_path / "client.pdf"
    src.write_bytes(b"%PDF-1.4 fake")

    run._stage_input_document(str(src), session_dir)

    assert (session_dir / "uploaded.pdf").read_bytes() == b"%PDF-1.4 fake"
    assert not (session_dir / "uploaded.docx").exists()
    assert not (session_dir / "source.html").exists()
