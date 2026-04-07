"""Tests for deterministic TOC page locator."""
from __future__ import annotations

import fitz
import pytest
from pathlib import Path

from scout.toc_locator import find_toc_candidate_pages, TocCandidate


@pytest.fixture
def text_pdf_with_toc(tmp_path: Path) -> Path:
    """Create a synthetic text-based PDF with a TOC on page 3."""
    doc = fitz.open()
    # Pages 1-2: filler
    for _ in range(2):
        page = doc.new_page()
        writer = fitz.TextWriter(page.rect)
        writer.append((72, 100), "Company Overview", fontsize=12)
        writer.write_text(page)
    # Page 3: TOC
    page = doc.new_page()
    writer = fitz.TextWriter(page.rect)
    writer.append((72, 80), "Table of Contents", fontsize=16)
    writer.append((72, 120), "Statement of financial position ...... 8", fontsize=11)
    writer.append((72, 140), "Statement of profit or loss ...... 10", fontsize=11)
    writer.append((72, 160), "Statement of comprehensive income ...... 12", fontsize=11)
    writer.append((72, 180), "Statement of cash flows ...... 14", fontsize=11)
    writer.append((72, 200), "Statement of changes in equity ...... 16", fontsize=11)
    writer.append((72, 220), "Notes to the financial statements ...... 18", fontsize=11)
    writer.write_text(page)
    # Pages 4-10: statement pages
    for i in range(7):
        page = doc.new_page()
        writer = fitz.TextWriter(page.rect)
        writer.append((72, 100), f"Financial statement page {i+4}", fontsize=12)
        writer.write_text(page)
    path = tmp_path / "text_with_toc.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def text_pdf_contents_variant(tmp_path: Path) -> Path:
    """PDF where TOC header says 'Contents' (no 'Table of')."""
    doc = fitz.open()
    page = doc.new_page()
    writer = fitz.TextWriter(page.rect)
    writer.append((72, 80), "Directors' Report", fontsize=12)
    writer.write_text(page)
    page = doc.new_page()
    writer = fitz.TextWriter(page.rect)
    writer.append((72, 80), "Contents", fontsize=16)
    writer.append((72, 120), "Financial statements ...... 5", fontsize=11)
    writer.write_text(page)
    for _ in range(3):
        doc.new_page()
    path = tmp_path / "contents_variant.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def image_only_pdf(tmp_path: Path) -> Path:
    """Create a PDF with no selectable text (simulates scanned PDF)."""
    doc = fitz.open()
    for _ in range(10):
        doc.new_page()  # blank pages, no text
    path = tmp_path / "image_only.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def text_pdf_dotted_lines(tmp_path: Path) -> Path:
    """PDF with no 'Contents' header but dotted-line page refs."""
    doc = fitz.open()
    # Page 1: filler
    page = doc.new_page()
    writer = fitz.TextWriter(page.rect)
    writer.append((72, 100), "Company registration", fontsize=12)
    writer.write_text(page)
    # Page 2: TOC-like page with dotted references but no header
    page = doc.new_page()
    writer = fitz.TextWriter(page.rect)
    writer.append((72, 100), "Statement of financial position           42", fontsize=11)
    writer.append((72, 120), "Statement of profit or loss               44", fontsize=11)
    writer.append((72, 140), "Notes to the financial statements          48", fontsize=11)
    writer.write_text(page)
    for _ in range(3):
        doc.new_page()
    path = tmp_path / "dotted_lines.pdf"
    doc.save(str(path))
    doc.close()
    return path


class TestFindTocCandidatePages:
    """Step 3.2 RED: deterministic TOC locator."""

    def test_finds_toc_by_keyword(self, text_pdf_with_toc: Path):
        candidates = find_toc_candidate_pages(text_pdf_with_toc)
        # Should find page 3 (1-indexed)
        pages = [c.page_number for c in candidates]
        assert 3 in pages

    def test_contents_header_variant(self, text_pdf_contents_variant: Path):
        candidates = find_toc_candidate_pages(text_pdf_contents_variant)
        pages = [c.page_number for c in candidates]
        assert 2 in pages

    def test_dotted_line_pattern(self, text_pdf_dotted_lines: Path):
        candidates = find_toc_candidate_pages(text_pdf_dotted_lines)
        pages = [c.page_number for c in candidates]
        assert 2 in pages

    def test_image_only_returns_heuristic_range(self, image_only_pdf: Path):
        """Image-only PDFs: locator returns early pages as candidates."""
        candidates = find_toc_candidate_pages(image_only_pdf)
        # Should return some candidates (heuristic fallback)
        assert len(candidates) > 0
        # All candidates should have method="heuristic"
        assert all(c.method == "heuristic" for c in candidates)

    def test_candidate_has_required_fields(self, text_pdf_with_toc: Path):
        candidates = find_toc_candidate_pages(text_pdf_with_toc)
        c = candidates[0]
        assert hasattr(c, "page_number")
        assert hasattr(c, "method")
        assert hasattr(c, "score")
        assert c.page_number > 0

    def test_candidates_sorted_by_score(self, text_pdf_with_toc: Path):
        candidates = find_toc_candidate_pages(text_pdf_with_toc)
        scores = [c.score for c in candidates]
        assert scores == sorted(scores, reverse=True)

    def test_real_scanned_pdf(self):
        """Real scanned PDFs should still return heuristic candidates."""
        pdf = Path("/Users/user/Desktop/xbrl-agent/data/FINCO-Audited-Financial-Statement-2021.pdf")
        if not pdf.exists():
            pytest.skip("FINCO PDF not available")
        candidates = find_toc_candidate_pages(pdf)
        assert len(candidates) > 0
