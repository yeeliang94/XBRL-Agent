"""Tests for prompts/monolith_face.md + monolith/prompt_renderer.py.

The pinning tests grep-assert that the load-bearing invariants survive
into the rendered prompt verbatim. If a future edit softens any of them,
the corresponding test fails loudly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from monolith.config import MONOLITH_PROMPT_BYTE_CEILING
from monolith.prompt_renderer import RenderedPrompt, render
from statement_types import StatementType


_REPO = Path(__file__).resolve().parent.parent
_PROMPT_MD = _REPO / "prompts" / "monolith_face.md"
_FINCO_PDF = _REPO / "data" / "FINCO-Audited-Financial-Statement-2021.pdf"


# ---------------------------------------------------------------------------
# Static prompt-content invariants
# ---------------------------------------------------------------------------


def test_prompt_md_contains_load_bearing_rules():
    text = _PROMPT_MD.read_text(encoding="utf-8")
    # SOCIE dividend sign convention (gotcha #15, ADR-002).
    assert "dividends" in text.lower() and "positive" in text.lower(), (
        "SOCIE dividend sign rule missing or softened"
    )
    # Abstract-row guard (gotcha #17).
    assert "abstract" in text.lower(), "abstract-row rule missing"
    # No-residual-plug rule.
    assert (
        "catch-all" in text.lower() or "residual" in text.lower()
    ), "no-residual-plug rule missing"
    # All five cross-statement identities surfaced.
    for ident in (
        "sofp_balance",
        "sopl_to_socie_profit",
        "soci_to_socie_tci",
        "socie_to_sofp_equity",
        "socf_to_sofp_cash",
    ):
        assert ident in text, f"cross-check identity {ident} missing from prompt"
    # Workflow contract bullet points.
    assert "get_state" in text and "write_cells" in text and "done" in text


def test_prompt_md_describes_workflow_contract():
    """get_state → write_cells → done loop must be stated as a contract."""
    text = _PROMPT_MD.read_text(encoding="utf-8")
    assert "after every" in text.lower() and "get_state" in text.lower()


# ---------------------------------------------------------------------------
# Renderer behaviour
# ---------------------------------------------------------------------------


def _make_text_pdf(path: Path) -> Path:
    """Synthesise a 3-page PDF whose pages carry a real text layer.

    The bundled FINCO PDF is image-only, so renderer tests that want
    the text-extraction branch must build their own fixture.
    """
    import fitz
    doc = fitz.open()
    try:
        for i in range(3):
            page = doc.new_page()
            page.insert_text(
                (72, 72), f"Page {i + 1}: Total assets {12345 + i}",
            )
        doc.save(str(path))
    finally:
        doc.close()
    return path


def test_render_emits_rules_template_and_pdf_blocks(tmp_path):
    pdf_path = _make_text_pdf(tmp_path / "text.pdf")
    out = render(
        str(pdf_path),
        filing_standard="mfrs",
        filing_level="company",
    )
    assert isinstance(out, RenderedPrompt)
    assert "Monolith Face-Statement Agent" in out.full
    assert "## Template structure" in out.full
    assert "## PDF TEXT" in out.full
    # Cross-reference annotations present in the structure block.
    for ident in (
        "sofp_balance",
        "sopl_to_socie_profit",
        "soci_to_socie_tci",
        "socie_to_sofp_equity",
        "socf_to_sofp_cash",
    ):
        assert ident in out.template_structure


def test_render_per_sheet_lists_include_all_five():
    """The template-structure block must list a section per face statement."""
    # Doesn't need a PDF — use an empty path; the PDF block is empty and
    # the structure block is what we're asserting on.
    out = render(
        pdf_path="",
        filing_standard="mfrs",
        filing_level="company",
    )
    for stmt in (StatementType.SOFP, StatementType.SOPL, StatementType.SOCI,
                 StatementType.SOCF, StatementType.SOCIE):
        assert f"### {stmt.value}" in out.template_structure


def test_render_byte_ceiling_trims_pdf_text(tmp_path):
    """Setting a tiny ceiling forces PDF text trim and sets `trimmed=True`."""
    # Synthesise a text-rich PDF so we exercise the trim path (the
    # bundled FINCO PDF is image-only and routes through the
    # vision-only branch, which doesn't trim).
    import fitz
    pdf_path = tmp_path / "wide.pdf"
    doc = fitz.open()
    try:
        # Pack each page with enough text to push past a 32 KB ceiling
        # once 5 of them are concatenated.
        filler = ("Line of accounting text with figures. " * 40 + "\n") * 30
        for _ in range(5):
            page = doc.new_page()
            page.insert_text((50, 50), filler)
        doc.save(str(pdf_path))
    finally:
        doc.close()

    tiny_ceiling = 32 * 1024  # 32 KB
    out = render(
        str(pdf_path),
        byte_ceiling=tiny_ceiling,
    )
    assert out.trimmed, (
        f"expected trimming at {tiny_ceiling} byte budget; got "
        f"{out.byte_size} bytes"
    )


def test_render_default_ceiling_used_when_unset(tmp_path):
    out = render(pdf_path="", byte_ceiling=MONOLITH_PROMPT_BYTE_CEILING)
    # With no PDF the assembly stays small; trimmed should be False.
    assert out.trimmed is False


def test_render_detects_scanned_pdf_and_emits_vision_only_banner(tmp_path):
    """Image-only PDFs (every page returns 0 chars from PyMuPDF) flip
    `pdf_text_empty=True` and swap the PDF TEXT block for a vision
    banner. Pins the 2026-05-28 fix for the scanned-PDF incident
    (run 82dd3ac8) where the agent bailed on an empty cache.
    """
    import fitz  # PyMuPDF — synthesise a real image-only PDF
    pdf_path = tmp_path / "scanned.pdf"
    doc = fitz.open()
    try:
        for _ in range(3):
            doc.new_page()  # no insert_text — page has no text layer
        doc.save(str(pdf_path))
    finally:
        doc.close()

    out = render(
        str(pdf_path),
        filing_standard="mfrs",
        filing_level="company",
    )
    assert out.pdf_text_empty is True
    # Banner present, PDF TEXT block absent.
    assert "vision-only" in out.full.lower()
    assert "## PDF TEXT (cached)" not in out.full
    assert out.pdf_text == ""


def test_render_text_pdf_keeps_pdf_text_empty_false(tmp_path):
    """Regression: a PDF with a real text layer must not flip the
    vision-only branch on. Otherwise every monolith run would silently
    move to vision-preload (paying tokens for nothing)."""
    pdf_path = _make_text_pdf(tmp_path / "text.pdf")
    out = render(
        str(pdf_path),
        filing_standard="mfrs",
        filing_level="company",
    )
    assert out.pdf_text_empty is False
    assert "## PDF TEXT (cached)" in out.full
    assert out.blank_pages == []


def test_render_mixed_pdf_lists_blank_pages_in_banner(tmp_path):
    """Peer-review MEDIUM #3: a PDF with both text and scanned pages
    must surface the blank page numbers in the cached-text banner so
    the agent doesn't silently swallow the empty markers. The
    coordinator's opening user message will then attach the PNGs."""
    import fitz
    pdf_path = tmp_path / "mixed.pdf"
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), "Cover page text")
        doc.new_page()  # blank — image-only in a real scan
        doc.new_page()  # blank
        page = doc.new_page()
        page.insert_text((72, 72), "Auditor signature page")
        doc.save(str(pdf_path))
    finally:
        doc.close()

    out = render(
        str(pdf_path), filing_standard="mfrs", filing_level="company",
    )
    assert out.pdf_text_empty is False
    assert out.blank_pages == [2, 3]
    # Banner names the blank pages so the agent knows which to read
    # from the opening user message's PNGs.
    assert "pages 2, 3" in out.full
    assert "PNG images" in out.full
