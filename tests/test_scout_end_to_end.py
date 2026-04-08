"""End-to-end tests for the scout agent (mocked LLM)."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock

import fitz

from statement_types import StatementType, variants_for
from scout.infopack import Infopack
from scout.runner import run_scout
from scout.vision import VisionTocResult, VisionTocEntry
from scout.variant_detector import VariantDetectionResult


async def _mock_detect_variant(statement_type, page_text, pdf_path, page_num, model):
    """Auto-pick the first detectable variant for any statement type."""
    detectable = [v for v in variants_for(statement_type) if v.detection_signals]
    if detectable:
        return VariantDetectionResult(variant=detectable[0].name, confident=True, method="deterministic")
    return None


@pytest.fixture(autouse=True)
def _mock_hybrid_detector():
    """All end-to-end scout tests mock the hybrid variant detector so they
    don't need a real LLM for variant classification."""
    with patch("scout.runner.detect_variant", side_effect=_mock_detect_variant):
        yield


@pytest.fixture
def synthetic_pdf(tmp_path: Path) -> Path:
    """Create a synthetic PDF with TOC + statement pages."""
    doc = fitz.open()

    # Page 1: cover
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 100), "Annual Report 2021", fontsize=16)
    w.write_text(page)

    # Page 2: TOC
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Table of Contents", fontsize=16)
    w.append((72, 100), "Statement of Financial Position .......... 5", fontsize=11)
    w.append((72, 120), "Statement of Profit or Loss .......... 6", fontsize=11)
    w.append((72, 140), "Statement of Comprehensive Income .......... 7", fontsize=11)
    w.append((72, 160), "Statement of Cash Flows .......... 8", fontsize=11)
    w.append((72, 180), "Statement of Changes in Equity .......... 9", fontsize=11)
    w.append((72, 200), "Notes to the Financial Statements .......... 10", fontsize=11)
    w.write_text(page)

    # Pages 3-4: filler
    for _ in range(2):
        page = doc.new_page()
        w = fitz.TextWriter(page.rect)
        w.append((72, 100), "Directors Report content", fontsize=11)
        w.write_text(page)

    # Page 5: SOFP
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Statement of Financial Position", fontsize=14)
    w.append((72, 100), "Non-current assets", fontsize=11)
    w.append((72, 120), "Property, plant and equipment  Note 4  1,234", fontsize=11)
    w.append((72, 140), "Current assets", fontsize=11)
    w.append((72, 160), "Trade receivables  Note 5  384", fontsize=11)
    w.write_text(page)

    # Page 6: SOPL
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Statement of Profit or Loss", fontsize=14)
    w.append((72, 100), "Revenue  10,000", fontsize=11)
    w.append((72, 120), "Cost of sales  (7,000)", fontsize=11)
    w.write_text(page)

    # Pages 7-9: other statements
    for name in ["Statement of Comprehensive Income",
                 "Statement of Cash Flows",
                 "Statement of Changes in Equity"]:
        page = doc.new_page()
        w = fitz.TextWriter(page.rect)
        w.append((72, 60), name, fontsize=14)
        w.write_text(page)

    # Pages 10-12: notes
    for i in range(3):
        page = doc.new_page()
        w = fitz.TextWriter(page.rect)
        w.append((72, 60), f"Note {i+4}", fontsize=14)
        w.append((72, 100), f"Details for note {i+4}", fontsize=11)
        w.write_text(page)

    path = tmp_path / "synthetic_annual_report.pdf"
    doc.save(str(path))
    doc.close()
    return path


class TestRunScout:
    """End-to-end scout runs with mocked LLM calibration."""

    @pytest.mark.asyncio
    async def test_produces_valid_infopack(self, synthetic_pdf: Path):
        """Scout should produce an infopack with all 5 statements."""
        # Mock calibration: pages match directly (offset 0)
        async def mock_validate(pdf_path, page_num, statement_name, model):
            page_map = {
                5: "Statement of Financial Position",
                6: "Statement of Profit or Loss",
                7: "Statement of Comprehensive Income",
                8: "Statement of Cash Flows",
                9: "Statement of Changes in Equity",
            }
            expected_name = page_map.get(page_num, "")
            if expected_name and expected_name.lower() in statement_name.lower():
                return {"found": True}
            return {"found": False}

        with patch("scout.calibrator._validate_page_via_llm", side_effect=mock_validate):
            infopack = await run_scout(synthetic_pdf, model="fake-model")

        assert isinstance(infopack, Infopack)
        assert infopack.toc_page == 2
        assert len(infopack.statements) == 5
        assert StatementType.SOFP in infopack.statements
        assert StatementType.SOPL in infopack.statements
        assert StatementType.SOCI in infopack.statements
        assert StatementType.SOCF in infopack.statements
        assert StatementType.SOCIE in infopack.statements

    @pytest.mark.asyncio
    async def test_face_pages_correct(self, synthetic_pdf: Path):
        """Each statement's face_page should point to the right PDF page."""
        async def mock_validate(pdf_path, page_num, statement_name, model):
            page_map = {
                5: "financial position",
                6: "profit or loss",
                7: "comprehensive income",
                8: "cash flows",
                9: "changes in equity",
            }
            keyword = page_map.get(page_num, "")
            if keyword and keyword in statement_name.lower():
                return {"found": True}
            return {"found": False}

        with patch("scout.calibrator._validate_page_via_llm", side_effect=mock_validate):
            infopack = await run_scout(synthetic_pdf, model="fake-model")

        assert infopack.statements[StatementType.SOFP].face_page == 5
        assert infopack.statements[StatementType.SOPL].face_page == 6

    @pytest.mark.asyncio
    async def test_honors_subset(self, synthetic_pdf: Path):
        """When statements_to_find is specified, only those are in the result."""
        async def mock_validate(pdf_path, page_num, statement_name, model):
            if page_num == 8 and "cash flow" in statement_name.lower():
                return {"found": True}
            return {"found": False}

        with patch("scout.calibrator._validate_page_via_llm", side_effect=mock_validate):
            infopack = await run_scout(
                synthetic_pdf,
                model="fake-model",
                statements_to_find={StatementType.SOCF},
            )

        # Only SOCF should be present
        assert StatementType.SOCF in infopack.statements
        assert len(infopack.statements) == 1

    @pytest.mark.asyncio
    async def test_infopack_serialises(self, synthetic_pdf: Path):
        """Produced infopack should round-trip through JSON."""
        async def mock_validate(pdf_path, page_num, statement_name, model):
            if page_num == 5 and "financial position" in statement_name.lower():
                return {"found": True}
            return {"found": False}

        with patch("scout.calibrator._validate_page_via_llm", side_effect=mock_validate):
            infopack = await run_scout(
                synthetic_pdf,
                model="fake-model",
                statements_to_find={StatementType.SOFP},
            )

        # Round-trip
        restored = Infopack.from_json(infopack.to_json())
        assert restored.statements[StatementType.SOFP].face_page == \
               infopack.statements[StatementType.SOFP].face_page


