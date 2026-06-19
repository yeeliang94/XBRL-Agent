"""Phase 1 / Step 2 verification for the scanned-PDF → readable-doc converter.

Proves two things at once on a REAL scanned financial-statement page:
- correctness: the converted HTML rebuilds the table with the right figures;
- offline: it works with all outbound network physically blocked (the bundled
  models are used; nothing is fetched).

Auto-skips when the sample PDF or the model bundle is absent (mirrors the
data-dependent skips elsewhere, e.g. tests/test_pdf_viewer.py).
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
FINCO = _REPO / "data" / "FINCO-Audited-Financial-Statement-2021.pdf"
MODELS = _REPO / "models" / "docling"

pytestmark = pytest.mark.skipif(
    not FINCO.exists() or not MODELS.exists(),
    reason="sample FINCO PDF or Docling model bundle not present",
)

# The Statement of Financial Position is at 0-based PDF index 13 (printed "12").
_SOFP_PAGE_INDEX = 13


def test_converts_scanned_sofp_page_fully_offline(tmp_path, monkeypatch):
    import fitz

    # Extract just the SOFP page to keep the test fast (one page, not all 37).
    src = fitz.open(str(FINCO))
    one = fitz.open()
    one.insert_pdf(src, from_page=_SOFP_PAGE_INDEX, to_page=_SOFP_PAGE_INDEX)
    page_pdf = tmp_path / "sofp.pdf"
    one.save(str(page_pdf))
    one.close()
    src.close()

    # Hard-block ALL outbound network so any fetch attempt fails loudly. If the
    # converter still succeeds, it is genuinely offline. monkeypatch restores
    # the originals after the test so other tests keep their network.
    def _blocked(*_args, **_kwargs):
        raise OSError("NETWORK BLOCKED (offline test)")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    from docconvert import convert_pdf_to_html

    progress: list[tuple[int, int]] = []
    html = convert_pdf_to_html(
        page_pdf, progress_cb=lambda done, total: progress.append((done, total))
    )

    # Correctness: a real table with the key figures from the page.
    assert "<table" in html
    assert "3,141,738" in html  # Total assets / Total equity and liabilities
    assert "Receivables" in html
    assert "391,675" in html

    # Progress fired once per page and completed.
    assert progress == [(1, 1)]


def test_missing_pdf_raises_clear_error(tmp_path, monkeypatch):
    from docconvert import convert_pdf_to_html, DocConvertError

    with pytest.raises(DocConvertError):
        convert_pdf_to_html(tmp_path / "does-not-exist.pdf")
