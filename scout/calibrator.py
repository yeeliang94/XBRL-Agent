"""Offset calibration: verify TOC-stated page numbers against actual PDF content.

For each TOC entry, builds a search window around the stated page, renders
each candidate as an image, and asks the LLM whether the page contains the
expected statement header. Locks the first confirmed page as the validated
actual page and records the offset.

If no match is found within the search window, the entry is marked LOW
confidence and flagged for user review.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model

from statement_types import StatementType
from scout.toc_parser import TocEntry
from tools.pdf_viewer import render_pages_to_png_bytes

# How far from the stated page to search (±WINDOW pages).
_SEARCH_RADIUS = 10


@dataclass
class CalibratedPage:
    """Result of calibrating one TOC entry to an actual PDF page."""
    statement_type: StatementType
    stated_page: int
    actual_page: int          # 0 if not found
    offset: int               # actual_page - stated_page
    confidence: str           # HIGH, MEDIUM, LOW
    variant_suggestion: Optional[str] = None


@dataclass
class CalibrationResult:
    """Aggregated calibration results for all statements."""
    pages: dict[StatementType, CalibratedPage] = field(default_factory=dict)


def _build_search_window(stated_page: int, pdf_length: int) -> list[int]:
    """Build ordered list of candidate pages to probe.

    Starts at stated_page, then alternates outward (±1, ±2, ...).
    All values clamped to [1, pdf_length].
    """
    candidates: list[int] = []
    seen: set[int] = set()

    def _add(p: int) -> None:
        if 1 <= p <= pdf_length and p not in seen:
            candidates.append(p)
            seen.add(p)

    _add(stated_page)
    for delta in range(1, _SEARCH_RADIUS + 1):
        _add(stated_page + delta)
        _add(stated_page - delta)

    return candidates


# -- LLM page validation (the part that gets mocked in tests) -----------------

class _PageValidationResult(BaseModel):
    """LLM structured output for page validation.

    This is purely a found/not-found check. Variant classification is handled
    separately by the hybrid detector in variant_detector.py.
    """
    found: bool = Field(description="True if this page contains the expected statement header")


_VALIDATION_PROMPT = """\
You are checking whether a PDF page contains the header/title of a specific
financial statement from a Malaysian annual report.

The statement you are looking for: {statement_name}

Does this page contain the title/header of that statement?
Return found=true only if you can clearly see the statement title on this page.
Do NOT return found=true for notes pages or continuation pages.
"""


async def _validate_page_via_llm(
    pdf_path: Path,
    page_num: int,
    statement_name: str,
    model: str | Model,
) -> dict:
    """Render a single page and ask LLM if it contains the statement header.

    Returns dict with 'found' (bool).
    """
    rendered = render_pages_to_png_bytes(
        str(pdf_path), start=page_num, end=page_num, dpi=200,
    )
    if not rendered:
        return {"found": False}
    img_bytes = rendered[0]

    agent = Agent(
        model,
        output_type=_PageValidationResult,
        system_prompt=_VALIDATION_PROMPT.format(statement_name=statement_name),
    )

    result = await agent.run([
        f"Check if this page contains: {statement_name}",
        BinaryContent(data=img_bytes, media_type="image/png"),
    ])

    return {"found": result.output.found}


# -- Main calibration loop ----------------------------------------------------

async def calibrate_pages(
    pdf_path: Path,
    toc_entries: list[TocEntry],
    pdf_length: int,
    model: str | Model = "google-gla:gemini-3-flash-preview",
) -> CalibrationResult:
    """Calibrate each TOC entry to its actual PDF page.

    For each entry with a known statement_type, builds a search window
    around the stated page and probes candidates via LLM until a match
    is found or the window is exhausted.

    Args:
        pdf_path: path to the PDF file.
        toc_entries: TOC entries from the parser (may include non-statement
            entries which are skipped).
        pdf_length: total number of pages in the PDF.
        model: PydanticAI model for vision validation.

    Returns:
        CalibrationResult with one CalibratedPage per statement-type entry.
    """
    result = CalibrationResult()

    # Only calibrate entries with a known statement type
    statement_entries = [e for e in toc_entries if e.statement_type is not None]

    for entry in statement_entries:
        st_type = entry.statement_type
        assert st_type is not None  # for type checker

        window = _build_search_window(entry.stated_page, pdf_length)
        found = False

        for candidate_page in window:
            validation = await _validate_page_via_llm(
                pdf_path, candidate_page, entry.statement_name, model,
            )

            if validation["found"]:
                result.pages[st_type] = CalibratedPage(
                    statement_type=st_type,
                    stated_page=entry.stated_page,
                    actual_page=candidate_page,
                    offset=candidate_page - entry.stated_page,
                    confidence="HIGH",
                )
                found = True
                break

        if not found:
            # Exhausted search window — mark LOW confidence
            result.pages[st_type] = CalibratedPage(
                statement_type=st_type,
                stated_page=entry.stated_page,
                actual_page=0,
                offset=0,
                confidence="LOW",
                variant_suggestion=None,
            )

    return result
