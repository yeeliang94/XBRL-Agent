"""Infopack: the typed output of the scout agent.

The scout reads a PDF's Table of Contents, calibrates page-number offsets
against actual PDF content, and produces an Infopack that tells each
extraction sub-agent exactly which pages to read.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from statement_types import StatementType

# Allowed confidence levels for page validation.
Confidence = Literal["HIGH", "MEDIUM", "LOW"]
_VALID_CONFIDENCE: set[str] = {"HIGH", "MEDIUM", "LOW"}


@dataclass
class StatementPageRef:
    """Scout's validated page reference for a single statement.

    Attributes:
        variant_suggestion: detected variant name (e.g. "CuNonCu"), to be
            confirmed or overridden by the user before extraction.
        face_page: 1-indexed PDF page containing the statement's face
            (primary table). Must be > 0.
        note_pages: 1-indexed PDF pages containing notes referenced by
            the statement. All entries must be > 0.
        confidence: how confident the scout is in the page mapping.
    """
    variant_suggestion: str
    face_page: int
    note_pages: list[int] = field(default_factory=list)
    confidence: Confidence = "HIGH"

    def __post_init__(self) -> None:
        if self.face_page < 1:
            raise ValueError(f"face_page must be >= 1, got {self.face_page}")
        if any(p < 1 for p in self.note_pages):
            bad = [p for p in self.note_pages if p < 1]
            raise ValueError(f"note_pages must all be >= 1, got invalid: {bad}")
        if self.confidence not in _VALID_CONFIDENCE:
            raise ValueError(
                f"confidence must be one of {_VALID_CONFIDENCE}, "
                f"got {self.confidence!r}"
            )


@dataclass
class Infopack:
    """Complete scout output for a PDF.

    Attributes:
        toc_page: 1-indexed PDF page where the Table of Contents was found.
        page_offset: the typical difference between TOC-stated page numbers
            and actual PDF page indices (e.g. +6 means TOC says "page 42"
            but the actual PDF page is 48).
        statements: per-statement validated page references. Only contains
            statements the scout was asked to find (or all 5 by default).
    """
    toc_page: int
    page_offset: int
    statements: dict[StatementType, StatementPageRef] = field(default_factory=dict)

    # -- Serialisation ---------------------------------------------------------

    def to_json(self) -> str:
        """Serialize to a JSON string for persistence / SSE transport."""
        return json.dumps({
            "toc_page": self.toc_page,
            "page_offset": self.page_offset,
            "statements": {
                st.value: {
                    "variant_suggestion": ref.variant_suggestion,
                    "face_page": ref.face_page,
                    "note_pages": ref.note_pages,
                    "confidence": ref.confidence,
                }
                for st, ref in self.statements.items()
            },
        }, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> Infopack:
        """Deserialize from a JSON string produced by to_json()."""
        data = json.loads(raw)
        statements: dict[StatementType, StatementPageRef] = {}
        for key, ref_data in data.get("statements", {}).items():
            st = StatementType(key)
            statements[st] = StatementPageRef(
                variant_suggestion=ref_data["variant_suggestion"],
                face_page=ref_data["face_page"],
                note_pages=ref_data.get("note_pages", []),
                confidence=ref_data.get("confidence", "HIGH"),
            )
        return cls(
            toc_page=data["toc_page"],
            page_offset=data["page_offset"],
            statements=statements,
        )

    # -- Validation ------------------------------------------------------------

    def validate_page_range(self, pdf_length: int) -> list[str]:
        """Check that all referenced pages fall within [1, pdf_length].

        Returns a list of human-readable error strings (empty = valid).
        """
        errors: list[str] = []
        for st, ref in self.statements.items():
            if ref.face_page > pdf_length:
                errors.append(
                    f"{st.value}: face_page {ref.face_page} exceeds "
                    f"PDF length {pdf_length}"
                )
            for p in ref.note_pages:
                if p > pdf_length:
                    errors.append(
                        f"{st.value}: note_page {p} exceeds "
                        f"PDF length {pdf_length}"
                    )
        return errors