class TestRunScoutImageOnly:
    """Tests for the scanned/image-only PDF path (Finding 1 + 4 fix)."""

    @pytest.fixture
    def image_only_pdf(self, tmp_path: Path) -> Path:
        """Create a PDF with NO selectable text (simulates scanned PDF)."""
        doc = fitz.open()
        for _ in range(15):
            doc.new_page()  # blank pages, no text
        path = tmp_path / "scanned_annual_report.pdf"
        doc.save(str(path))
        doc.close()
        return path

    @pytest.mark.asyncio
    async def test_vision_fallback_called_for_image_pdf(self, image_only_pdf: Path):
        """Image-only PDFs should invoke extract_toc_via_vision."""
        # Mock the vision extraction to return TOC entries
        mock_vision_result = VisionTocResult(entries=[
            VisionTocEntry(statement_name="Statement of Financial Position", stated_page=8),
            VisionTocEntry(statement_name="Statement of Profit or Loss", stated_page=10),
            VisionTocEntry(statement_name="Notes to the Financial Statements", stated_page=14),
        ])

        async def mock_vision(pdf_path, pages, model):
            return mock_vision_result

        async def mock_validate(pdf_path, page_num, statement_name, model):
            if page_num == 8 and "financial position" in statement_name.lower():
                return {"found": True}
            if page_num == 10 and "profit or loss" in statement_name.lower():
                return {"found": True}
            return {"found": False}

        with patch("scout.runner.extract_toc_via_vision", side_effect=mock_vision) as vision_mock, \
             patch("scout.calibrator._validate_page_via_llm", side_effect=mock_validate):
            infopack = await run_scout(image_only_pdf, model="fake-model")

        # Vision should have been called
        vision_mock.assert_called_once()
        # SOFP and SOPL should be in the infopack
        assert StatementType.SOFP in infopack.statements
        assert StatementType.SOPL in infopack.statements
        assert infopack.statements[StatementType.SOFP].face_page == 8

    @pytest.mark.asyncio
    async def test_vision_returns_empty_gives_empty_infopack(self, image_only_pdf: Path):
        """If vision also returns nothing, infopack is empty (not a crash)."""
        async def mock_vision(pdf_path, pages, model):
            return VisionTocResult(entries=[])

        with patch("scout.runner.extract_toc_via_vision", side_effect=mock_vision):
            infopack = await run_scout(image_only_pdf, model="fake-model")

        assert isinstance(infopack, Infopack)
        assert len(infopack.statements) == 0


class TestLowConfidenceOmission:
    """Tests for Finding 2: LOW-confidence entries omitted from infopack."""

    @pytest.fixture
    def synthetic_pdf(self, tmp_path: Path) -> Path:
        doc = fitz.open()
        page = doc.new_page()
        w = fitz.TextWriter(page.rect)
        w.append((72, 60), "Table of Contents", fontsize=16)
        w.append((72, 100), "Statement of Financial Position .......... 5", fontsize=11)
        w.append((72, 120), "Statement of Profit or Loss .......... 6", fontsize=11)
        w.write_text(page)
        for _ in range(10):
            doc.new_page()
        path = tmp_path / "test.pdf"
        doc.save(str(path))
        doc.close()
        return path

    @pytest.mark.asyncio
    async def test_low_confidence_excluded(self, synthetic_pdf: Path):
        """Statements that couldn't be calibrated should NOT appear."""
        async def mock_validate(pdf_path, page_num, statement_name, model):
            # Only SOFP found, SOPL never found
            if page_num == 5 and "financial position" in statement_name.lower():
                return {"found": True}
            return {"found": False}

        with patch("scout.calibrator._validate_page_via_llm", side_effect=mock_validate):
            infopack = await run_scout(synthetic_pdf, model="fake-model")

        # SOFP should be present (HIGH confidence)
        assert StatementType.SOFP in infopack.statements
        # SOPL should be OMITTED (LOW confidence — calibration failed)
        assert StatementType.SOPL not in infopack.statements
