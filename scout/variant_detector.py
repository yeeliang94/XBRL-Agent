"""Deterministic variant detection using detection_signals from the registry.

Scores each variant's detection_signals against page text and returns
the best match. Used as a fallback/supplement when the LLM's variant
suggestion is unavailable or needs confirmation.
"""
from __future__ import annotations

from statement_types import StatementType, VARIANTS, variants_for


def detect_variant_from_signals(
    statement_type: StatementType,
    page_text: str,
) -> str:
    """Detect the most likely variant by matching detection_signals against text.

    Each variant's detection_signals are checked as case-insensitive substrings.
    The variant with the most matching signals wins. If tied or no matches,
    returns the first registered variant as a safe default.

    Args:
        statement_type: which statement to detect the variant for.
        page_text: text from the statement's face page (from OCR or PyMuPDF).

    Returns:
        The variant name (e.g. "CuNonCu", "Function", "Indirect").
    """
    candidates = variants_for(statement_type)
    if not candidates:
        raise ValueError(f"No variants registered for {statement_type.value}")

    # Single variant = no ambiguity
    if len(candidates) == 1:
        return candidates[0].name

    lower_text = page_text.lower()
    best_name = candidates[0].name
    best_score = 0

    for variant in candidates:
        score = sum(1 for sig in variant.detection_signals if sig in lower_text)
        if score > best_score:
            best_score = score
            best_name = variant.name

    return best_name
