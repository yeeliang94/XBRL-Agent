"""Phase 1a Steps 3-5 — wire ``read_face_structure`` through the scout agent.

Three scenarios cover the resolution rule:

1. **Text-PDF regex path:** scout LLM calls ``read_face_structure`` and the
   parsed refs land in the saved Infopack. ``face_read_in_detail`` is True.

2. **Vision path:** scout LLM doesn't call ``read_face_structure`` (or it
   returns empty), but the LLM populates ``face_line_refs`` in the
   save_infopack JSON. The LLM-supplied list is used.

3. **Regex wins over vision when both populate:** scout calls
   ``read_face_structure`` AND submits a different ``face_line_refs`` list
   in the save_infopack JSON. The regex-cached list wins (cheap + exact).

The synthetic PDF used here mirrors a text-based Malaysian annual report:
TOC + face pages with "Note N" cross-references that PyMuPDF can extract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import fitz

from statement_types import StatementType
from scout.infopack import Infopack
from scout.runner import run_scout
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelResponse, ToolCallPart, TextPart


# Synthetic PDF with explicitly noted face pages — PyMuPDF can extract the
# text so the regex path is exercised end-to-end.
@pytest.fixture
def text_pdf(tmp_path: Path) -> Path:
    doc = fitz.open()

    # Page 1: cover (filler)
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 100), "Annual Report 2022", fontsize=16)
    w.write_text(page)

    # Page 2: TOC
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Table of Contents", fontsize=16)
    w.append((72, 100), "Statement of Financial Position ........ 5", fontsize=11)
    w.append((72, 120), "Statement of Profit or Loss ........ 6", fontsize=11)
    w.append((72, 140), "Notes to the Financial Statements ........ 10", fontsize=11)
    w.write_text(page)

    # Pages 3-4: filler
    for _ in range(2):
        page = doc.new_page()
        w = fitz.TextWriter(page.rect)
        w.append((72, 100), "Directors Report", fontsize=11)
        w.write_text(page)

    # Page 5: SOFP with "Note N" references the regex can find
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Statement of Financial Position", fontsize=14)
    w.append((72, 100), "Non-current assets", fontsize=11)
    w.append((72, 120), "Property, plant and equipment   Note 4   12,500", fontsize=11)
    w.append((72, 140), "Current assets", fontsize=11)
    w.append((72, 160), "Trade receivables   Note 7   8,900", fontsize=11)
    w.write_text(page)

    # Page 6: SOPL
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Statement of Profit or Loss", fontsize=14)
    w.append((72, 100), "Revenue   Note 12   45,000", fontsize=11)
    w.write_text(page)

    # Pages 7-10: notes filler
    for i in range(4):
        page = doc.new_page()
        w = fitz.TextWriter(page.rect)
        w.append((72, 60), f"Note {i + 4}", fontsize=14)
        w.write_text(page)

    path = tmp_path / "text_report.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _scripted_scout_model(
    *,
    call_read_face_structure: bool,
    save_infopack_data: dict,
):
    """Build a FunctionModel that scripts the scout's tool sequence.

    When ``call_read_face_structure`` is True the scripted scout calls
    read_face_structure for SOFP and SOPL before save_infopack. When
    False it skips straight to save_infopack — the vision-path
    simulation, where the LLM supplied face_line_refs itself.
    """
    call_count = 0

    def model_function(messages, info: AgentInfo):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="find_toc", args={}, tool_call_id="tc1"),
            ])

        if call_read_face_structure:
            if call_count == 2:
                return ModelResponse(parts=[
                    ToolCallPart(
                        tool_name="read_face_structure",
                        args={"statement_type": "SOFP", "face_page": 5},
                        tool_call_id="tc2",
                    ),
                ])
            if call_count == 3:
                return ModelResponse(parts=[
                    ToolCallPart(
                        tool_name="read_face_structure",
                        args={"statement_type": "SOPL", "face_page": 6},
                        tool_call_id="tc3",
                    ),
                ])
            if call_count == 4:
                return ModelResponse(parts=[
                    ToolCallPart(
                        tool_name="save_infopack",
                        args={"infopack_json": json.dumps(save_infopack_data)},
                        tool_call_id="tc4",
                    ),
                ])
        else:
            if call_count == 2:
                return ModelResponse(parts=[
                    ToolCallPart(
                        tool_name="save_infopack",
                        args={"infopack_json": json.dumps(save_infopack_data)},
                        tool_call_id="tc2",
                    ),
                ])

        return ModelResponse(parts=[TextPart(content="Scout complete.")])

    return FunctionModel(model_function)


@pytest.mark.asyncio
async def test_text_path_carries_regex_refs_into_infopack(text_pdf: Path):
    """Step 3: read_face_structure runs, deterministic refs land on infopack."""
    # save_infopack JSON omits face_line_refs — the cache from
    # read_face_structure must populate them automatically.
    payload = {
        "toc_page": 2,
        "page_offset": 0,
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu",
                "face_page": 5,
                "note_pages": [7, 8],
                "confidence": "HIGH",
            },
            "SOPL": {
                "variant_suggestion": "Function",
                "face_page": 6,
                "note_pages": [9, 10],
                "confidence": "HIGH",
            },
        },
    }
    model = _scripted_scout_model(
        call_read_face_structure=True,
        save_infopack_data=payload,
    )
    infopack = await run_scout(text_pdf, model=model)

    sofp = infopack.statements[StatementType.SOFP]
    sopl = infopack.statements[StatementType.SOPL]

    assert sofp.face_read_in_detail is True
    sofp_note_nums = {r.note_num for r in sofp.face_line_refs}
    assert 4 in sofp_note_nums
    assert 7 in sofp_note_nums

    assert sopl.face_read_in_detail is True
    sopl_note_nums = {r.note_num for r in sopl.face_line_refs}
    assert 12 in sopl_note_nums


@pytest.mark.asyncio
async def test_vision_path_accepts_llm_supplied_refs(text_pdf: Path):
    """Step 5: LLM-supplied face_line_refs land on infopack when regex empty."""
    # Scripted scout skips read_face_structure entirely — simulates a
    # scanned-PDF run where the regex would return [] anyway.
    payload = {
        "toc_page": 2,
        "page_offset": 0,
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu",
                "face_page": 5,
                "note_pages": [7, 8],
                "confidence": "HIGH",
                "face_line_refs": [
                    {
                        "label": "Vision-seen PPE",
                        "note_num": 4,
                        "section": "non-current assets",
                    },
                    {
                        "label": "Vision-seen receivables",
                        "note_num": 7,
                        "section": "current assets",
                    },
                ],
                "face_read_in_detail": True,
            },
        },
    }
    model = _scripted_scout_model(
        call_read_face_structure=False,
        save_infopack_data=payload,
    )
    infopack = await run_scout(text_pdf, model=model)

    sofp = infopack.statements[StatementType.SOFP]
    assert sofp.face_read_in_detail is True
    assert len(sofp.face_line_refs) == 2
    labels = [r.label for r in sofp.face_line_refs]
    assert "Vision-seen PPE" in labels
    assert "Vision-seen receivables" in labels


@pytest.mark.asyncio
async def test_regex_wins_when_both_populate(text_pdf: Path):
    """Step 4: regex cache wins over LLM-submitted face_line_refs."""
    # LLM submits an obviously-wrong face_line_refs list, but the
    # cached regex parse has the right one. Regex wins.
    payload = {
        "toc_page": 2,
        "page_offset": 0,
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu",
                "face_page": 5,
                "note_pages": [7, 8],
                "confidence": "HIGH",
                "face_line_refs": [
                    {"label": "WRONG_LABEL", "note_num": 99},
                ],
                "face_read_in_detail": True,
            },
            "SOPL": {
                "variant_suggestion": "Function",
                "face_page": 6,
                "note_pages": [9, 10],
                "confidence": "HIGH",
            },
        },
    }
    model = _scripted_scout_model(
        call_read_face_structure=True,
        save_infopack_data=payload,
    )
    infopack = await run_scout(text_pdf, model=model)

    sofp = infopack.statements[StatementType.SOFP]
    labels = [r.label for r in sofp.face_line_refs]
    # WRONG_LABEL must NOT be present — the regex cache wins.
    assert "WRONG_LABEL" not in labels
    note_nums = {r.note_num for r in sofp.face_line_refs}
    assert 4 in note_nums
    assert 7 in note_nums
    assert 99 not in note_nums


@pytest.mark.asyncio
async def test_face_read_in_detail_false_when_no_refs(text_pdf: Path):
    """Sanity: when no refs are captured anywhere, the flag stays False."""
    payload = {
        "toc_page": 2,
        "page_offset": 0,
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu",
                "face_page": 5,
                "note_pages": [],
                "confidence": "HIGH",
                # No face_line_refs supplied, no read_face_structure call
            },
        },
    }
    model = _scripted_scout_model(
        call_read_face_structure=False,
        save_infopack_data=payload,
    )
    infopack = await run_scout(text_pdf, model=model)
    sofp = infopack.statements[StatementType.SOFP]
    assert sofp.face_read_in_detail is False
    assert sofp.face_line_refs == []
