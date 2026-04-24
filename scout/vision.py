"""LLM vision helpers for the scout agent.

Renders PDF pages as images and sends them to the LLM for extraction.
Used when PDFs are scanned (no selectable text) or to supplement
deterministic parsing with visual confirmation.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model

from tools.pdf_viewer import render_pages_to_png_bytes


class VisionTocEntry(BaseModel):
    """A single TOC entry as extracted by the LLM."""
    statement_name: str = Field(description="Name as printed in the TOC")
    stated_page: int = Field(description="Page number as printed in the TOC")


class VisionTocResult(BaseModel):
    """LLM-extracted TOC entries from page images."""
    entries: list[VisionTocEntry] = Field(default_factory=list)


_TOC_EXTRACTION_PROMPT = """\
You are reading the Table of Contents page(s) of a Malaysian annual report PDF.

Extract every entry that refers to a financial statement or notes. For each entry, return:
- statement_name: the EXACT text as printed (e.g. "Statement of Financial Position")
- stated_page: the page number shown next to it

Focus on these sections:
- Statement of Financial Position / Balance Sheet / Penyata Kedudukan Kewangan
- Statement of Profit or Loss / Income Statement / Penyata Untung Rugi
- Statement of Comprehensive Income / Penyata Pendapatan Komprehensif
- Statement of Cash Flows / Penyata Aliran Tunai
- Statement of Changes in Equity / Penyata Perubahan Ekuiti
- Notes to the Financial Statements

Also include Directors' Report, Auditors' Report, and any other entries you can read,
as they help calibrate page offsets.

Return ALL entries you can read, even non-financial-statement ones.
"""


def _vision_entries_to_toc_entries(vision_entries: list[VisionTocEntry]) -> list:
    """Convert VisionTocEntry list into TocEntry objects via the parser.

    Re-uses parse_toc_entries_from_text by reconstructing a text block
    from the vision entries, so statement-type classification stays
    in one place.
    """
    if not vision_entries:
        return []
    from scout.toc_parser import parse_toc_entries_from_text
    lines = [f"{e.statement_name}    {e.stated_page}" for e in vision_entries]
    return parse_toc_entries_from_text("\n".join(lines))


async def extract_toc_via_vision(
    pdf_path: Path | str,
    candidate_pages: list[int],
    model: str | Model = "openai.gpt-5.4",
) -> VisionTocResult:
    """Render candidate TOC pages as images and ask LLM to extract entries.

    Args:
        pdf_path: path to the PDF file.
        candidate_pages: 1-indexed page numbers to render and send.
        model: PydanticAI model to use for vision.

    Returns:
        VisionTocResult with extracted entries.
    """
    pdf_path = Path(pdf_path)

    images: list[BinaryContent] = []
    for page_num in candidate_pages:
        rendered = render_pages_to_png_bytes(
            str(pdf_path),
            start=page_num,
            end=page_num,
            dpi=200,
        )
        if rendered:
            images.append(BinaryContent(data=rendered[0], media_type="image/png"))

    if not images:
        return VisionTocResult(entries=[])

    # Build the agent with structured output
    agent = Agent(
        model,
        output_type=VisionTocResult,
        system_prompt=_TOC_EXTRACTION_PROMPT,
    )

    # Build the user message: images interleaved with page labels
    user_parts: list[str | BinaryContent] = []
    for i, (page_num, img) in enumerate(zip(candidate_pages, images)):
        user_parts.append(f"=== Page {page_num} ===")
        user_parts.append(img)
    user_parts.append("Extract all TOC entries from the page(s) above.")

    result = await agent.run(user_parts)
    return result.output
