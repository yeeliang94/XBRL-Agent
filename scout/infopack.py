"""Infopack: the typed output of the scout agent.

The scout reads a PDF's Table of Contents, calibrates page-number offsets
against actual PDF content, and produces an Infopack that tells each
extraction sub-agent exactly which pages to read.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Literal

from statement_types import StatementType
from scout.notes_discoverer import NoteInventoryEntry

logger = logging.getLogger(__name__)

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
    # Notes inventory built by the scout (A.2). Each entry is a
    # (note_num, title, page_range) triple describing one disclosure note
    # in the PDF. Consumed by the notes agents / sub-coordinator.
    notes_inventory: list[NoteInventoryEntry] = field(default_factory=list)

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
            "notes_inventory": [
                {
                    "note_num": e.note_num,
                    "title": e.title,
                    "page_range": list(e.page_range),
                }
                for e in self.notes_inventory
            ],
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
        inventory: list[NoteInventoryEntry] = []
        for idx, raw in enumerate(data.get("notes_inventory", []) or []):
            pr = raw.get("page_range", [])
            if isinstance(pr, list) and len(pr) == 2:
                page_range = (int(pr[0]), int(pr[1]))
            else:
                # Preserve the index so operators can correlate with source;
                # entries with malformed page_range are accepted but flagged.
                logger.warning(
                    "Infopack notes_inventory[%d] has malformed page_range %r; "
                    "defaulting to (0, 0)", idx, pr,
                )
                page_range = (0, 0)
            inventory.append(NoteInventoryEntry(
                note_num=int(raw["note_num"]),
                title=str(raw.get("title", "")),
                page_range=page_range,
            ))

        return cls(
            toc_page=data["toc_page"],
            page_offset=data["page_offset"],
            statements=statements,
            notes_inventory=inventory,
        )

    # -- Notes page hints ------------------------------------------------------

    def notes_page_hints(self) -> list[int]:
        """Union of every face-statement's face_page + note_pages, sorted unique.

        Scout's `notes_inventory` is empty on scanned PDFs (no extractable
        text for the header regex to bite on). Without hints the notes
        agents default to scanning the entire PDF, which on a typical
        Malaysian filing means rendering 30+ pages through the LLM just
        to find where Note 1 begins. This method surfaces the pages that
        face-statement scout scoring already identified as note-bearing,
        giving the notes agents a tight starting viewport even when the
        inventory is empty.

        Returns an empty list when no statement refs exist.
        """
        pages: set[int] = set()
        for ref in self.statements.values():
            pages.add(ref.face_page)
            pages.update(ref.note_pages)
        return sorted(p for p in pages if p >= 1)

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
