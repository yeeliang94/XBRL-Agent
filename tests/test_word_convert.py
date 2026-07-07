"""Tests for ingest.word_convert (docx→PDF, PLAN-word-input Phase 1 Step 2).

The error/dispatch paths run everywhere (no LibreOffice/Word needed — the
converter is mocked). The one test that performs a *real* conversion auto-skips
when no converter binary is installed, mirroring tests/test_pdf_viewer.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ingest import word_convert
from ingest.word_convert import WordConversionError, convert_docx_to_pdf


def _write(path: Path, data: bytes = b"PK\x03\x04fake-docx") -> Path:
    path.write_bytes(data)
    return path


# --- Error paths (always run) ---


def test_missing_source_raises(tmp_path: Path):
    with pytest.raises(WordConversionError):
        convert_docx_to_pdf(tmp_path / "nope.docx", tmp_path / "out.pdf")


def test_empty_source_raises(tmp_path: Path):
    src = _write(tmp_path / "empty.docx", b"")
    with pytest.raises(WordConversionError) as ei:
        convert_docx_to_pdf(src, tmp_path / "out.pdf")
    assert "empty" in ei.value.user_message.lower()


def test_converter_producing_no_output_raises(tmp_path: Path, monkeypatch):
    """A converter that "succeeds" but leaves no file is a hard error."""
    src = _write(tmp_path / "in.docx")
    monkeypatch.setattr(word_convert, "_run_conversion", lambda s, d: None)
    with pytest.raises(WordConversionError):
        convert_docx_to_pdf(src, tmp_path / "out.pdf")


def test_converter_producing_empty_output_raises(tmp_path: Path, monkeypatch):
    src = _write(tmp_path / "in.docx")
    dest = tmp_path / "out.pdf"

    def _fake(s: Path, d: Path):
        d.write_bytes(b"")

    monkeypatch.setattr(word_convert, "_run_conversion", _fake)
    with pytest.raises(WordConversionError):
        convert_docx_to_pdf(src, dest)


def test_success_returns_dest(tmp_path: Path, monkeypatch):
    src = _write(tmp_path / "in.docx")
    dest = tmp_path / "sub" / "out.pdf"  # parent created by convert

    def _fake(s: Path, d: Path):
        d.write_bytes(b"%PDF-1.7 fake")

    monkeypatch.setattr(word_convert, "_run_conversion", _fake)
    out = convert_docx_to_pdf(src, dest)
    assert out == dest and dest.exists() and dest.stat().st_size > 0


def test_user_message_present_on_error():
    err = WordConversionError("boom")
    assert err.user_message  # non-empty plain-language message for the 422
    assert "boom" in str(err)  # technical detail preserved for logs


# --- Dispatch selection ---


def test_forced_docx2pdf_calls_word_com(tmp_path: Path, monkeypatch):
    called = {}

    def _fake_com(s: Path, d: Path):
        called["com"] = True
        d.write_bytes(b"%PDF")

    monkeypatch.setenv("XBRL_DOCX_CONVERTER", "docx2pdf")
    monkeypatch.setattr(word_convert, "_convert_with_word_com", _fake_com)
    monkeypatch.setattr(
        word_convert, "_convert_with_soffice",
        lambda *a: pytest.fail("soffice should not be called"),
    )
    convert_docx_to_pdf(_write(tmp_path / "in.docx"), tmp_path / "out.pdf")
    assert called.get("com")


def test_unknown_forced_converter_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XBRL_DOCX_CONVERTER", "libreoffice")  # common typo
    with pytest.raises(WordConversionError) as ei:
        convert_docx_to_pdf(_write(tmp_path / "in.docx"), tmp_path / "out.pdf")
    assert "libreoffice" in str(ei.value)


def test_forced_soffice_without_binary_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XBRL_DOCX_CONVERTER", "soffice")
    monkeypatch.setattr(word_convert, "_find_soffice", lambda: None)
    with pytest.raises(WordConversionError):
        convert_docx_to_pdf(_write(tmp_path / "in.docx"), tmp_path / "out.pdf")


def test_auto_on_windows_uses_word_com(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("XBRL_DOCX_CONVERTER", raising=False)
    monkeypatch.setattr(word_convert.sys, "platform", "win32")
    called = {}

    def _fake_com(s: Path, d: Path):
        called["com"] = True
        d.write_bytes(b"%PDF")

    monkeypatch.setattr(word_convert, "_convert_with_word_com", _fake_com)
    convert_docx_to_pdf(_write(tmp_path / "in.docx"), tmp_path / "out.pdf")
    assert called.get("com")


def test_auto_on_posix_uses_soffice(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("XBRL_DOCX_CONVERTER", raising=False)
    monkeypatch.setattr(word_convert.sys, "platform", "darwin")
    monkeypatch.setattr(word_convert, "_find_soffice", lambda: "/usr/bin/soffice")
    seen = {}

    def _fake_soffice(s: Path, d: Path, binpath: str):
        seen["bin"] = binpath
        d.write_bytes(b"%PDF")

    monkeypatch.setattr(word_convert, "_convert_with_soffice", _fake_soffice)
    convert_docx_to_pdf(_write(tmp_path / "in.docx"), tmp_path / "out.pdf")
    assert seen.get("bin") == "/usr/bin/soffice"


def test_soffice_command_builder_moves_output(tmp_path: Path, monkeypatch):
    """_convert_with_soffice builds the right argv and relocates the produced
    <stem>.pdf to the requested dest name."""
    src = _write(tmp_path / "uploaded.docx")
    dest = tmp_path / "uploaded.pdf"
    captured = {}

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        # Simulate LibreOffice writing <outdir>/<stem>.pdf
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / f"{src.stem}.pdf").write_bytes(b"%PDF-1.7")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(word_convert.subprocess, "run", _fake_run)
    word_convert._convert_with_soffice(src, dest, "soffice")
    assert "--convert-to" in captured["cmd"] and "pdf" in captured["cmd"]
    assert dest.exists()


def test_word_com_timeout_raises(tmp_path: Path, monkeypatch):
    """The Windows path runs in a child process so a hung Word COM call is
    killed and surfaced as a WordConversionError, not an infinite hang."""
    def _timeout_run(cmd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(word_convert.subprocess, "run", _timeout_run)
    with pytest.raises(WordConversionError) as ei:
        word_convert._convert_with_word_com(tmp_path / "in.docx", tmp_path / "out.pdf")
    assert "timed out" in str(ei.value).lower()


def test_word_com_missing_module_gives_clean_message(tmp_path: Path, monkeypatch):
    def _fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(
            cmd, 1, "", "ModuleNotFoundError: No module named 'docx2pdf'"
        )

    monkeypatch.setattr(word_convert.subprocess, "run", _fake_run)
    with pytest.raises(WordConversionError) as ei:
        word_convert._convert_with_word_com(tmp_path / "in.docx", tmp_path / "out.pdf")
    assert "Word isn't available" in ei.value.user_message


def test_word_com_success_no_raise(tmp_path: Path, monkeypatch):
    def _ok_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(word_convert.subprocess, "run", _ok_run)
    # _convert_with_word_com doesn't itself verify output (the outer
    # convert_docx_to_pdf does); a clean child exit must not raise.
    word_convert._convert_with_word_com(tmp_path / "in.docx", tmp_path / "out.pdf")


def test_soffice_nonzero_exit_raises(tmp_path: Path, monkeypatch):
    src = _write(tmp_path / "in.docx")

    def _fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, 1, "", "conversion boom")

    monkeypatch.setattr(word_convert.subprocess, "run", _fake_run)
    with pytest.raises(WordConversionError) as ei:
        word_convert._convert_with_soffice(src, tmp_path / "out.pdf", "soffice")
    assert "boom" in str(ei.value)


# --- Real conversion (auto-skips without a converter) ---


@pytest.mark.skipif(
    word_convert._find_soffice() is None,
    reason="no LibreOffice installed — real docx→pdf conversion untestable here",
)
def test_real_conversion_produces_text_pdf(tmp_path: Path):
    from tests._docx_fixture import build_minimal_docx  # local helper

    src = build_minimal_docx(tmp_path / "real.docx")
    dest = tmp_path / "real.pdf"
    convert_docx_to_pdf(src, dest)
    assert dest.exists() and dest.stat().st_size > 0
    from tools.pdf_search import pdf_has_text_layer
    assert pdf_has_text_layer(str(dest)) is True
