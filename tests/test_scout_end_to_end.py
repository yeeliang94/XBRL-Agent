"""End-to-end tests for the scout agent (mocked LLM via FunctionModel).

These tests verify the full run_scout() flow using PydanticAI's FunctionModel
to simulate agent behavior without real LLM calls.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

import fitz

from statement_types import StatementType
from scout.infopack import Infopack
from scout.runner import run_scout
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelResponse, ToolCallPart, TextPart


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
        w.append((72, 60), f"Note {i + 4}", fontsize=14)
        w.append((72, 100), f"Details for note {i + 4}", fontsize=11)
        w.write_text(page)

    path = tmp_path / "synthetic_annual_report.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _make_scout_model(infopack_data: dict):
    """Create a FunctionModel that simulates scout agent behavior:
    1. Call find_toc
    2. Call save_infopack with the provided data
    3. Return final text
    """
    call_count = 0

    def model_function(messages, info: AgentInfo):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="find_toc", args={}, tool_call_id="tc1"),
            ])
        elif call_count == 2:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="save_infopack",
                    args={"infopack_json": json.dumps(infopack_data)},
                    tool_call_id="tc2",
                ),
            ])
        else:
            return ModelResponse(parts=[TextPart(content="Scout complete.")])

    return FunctionModel(model_function)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunScout:
    """End-to-end scout runs with FunctionModel."""

    @pytest.mark.asyncio
    async def test_produces_valid_infopack(self, synthetic_pdf: Path):
        """Scout should produce an infopack with requested statements."""
        model = _make_scout_model({
            "toc_page": 2,
            "page_offset": 0,
            "statements": {
                "SOFP": {"variant_suggestion": "CuNonCu", "face_page": 5,
                          "note_pages": [10, 11], "confidence": "HIGH"},
                "SOPL": {"variant_suggestion": "Function", "face_page": 6,
                          "note_pages": [], "confidence": "HIGH"},
                "SOCI": {"variant_suggestion": "BeforeTax", "face_page": 7,
                          "note_pages": [], "confidence": "HIGH"},
                "SOCF": {"variant_suggestion": "Indirect", "face_page": 8,
                          "note_pages": [], "confidence": "HIGH"},
                "SOCIE": {"variant_suggestion": "Default", "face_page": 9,
                           "note_pages": [], "confidence": "HIGH"},
            },
        })

        infopack = await run_scout(synthetic_pdf, model=model)

        assert isinstance(infopack, Infopack)
        assert infopack.toc_page == 2
        assert len(infopack.statements) == 5
        assert StatementType.SOFP in infopack.statements
        assert StatementType.SOPL in infopack.statements

    @pytest.mark.asyncio
    async def test_face_pages_correct(self, synthetic_pdf: Path):
        """Face pages should match what the agent reported."""
        model = _make_scout_model({
            "toc_page": 2,
            "page_offset": 0,
            "statements": {
                "SOFP": {"variant_suggestion": "CuNonCu", "face_page": 5,
                          "note_pages": [], "confidence": "HIGH"},
                "SOPL": {"variant_suggestion": "Function", "face_page": 6,
                          "note_pages": [], "confidence": "HIGH"},
            },
        })

        infopack = await run_scout(synthetic_pdf, model=model)
        assert infopack.statements[StatementType.SOFP].face_page == 5
        assert infopack.statements[StatementType.SOPL].face_page == 6

    @pytest.mark.asyncio
    async def test_honors_subset(self, synthetic_pdf: Path):
        """When statements_to_find is specified, agent still runs."""
        model = _make_scout_model({
            "toc_page": 2,
            "page_offset": 0,
            "statements": {
                "SOCF": {"variant_suggestion": "Indirect", "face_page": 8,
                          "note_pages": [], "confidence": "HIGH"},
            },
        })

        infopack = await run_scout(
            synthetic_pdf,
            model=model,
            statements_to_find={StatementType.SOCF},
        )
        assert StatementType.SOCF in infopack.statements

    @pytest.mark.asyncio
    async def test_infopack_serialises(self, synthetic_pdf: Path):
        """Produced infopack should round-trip through JSON."""
        model = _make_scout_model({
            "toc_page": 2,
            "page_offset": 0,
            "statements": {
                "SOFP": {"variant_suggestion": "CuNonCu", "face_page": 5,
                          "note_pages": [10], "confidence": "HIGH"},
            },
        })

        infopack = await run_scout(
            synthetic_pdf,
            model=model,
            statements_to_find={StatementType.SOFP},
        )

        restored = Infopack.from_json(infopack.to_json())
        assert restored.statements[StatementType.SOFP].face_page == \
               infopack.statements[StatementType.SOFP].face_page


class TestRunScoutImageOnly:
    """Tests for scanned PDFs — agent uses vision to read TOC."""

    @pytest.fixture
    def image_only_pdf(self, tmp_path: Path) -> Path:
        doc = fitz.open()
        for _ in range(15):
            doc.new_page()
        path = tmp_path / "scanned.pdf"
        doc.save(str(path))
        doc.close()
        return path

    @pytest.mark.asyncio
    async def test_empty_pdf_raises_when_no_infopack(self, image_only_pdf: Path):
        """Agent on a blank PDF that never saves an infopack should raise."""
        call_count = 0

        def model_fn(messages, info: AgentInfo):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ModelResponse(parts=[
                    ToolCallPart(tool_name="find_toc", args={}, tool_call_id="tc1"),
                ])
            else:
                return ModelResponse(parts=[TextPart(content="No TOC found.")])

        with pytest.raises(RuntimeError, match="without producing a valid infopack"):
            await run_scout(image_only_pdf, model=FunctionModel(model_fn))
