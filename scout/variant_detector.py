"""Hybrid variant detection: LLM-first classification + deterministic cross-check.

Presentation choices (CuNonCu vs OrderOfLiquidity, Function vs Nature, etc.)
are semantic accounting judgments made harder by noisy OCR, so an LLM is the
better primary classifier.  The deterministic signal scorer is still valuable
as a cheap cross-check, confidence signal, and fallback when the LLM errors
or is uncertain.

Public API:
    detect_variant()             — async hybrid (LLM + deterministic)
    detect_variant_from_signals() — sync deterministic-only (kept for tests/fallback)
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model

from statement_types import StatementType, VARIANTS, variants_for
from tools.pdf_viewer import render_pages_to_images

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class VariantDetectionResult:
    """Outcome of hybrid variant detection."""
    variant: str              # e.g. "CuNonCu", "Function"
    confident: bool           # True when LLM+deterministic agree, or unambiguous
    method: str               # "hybrid", "deterministic", or "llm"


class _LlmVariantOutput(BaseModel):
    """Structured output the LLM returns for variant classification."""
    variant: str = Field(description="The variant name — must be one of the allowed values")
    confident: bool = Field(description="True if you are confident in this classification")
    reasoning: str = Field(description="Brief explanation of why this variant was chosen")


# ---------------------------------------------------------------------------
# LLM classification prompt
# ---------------------------------------------------------------------------

_VARIANT_PROMPT = """\
You are classifying the presentation variant of a Malaysian financial statement.

Statement type: {statement_type}
Allowed variants (pick exactly one):
{variant_list}

Classification rules:
{rules}

