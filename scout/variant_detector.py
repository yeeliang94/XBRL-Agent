"""Deterministic variant detection via signal scoring.

Used by the scout agent's check_variant_signals tool as a cross-check
against the agent's visual classification.  The agent (LLM) is the
primary classifier; this module provides a cheap, fast second opinion.

Public API:
    detect_variant_from_signals() — sync deterministic-only scorer
"""
from __future__ import annotations

from typing import Optional

from statement_types import (
    StatementType,
    VARIANTS,
    variants_for,
    variants_for_standard,
)


# Negative signals: if ANY of these appear, penalise the variant.
_NEGATIVE_SIGNALS: dict[tuple[StatementType, str], tuple[str, ...]] = {
    (StatementType.SOFP, "OrderOfLiquidity"): (
        "non-current assets", "current assets",
        "non-current liabilities", "current liabilities",
    ),
    (StatementType.SOFP, "CuNonCu"): (
        "order of liquidity", "by liquidity",
    ),
}

# Absence bonus: if NONE of these appear, award bonus points.
_ABSENCE_BONUS: dict[tuple[StatementType, str], tuple[tuple[str, ...], int]] = {
    (StatementType.SOFP, "OrderOfLiquidity"): (
        ("non-current assets", "current assets", "non-current liabilities", "current liabilities"),
        3,
    ),
}


def detect_variant_from_signals(
    statement_type: StatementType,
    page_text: str,
    standard: Optional[str] = None,
) -> Optional[str]:
    """Deterministic variant detection using detection_signals from the registry.

    Args:
        statement_type: the face statement being classified.
        page_text: rendered page text for scoring.
        standard: optional "mfrs" | "mpers". When provided, restricts the
            candidate set to variants registered for that standard — so MPERS
            text that mentions "Statement of Retained Earnings" can score
            SoRE on MPERS runs while MFRS runs stay on Default (SoRE is gated
            out by the registry anyway, but filtering here keeps the scorer
            honest about what it saw).

    Returns the best-matching variant name, or None if no signals matched.
    """
    if standard in ("mfrs", "mpers"):
        candidates = variants_for_standard(statement_type, standard)
    else:
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
