"""Scout runner: composes TOC locator → parser → calibrator → notes → Infopack.

This is the main entry point for the scout agent. It orchestrates:
1. Find candidate TOC pages (deterministic text scan or heuristic)
2. Parse TOC entries (deterministic regex on text, or LLM vision for scanned PDFs)
3. Calibrate stated page numbers to actual PDF pages (LLM vision)
4. Detect variants per statement
5. Discover note pages per statement
6. Assemble everything into an Infopack

When `statements_to_find` is provided, only those statements are calibrated
and included — reducing LLM calls proportionally.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import fitz

from statement_types import StatementType
from scout.infopack import Infopack, StatementPageRef
from scout.toc_locator import find_toc_candidate_pages
from scout.toc_parser import parse_toc_entries_from_text, TocEntry
from scout.vision import extract_toc_via_vision, VisionTocEntry
from scout.calibrator import calibrate_pages
from scout.variant_detector import detect_variant, detect_variant_from_signals
from scout.notes_discoverer import discover_note_pages

logger = logging.getLogger(__name__)


def _extract_text_from_pages(pdf_path: Path, page_numbers: list[int]) -> str:
    """Extract selectable text from specific PDF pages (1-indexed)."""
    doc = fitz.open(str(pdf_path))
    texts = []
    for pn in page_numbers:
        if 1 <= pn <= len(doc):
            texts.append(doc[pn - 1].get_text())
    doc.close()
    return "\n".join(texts)


def _get_pdf_length(pdf_path: Path) -> int:
    doc = fitz.open(str(pdf_path))
    length = len(doc)
    doc.close()
    return length


def _vision_entries_to_toc_entries(vision_entries: list[VisionTocEntry]) -> list[TocEntry]:
    """Convert LLM vision output into TocEntry objects via the parser.

    Re-uses parse_toc_entries_from_text by reconstructing a text block
    from the vision entries, so statement-type classification stays
    in one place.
    """
    if not vision_entries:
        return []
    # Reconstruct as TOC-like text lines so the parser can classify them
    lines = [f"{e.statement_name}    {e.stated_page}" for e in vision_entries]
    return parse_toc_entries_from_text("\n".join(lines))


async def run_scout(
    pdf_path: Path | str,
    model: str = "google-gla:gemini-3-flash-preview",
    statements_to_find: Optional[set[StatementType]] = None,
    on_progress: Optional[callable] = None,
) -> Infopack:
    """Run the full scout pipeline on a PDF.

    Args:
        pdf_path: path to the annual report PDF.
        model: PydanticAI model for LLM vision calls.
        statements_to_find: if provided, only calibrate and include these
            statements. If None, find all 5 statement types.

    Returns:
        Infopack with validated page references for each requested statement.
    """
    pdf_path = Path(pdf_path)
    pdf_length = _get_pdf_length(pdf_path)

    async def _progress(msg: str) -> None:
        if on_progress:
            await on_progress(msg)

    # Step 1: Find candidate TOC pages
    await _progress("Finding table of contents...")
    logger.info("Finding TOC candidate pages...")
    toc_candidates = find_toc_candidate_pages(pdf_path)

    # Step 2: Extract TOC text and parse entries
    toc_page = toc_candidates[0].page_number if toc_candidates else 1

    # Try deterministic text extraction first
    candidate_page_nums = [c.page_number for c in toc_candidates[:3]]
    toc_text = _extract_text_from_pages(pdf_path, candidate_page_nums)
    toc_entries = parse_toc_entries_from_text(toc_text)

    # Fallback: if no text entries found, use LLM vision on candidate pages
    if not toc_entries:
        await _progress("No text TOC found, using vision extraction...")
        logger.info(
            "No text-based TOC entries found. Falling back to LLM vision "
            "for TOC extraction on candidate pages %s", candidate_page_nums,
        )
        vision_result = await extract_toc_via_vision(
            pdf_path, candidate_page_nums, model=model,
        )
        toc_entries = _vision_entries_to_toc_entries(vision_result.entries)

        if not toc_entries:
            logger.warning(
                "LLM vision also returned no TOC entries. "
                "Returning empty infopack — caller should handle this."
            )
            return Infopack(toc_page=toc_page, page_offset=0)

    await _progress(f"Found {len(toc_entries)} TOC entries on page {toc_page}")
    logger.info(f"Found {len(toc_entries)} TOC entries on page {toc_page}")

    # Filter to requested statements only
    if statements_to_find is not None:
        toc_entries = [
            e for e in toc_entries
            if e.statement_type is None or e.statement_type in statements_to_find
        ]

    # Step 3: Calibrate pages (LLM vision)
    await _progress("Calibrating page offsets...")
    logger.info("Calibrating page offsets...")
    calibration = await calibrate_pages(
        pdf_path=pdf_path,
        toc_entries=toc_entries,
        pdf_length=pdf_length,
        model=model,
    )

    # Step 4: Build infopack from calibration results
    # Find notes start page from TOC (will be offset-adjusted per statement)
    notes_stated_page = None
    for entry in toc_entries:
        name_lower = entry.statement_name.lower()
        if "note" in name_lower:
            notes_stated_page = entry.stated_page
            break

    statements: dict[StatementType, StatementPageRef] = {}
    for i, (st_type, cal_page) in enumerate(calibration.pages.items()):
        await _progress(f"Processing {st_type.value} ({i+1}/{len(calibration.pages)})...")
        # Skip LOW-confidence entries (unvalidated) — don't promote
        # unverified guesses into the infopack
        if cal_page.confidence == "LOW":
            logger.warning(
                "%s: calibration failed (no match in search window). "
                "Omitting from infopack — user must resolve manually.",
                st_type.value,
            )
            continue

        # Determine variant via hybrid LLM + deterministic detection
        variant = None
        if cal_page.actual_page > 0:
            page_text = _extract_text_from_pages(pdf_path, [cal_page.actual_page])
            detection = await detect_variant(
                statement_type=st_type,
                page_text=page_text,
                pdf_path=pdf_path,
                page_num=cal_page.actual_page,
                model=model,
            )
            if detection:
                variant = detection.variant
                if not detection.confident:
                    logger.info(
                        "%s: variant %r detected but not confident (method=%s)",
                        st_type.value, variant, detection.method,
                    )
        if not variant:
            # Fallback to first detectable variant for this statement type
            from statement_types import variants_for as _variants_for
            detectable = [v for v in _variants_for(st_type) if v.detection_signals]
            variant = detectable[0].name if detectable else _variants_for(st_type)[0].name

        # Discover note pages — use this statement's own calibrated offset
        # instead of a single "typical" offset
        note_pages: list[int] = []
        if cal_page.actual_page > 0 and notes_stated_page is not None:
            notes_start_calibrated = notes_stated_page + cal_page.offset
            face_text = _extract_text_from_pages(pdf_path, [cal_page.actual_page])
            note_pages = discover_note_pages(
                face_page_text=face_text,
                toc_entries=toc_entries,
                pdf_length=pdf_length,
                notes_start_page=notes_start_calibrated,
            )

        statements[st_type] = StatementPageRef(
            variant_suggestion=variant,
            face_page=cal_page.actual_page,
            note_pages=note_pages,
            confidence=cal_page.confidence,
        )

    # Compute representative page_offset from calibrated results
    offsets = [cp.offset for cp in calibration.pages.values() if cp.confidence == "HIGH"]
    typical_offset = offsets[0] if offsets else 0

    infopack = Infopack(
        toc_page=toc_page,
        page_offset=typical_offset,
        statements=statements,
    )

    logger.info(f"Scout complete: {len(statements)} statements mapped")
    return infopack