Look at the page image and extracted text below.  Pick the best variant.
Set confident=true only when you are sure.  If the page is unclear, noisy,
or could plausibly be either variant, set confident=false.
"""

_VARIANT_RULES: dict[StatementType, str] = {
    StatementType.SOFP: (
        "- CuNonCu: assets and liabilities are split into current / non-current sections\n"
        "- OrderOfLiquidity: assets are listed by liquidity (no current/non-current split). "
        "Common in financial institutions."
    ),
    StatementType.SOPL: (
        "- Function: expenses grouped by role — cost of sales, distribution, administrative\n"
        "- Nature: expenses grouped by type — raw materials, employee benefits, depreciation, "
        "changes in inventories"
    ),
    StatementType.SOCI: (
        "- BeforeTax: OCI items shown gross, with tax relating to each component shown separately\n"
        "- NetOfTax: OCI items shown net of their related tax"
    ),
    StatementType.SOCF: (
        "- Indirect: starts from profit before tax, adjusts for non-cash items and working capital\n"
        "- Direct: shows gross cash receipts from customers, cash paid to suppliers, etc."
    ),
    StatementType.SOCIE: "- Default: single template",
}


# ---------------------------------------------------------------------------
# LLM call (mockable seam for tests)
# ---------------------------------------------------------------------------

async def _classify_variant_via_llm(
    statement_type: StatementType,
    page_text: str,
    pdf_path: Path,
    page_num: int,
    model: str | Model,
    allowed_variants: list[str],
) -> _LlmVariantOutput:
    """Render the page and ask the LLM to classify the variant.

    This is the mockable seam — tests replace this with AsyncMock.
    """
    # Render page to image for vision
    with tempfile.TemporaryDirectory() as tmpdir:
        rendered = render_pages_to_images(
            str(pdf_path), start=page_num, end=page_num,
            output_dir=tmpdir, dpi=200,
        )
        if not rendered:
            raise RuntimeError(f"Failed to render page {page_num}")
        img_bytes = rendered[0].read_bytes()

    variant_list = "\n".join(f"  - {v}" for v in allowed_variants)
    rules = _VARIANT_RULES.get(statement_type, "Pick the best match.")
    system_prompt = _VARIANT_PROMPT.format(
        statement_type=statement_type.value,
        variant_list=variant_list,
        rules=rules,
    )

    agent: Agent[None, _LlmVariantOutput] = Agent(
        model,
        output_type=_LlmVariantOutput,
        system_prompt=system_prompt,
    )

    result = await agent.run([
        f"Classify this {statement_type.value} page.\n\nExtracted text:\n{page_text[:2000]}",
        BinaryContent(data=img_bytes, media_type="image/png"),
    ])
    return result.output


# ---------------------------------------------------------------------------
# Negative / absence signal tables (used by deterministic scorer)
# ---------------------------------------------------------------------------

_NEGATIVE_SIGNALS: dict[tuple[StatementType, str], tuple[str, ...]] = {
    (StatementType.SOFP, "OrderOfLiquidity"): (
        "non-current assets", "current assets",
        "non-current liabilities", "current liabilities",
    ),
    (StatementType.SOFP, "CuNonCu"): (
        "order of liquidity", "by liquidity",
    ),
}

_ABSENCE_BONUS: dict[tuple[StatementType, str], tuple[tuple[str, ...], int]] = {
    (StatementType.SOFP, "OrderOfLiquidity"): (
        ("non-current assets", "current assets", "non-current liabilities", "current liabilities"),
        3,
    ),
}


# ---------------------------------------------------------------------------
# Deterministic scorer (sync, no LLM)
# ---------------------------------------------------------------------------

def detect_variant_from_signals(
    statement_type: StatementType,
    page_text: str,
) -> Optional[str]:
    """Deterministic variant detection using detection_signals from the registry.

    Returns the best-matching variant name, or None if no signals matched.
    Kept as a standalone public function for tests, cross-checking, and fallback.
    """
    candidates = variants_for(statement_type)
    if not candidates:
        raise ValueError(f"No variants registered for {statement_type.value}")

    candidates = [v for v in candidates if v.detection_signals]

    if len(candidates) == 1:
        return candidates[0].name

    if not page_text.strip():
        return None

    lower_text = page_text.lower()
    best_name: Optional[str] = None
    best_score = 0

    for variant in candidates:
        score = sum(1 for sig in variant.detection_signals if sig in lower_text)

        neg_sigs = _NEGATIVE_SIGNALS.get((statement_type, variant.name), ())
        penalty = sum(2 for neg in neg_sigs if neg in lower_text)
        score -= penalty

        absence_entry = _ABSENCE_BONUS.get((statement_type, variant.name))
        if absence_entry:
            phrases, bonus = absence_entry
            if not any(p in lower_text for p in phrases):
                score += bonus

        if score > best_score:
            best_score = score
            best_name = variant.name

    return best_name


# ---------------------------------------------------------------------------
# Hybrid detector (async, LLM + deterministic)
# ---------------------------------------------------------------------------

async def detect_variant(
    statement_type: StatementType,
    page_text: str,
    pdf_path: Path,
    page_num: int,
    model: str | Model,
) -> Optional[VariantDetectionResult]:
    """Hybrid variant detection: LLM-first with deterministic cross-check.

    Flow:
      1. If only one detectable variant exists → return immediately (no LLM).
      2. If no page text → return None (nothing to classify).
      3. Call LLM for structured classification.
      4. Run deterministic scorer on the same text.
      5. Combine:
         - LLM confident + deterministic agrees → confident
         - LLM confident + deterministic disagrees → trust LLM, not confident
         - LLM not confident + deterministic has opinion → trust deterministic, not confident
         - LLM fails → use deterministic if available, not confident
         - Both fail → None

    Returns:
        VariantDetectionResult, or None if no variant could be determined.
    """
    candidates = [v for v in variants_for(statement_type) if v.detection_signals]
    if not candidates:
        raise ValueError(f"No detectable variants for {statement_type.value}")

    # Trivial case: single variant, no ambiguity
    if len(candidates) == 1:
        return VariantDetectionResult(
            variant=candidates[0].name,
            confident=True,
            method="deterministic",
        )

    # No text → nothing to classify
    if not page_text.strip():
        return None

    allowed_names = [v.name for v in candidates]

    # Deterministic scorer (always runs — cheap)
    det_result = detect_variant_from_signals(statement_type, page_text)

    # LLM classification (primary)
    llm_variant: Optional[str] = None
    llm_confident: bool = False
    llm_ok = False

    try:
        llm_output = await _classify_variant_via_llm(
            statement_type=statement_type,
            page_text=page_text,
            pdf_path=pdf_path,
            page_num=page_num,
            model=model,
            allowed_variants=allowed_names,
        )
        # Validate the LLM didn't hallucinate a variant name
        if llm_output.variant in allowed_names:
            llm_variant = llm_output.variant
            llm_confident = llm_output.confident
            llm_ok = True
        else:
            logger.warning(
                "%s: LLM returned unknown variant %r (allowed: %s). Ignoring.",
                statement_type.value, llm_output.variant, allowed_names,
            )
    except Exception:
        logger.warning(
            "%s: LLM variant classification failed, falling back to deterministic",
            statement_type.value, exc_info=True,
        )

    # --- Combine signals ---

    # LLM succeeded with a valid variant
    if llm_ok and llm_variant:
        if llm_confident and det_result == llm_variant:
            # Both agree → confident
            return VariantDetectionResult(
                variant=llm_variant, confident=True, method="hybrid",
            )
        if llm_confident:
            # LLM confident but deterministic disagrees or has no opinion
            # → trust LLM, flag as not confident
            return VariantDetectionResult(
                variant=llm_variant, confident=False, method="hybrid",
            )
        # LLM not confident
        if det_result:
            # Deterministic has an opinion → prefer it
            return VariantDetectionResult(
                variant=det_result, confident=False, method="hybrid",
            )
        # Both uncertain → use LLM's guess, not confident
        return VariantDetectionResult(
            variant=llm_variant, confident=False, method="hybrid",
        )

    # LLM failed or returned invalid variant
    if det_result:
        return VariantDetectionResult(
            variant=det_result, confident=False, method="deterministic",
        )

    # Both failed
    return None
