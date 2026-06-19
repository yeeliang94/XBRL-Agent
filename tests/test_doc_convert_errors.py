"""Phase 6 / Step 11: converter error states surface friendly messages.

A corrupt PDF and a password-protected PDF must both raise a DocConvertError
with a user-actionable message (not a raw stack trace), so the worker can land
the job 'failed' with something the user can read. No model bundle needed —
these fail before OCR, so the test runs without the ~599MB weights.
"""
from __future__ import annotations

import pytest

from docconvert.converter import convert_pdf_to_html, DocConvertError


def test_corrupt_pdf_raises_clear_error(tmp_path):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not really a pdf \x00\x01\x02")
    with pytest.raises(DocConvertError) as exc:
        convert_pdf_to_html(bad)
    assert "corrupt" in str(exc.value).lower() or "couldn't open" in str(exc.value).lower()


def test_password_protected_pdf_raises_clear_error(tmp_path):
    import fitz

    # Build a 1-page encrypted PDF (user password set) and confirm it is
    # rejected with the password message rather than producing empty output.
    src = fitz.open()
    src.new_page()
    enc = tmp_path / "locked.pdf"
    src.save(
        str(enc),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="secret",
    )
    src.close()

    with pytest.raises(DocConvertError) as exc:
        convert_pdf_to_html(enc)
    assert "password" in str(exc.value).lower()


# Note: the "no pages" guard in the converter stays as defence-in-depth, but a
# zero-page PDF is not constructable via PyMuPDF (`save()` raises "cannot save
# with zero pages"), so there is no meaningful input to test it against.


def test_all_pages_failing_raises_not_silently_done(tmp_path, monkeypatch):
    """If every page fails to convert, the whole job must fail (not 'done').

    Regression for the code-review finding: per-page resilience must not turn a
    wholesale OCR/model failure into a 'successful' wall of error markers. Uses a
    real 1-page PDF but a fake converter that always raises, so no model bundle
    is needed.
    """
    import fitz

    from docconvert import converter as conv

    pdf = tmp_path / "ok.pdf"
    d = fitz.open()
    d.new_page()
    d.save(str(pdf))
    d.close()

    class _AlwaysFails:
        def convert(self, _path):
            raise RuntimeError("OCR engine exploded")

    monkeypatch.setattr(conv, "_resolve_models_dir", lambda _m: tmp_path)
    monkeypatch.setattr(conv, "_build_converter", lambda _m, _e=conv.DEFAULT_OCR_ENGINE: _AlwaysFails())

    with pytest.raises(DocConvertError) as exc:
        conv.convert_pdf_to_html(pdf)
    assert "none of the pages" in str(exc.value).lower()


def test_bad_pdf_error_precedes_missing_models(tmp_path):
    """A bad PDF surfaces the PDF error even when the model bundle is absent.

    Regression for the peer-review finding: the converter must validate/open the
    PDF BEFORE resolving the model bundle, so on a machine without the 599MB
    bundle a corrupt PDF still reports a PDF-specific error (not "models not
    found"). Pointing model_dir at a non-existent path simulates that machine.
    """
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.4 not a real pdf \x00\x01")
    with pytest.raises(DocConvertError) as exc:
        convert_pdf_to_html(bad, model_dir=tmp_path / "no-such-bundle")
    msg = str(exc.value).lower()
    assert "model" not in msg  # NOT the "model bundle not found" error
    assert "corrupt" in msg or "couldn't open" in msg
