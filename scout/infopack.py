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

# Scout's MFRS-vs-MPERS guess. "unknown" when the signals are ambiguous or
# absent — the UI falls back to the user toggle default (MFRS) without
# prompting. Narrowed to these three strings by `detect_filing_standard`.
DetectedStandard = Literal["mfrs", "mpers", "unknown"]
_VALID_DETECTED_STANDARD: set[str] = {"mfrs", "mpers", "unknown"}

# Phase 2 — units the agent may see on a face page header. "unknown" is
# the safe default — the prompt renders a loud "VERIFY UNIT" block in
# that case so the agent always reads the header itself. Putting a
# wrong value here produces a silent 1000× extraction error, so the
# resolver is intentionally restrictive about what it accepts.
ScaleUnit = Literal["units", "thousands", "millions", "unknown"]
_VALID_SCALE_UNIT: set[str] = {"units", "thousands", "millions", "unknown"}

# Filing-level guess from the cover / face-page header. Group filings
# show both consolidated and standalone columns; company-only filings
# show only the company columns. Scout's claim is advisory — the UI
# toggle wins, but a strong scout signal can preselect.
ConsolidationLevel = Literal["company", "group", "both", "unknown"]
_VALID_CONSOLIDATION: set[str] = {"company", "group", "both", "unknown"}
# Source-honesty (rewrite Phase 6.3): how the notes inventory was built.
_VALID_INVENTORY_SOURCE: set[str] = {"text", "vision", "none", "unknown"}

# Sparse-inventory heuristic (completeness_warnings check 3c). A real notes
# section averages well under 4 pages per note; anything thinner than that
# across a section of at least 8 pages means discovery mostly failed. Set
# deliberately loose — this warns, it never blocks, and a false positive on an
# unusually verbose filing costs the operator one glance at the PDF.
_SPARSE_MIN_SPAN = 8
_SPARSE_PAGES_PER_NOTE = 4

def _registered_variant_names(st: StatementType) -> set[str]:
    """Closed set of registered variant names for a statement.

    Used by ``Infopack.from_json`` to whitelist-clamp ``variant_suggestion``
    the way ``scale_unit`` is clamped against ``_VALID_SCALE_UNIT`` — the
    registry (statement_types.VARIANTS) is the single source of truth, so no
    literal list is duplicated here.
    """
    from statement_types import variants_for

    return {v.name for v in variants_for(st)}


# Item 4 (PLAN-orchestration-hardening): hard ceiling on a plausible
# disclosure-note number. Malaysian filings rarely exceed ~60 notes; a
# hallucinated "Note 743" on a 30-note filing sends a face agent hunting
# for a note that doesn't exist. Save-time filtering tightens further to
# max(inventory note_num) + 5 when the deterministic inventory exists.
MAX_PLAUSIBLE_NOTE_NUM = 150


@dataclass
class FaceLineRef:
    """One line item observed on a statement's face page.

    Carries the label, the cross-reference to a disclosure note number (if
    the face page cited one), and the section header the line sits under.
    Populated by the deterministic ``read_face_structure`` parser on text
    PDFs, or by the scout LLM emitting structured JSON on scanned PDFs
    (where PyMuPDF returns no text). Soft advisory only — downstream face
    agents are still required to verify against the PDF.
    """
    label: str
    note_num: Optional[int] = None
    section: Optional[str] = None

    def __post_init__(self) -> None:
        # Empty label = nothing for the prompt renderer to display; reject
        # so the failure surfaces in scout rather than producing a blank
        # bullet downstream.
        if not self.label or not self.label.strip():
            raise ValueError("FaceLineRef.label must be non-empty")
        if self.note_num is not None and self.note_num < 1:
            raise ValueError(
                f"FaceLineRef.note_num must be >= 1 when set, got {self.note_num}"
            )


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
        face_line_refs: structural map of face-page line items to their
            cited note numbers. Empty when scout did not read the face
            page in detail (e.g. scanned PDF with no vision-LLM
            available, or text PDF where the regex parser found
            nothing). Populated as advisory hints for face agents.
        face_read_in_detail: True iff scout viewed the face page AND
            either ``face_line_refs`` was populated by the regex parser
            or the LLM explicitly confirmed it from a vision read.
            Downstream agents use this to decide whether to trust the
            structural hints or fall back to self-discovery.
    """
    variant_suggestion: str
    face_page: int
    note_pages: list[int] = field(default_factory=list)
    confidence: Confidence = "HIGH"
    face_line_refs: list[FaceLineRef] = field(default_factory=list)
    face_read_in_detail: bool = False

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
    # Phase 2 — entity / period / unit context observed by scout on the
    # cover or face-page headers. All optional, all advisory: the prompt
    # renderer surfaces each value with a loud "VERIFY against the PDF"
    # framing because a wrong unit produces a silent 1000× error
    # (gotcha #17's sibling failure mode). Defaults are designed so the
    # block omits cleanly when scout couldn't enrich.
    entity_name: Optional[str] = None
    reporting_period_cy: Optional[str] = None
    reporting_period_py: Optional[str] = None
    currency: str = "RM"
    scale_unit: ScaleUnit = "unknown"
    consolidation_level: ConsolidationLevel = "unknown"
    # Scout's MFRS-vs-MPERS guess from the TOC / front-matter text. The UI
    # uses this to preselect the filing-standard toggle; the user always
    # wins. "unknown" when the signals are ambiguous (e.g. both MFRS and
    # MPERS keywords appear).
    detected_standard: DetectedStandard = "unknown"
    # Source-honesty (rewrite Phase 6.3): how the notes inventory was built —
    # "text" (deterministic PyMuPDF regex), "vision" (LLM/OCR fallback for
    # scanned PDFs — hidden determinism worth surfacing), "none" (nothing
    # found), or "unknown" (no inventory pass ran). Advisory/telemetry only.
    inventory_source: str = "unknown"
    # Degradation honesty: True iff the scout pass did NOT complete normally
    # (per-turn timeout / wall-clock cap) and this pack is whatever partial
    # state it had managed to build. The run can still proceed without hints
    # (gotcha #13), but the caller must NOT report the scout as "succeeded" —
    # it marks the audit row failed (with the timeout error_type) and emits
    # `scout_complete success:false`. Runtime-only signal: intentionally NOT
    # serialised, so a persisted/reloaded pack reads back as non-degraded (a
    # re-run's scout might succeed).
    degraded: bool = False
    degraded_reason: Optional[str] = None

    # -- Serialisation ---------------------------------------------------------

    def to_json(self) -> str:
        """Serialize to a JSON string for persistence / SSE transport."""
        return json.dumps({
            "toc_page": self.toc_page,
            "page_offset": self.page_offset,
            "detected_standard": self.detected_standard,
            "inventory_source": self.inventory_source,
            # Phase 2 — context fields. Always serialised so the loader
            # sees the same shape every time; consumers branch on the
            # values to decide whether to render.
            "entity_name": self.entity_name,
            "reporting_period_cy": self.reporting_period_cy,
            "reporting_period_py": self.reporting_period_py,
            "currency": self.currency,
            "scale_unit": self.scale_unit,
            "consolidation_level": self.consolidation_level,
            "statements": {
                st.value: {
                    "variant_suggestion": ref.variant_suggestion,
                    "face_page": ref.face_page,
                    "note_pages": ref.note_pages,
                    "confidence": ref.confidence,
                    # Phase 1a — structural face-page hints. Empty list /
                    # False are the safe defaults consumers fall back to
                    # when scout couldn't enrich (scanned PDF without
                    # vision model, regex returned nothing, etc).
                    "face_line_refs": [
                        {
                            "label": r.label,
                            "note_num": r.note_num,
                            "section": r.section,
                        }
                        for r in ref.face_line_refs
                    ],
                    "face_read_in_detail": ref.face_read_in_detail,
                }
                for st, ref in self.statements.items()
            },
            "notes_inventory": [
                {
                    "note_num": e.note_num,
                    "title": e.title,
                    "page_range": list(e.page_range),
                    # Phase 1b — nested sub-note structure (display-only,
                    # never participates in Sheet-12 fan-out). Empty list
                    # is the safe default; existing consumers ignore it.
                    "subnotes": [
                        {
                            "subnote_ref": s.subnote_ref,
                            "title": s.title,
                            "page_range": list(s.page_range),
                        }
                        for s in getattr(e, "subnotes", []) or []
                    ],
                }
                for e in self.notes_inventory
            ],
        }, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> Infopack:
        """Deserialize from a JSON string produced by to_json()."""
        data = json.loads(raw)
        statements: dict[StatementType, StatementPageRef] = {}
        # Item 3: count what defensive deserialisation drops so degradation
        # is visible in one summary line, not only in scattered per-entry
        # warnings (each drop site below already warns individually).
        dropped_face_refs = 0
        malformed_inventory = 0
        for key, ref_data in data.get("statements", {}).items():
            st = StatementType(key)
            # Decode the optional Phase 1a face_line_refs list. Malformed
            # entries are skipped with a warning rather than failing the
            # whole load — same defensive posture as notes_inventory below
            # so a single bad entry in a persisted infopack doesn't
            # abort a rerun.
            face_line_refs: list[FaceLineRef] = []
            for idx, raw in enumerate(ref_data.get("face_line_refs", []) or []):
                if not isinstance(raw, dict):
                    logger.warning(
                        "Infopack statements[%s].face_line_refs[%d] is not "
                        "a dict (%r); skipping", key, idx, raw,
                    )
                    dropped_face_refs += 1
                    continue
                label = raw.get("label", "")
                if not isinstance(label, str) or not label.strip():
                    logger.warning(
                        "Infopack statements[%s].face_line_refs[%d] has "
                        "empty/non-string label; skipping", key, idx,
                    )
                    dropped_face_refs += 1
                    continue
                raw_note = raw.get("note_num")
                note_num: Optional[int]
                if raw_note is None:
                    note_num = None
                else:
                    try:
                        note_num = int(raw_note)
                    except (TypeError, ValueError):
                        logger.warning(
                            "Infopack statements[%s].face_line_refs[%d] has "
                            "non-integer note_num %r; treating as None",
                            key, idx, raw_note,
                        )
                        note_num = None
                section_raw = raw.get("section")
                section = section_raw if isinstance(section_raw, str) and section_raw else None
                try:
                    face_line_refs.append(FaceLineRef(
                        label=label,
                        note_num=note_num,
                        section=section,
                    ))
                except ValueError as e:
                    logger.warning(
                        "Infopack statements[%s].face_line_refs[%d] rejected: %s",
                        key, idx, e,
                    )
                    dropped_face_refs += 1
            # Whitelist-clamp variant_suggestion against the registered
            # variants for this statement — same posture as scale_unit
            # below. A persisted infopack is re-rendered into FUTURE runs'
            # prompts (entity_memory prior-year advisory), so free-form
            # scout-LLM output here is a cross-run prompt-injection channel
            # unless laundered at parse time (code-review fix, 2026-06-13).
            variant_raw = ref_data["variant_suggestion"]
            if not isinstance(variant_raw, str):
                logger.warning(
                    "Infopack statements[%s].variant_suggestion %r is not a "
                    "string; coercing to ''", key, variant_raw,
                )
                variant_raw = ""
            elif variant_raw and variant_raw not in _registered_variant_names(st):
                logger.warning(
                    "Infopack statements[%s].variant_suggestion %r is not a "
                    "registered variant; coercing to ''", key, variant_raw,
                )
                variant_raw = ""
            statements[st] = StatementPageRef(
                variant_suggestion=variant_raw,
                face_page=ref_data["face_page"],
                note_pages=ref_data.get("note_pages", []),
                confidence=ref_data.get("confidence", "HIGH"),
                face_line_refs=face_line_refs,
                face_read_in_detail=bool(ref_data.get("face_read_in_detail", False)),
            )
        # Local import keeps the SubNote symbol close to the loader so
        # operators reading from_json see the exact shape it accepts.
        from scout.notes_discoverer import SubNoteInventoryEntry

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
                malformed_inventory += 1
                page_range = (0, 0)

            # Phase 1b — decode nested subnotes. Malformed entries are
            # dropped silently (matching the inventory-level posture)
            # so a single bad subnote doesn't abort the parent load.
            subnotes: list[SubNoteInventoryEntry] = []
            for sidx, sraw in enumerate(raw.get("subnotes", []) or []):
                if not isinstance(sraw, dict):
                    continue
                ref = sraw.get("subnote_ref", "")
                if not isinstance(ref, str) or not ref.strip():
                    continue
                spr = sraw.get("page_range", [])
                if isinstance(spr, list) and len(spr) == 2:
                    try:
                        spage_range = (int(spr[0]), int(spr[1]))
                    except (TypeError, ValueError):
                        continue
                else:
                    continue
                try:
                    subnotes.append(SubNoteInventoryEntry(
                        subnote_ref=ref,
                        title=str(sraw.get("title", "")),
                        page_range=spage_range,
                    ))
                except ValueError as e:
                    logger.warning(
                        "Infopack notes_inventory[%d].subnotes[%d] rejected: %s",
                        idx, sidx, e,
                    )

            inventory.append(NoteInventoryEntry(
                note_num=int(raw["note_num"]),
                title=str(raw.get("title", "")),
                page_range=page_range,
                subnotes=subnotes,
            ))

        detected = data.get("detected_standard", "unknown")
        # Defensive — any unexpected value from an upstream change should
        # fall back to "unknown" rather than crash Infopack reconstruction.
        if detected not in _VALID_DETECTED_STANDARD:
            detected = "unknown"

        # Phase 2 — context fields. Each is independently optional and
        # defensively narrowed so a malformed value lands as the safe
        # default rather than crashing the load.
        scale_unit = data.get("scale_unit", "unknown")
        if scale_unit not in _VALID_SCALE_UNIT:
            logger.warning(
                "Infopack scale_unit %r unknown; coercing to 'unknown'", scale_unit,
            )
            scale_unit = "unknown"
        consolidation = data.get("consolidation_level", "unknown")
        if consolidation not in _VALID_CONSOLIDATION:
            logger.warning(
                "Infopack consolidation_level %r unknown; coercing to 'unknown'",
                consolidation,
            )
            consolidation = "unknown"
        entity_name_raw = data.get("entity_name")
        entity_name = entity_name_raw if isinstance(entity_name_raw, str) and entity_name_raw.strip() else None
        cy_raw = data.get("reporting_period_cy")
        reporting_period_cy = cy_raw if isinstance(cy_raw, str) and cy_raw.strip() else None
        py_raw = data.get("reporting_period_py")
        reporting_period_py = py_raw if isinstance(py_raw, str) and py_raw.strip() else None
        currency_raw = data.get("currency", "RM")
        currency = currency_raw if isinstance(currency_raw, str) and currency_raw.strip() else "RM"

        # Source-honesty (Phase 6.3): narrow to the known method labels; an
        # unexpected upstream value lands as "unknown" rather than crashing.
        inv_source = data.get("inventory_source", "unknown")
        if inv_source not in _VALID_INVENTORY_SOURCE:
            inv_source = "unknown"

        # Item 3: one loud summary so a degraded load can't hide in the
        # per-entry warning noise above.
        if dropped_face_refs or malformed_inventory:
            logger.warning(
                "Infopack load degraded: %d face ref(s) dropped, %d "
                "inventory entr(y/ies) with malformed page_range",
                dropped_face_refs, malformed_inventory,
            )

        return cls(
            toc_page=data["toc_page"],
            page_offset=data["page_offset"],
            statements=statements,
            notes_inventory=inventory,
            detected_standard=detected,
            entity_name=entity_name,
            reporting_period_cy=reporting_period_cy,
            reporting_period_py=reporting_period_py,
            currency=currency,
            scale_unit=scale_unit,
            consolidation_level=consolidation,
            inventory_source=inv_source,
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

    # -- Completeness probe ----------------------------------------------------

    def completeness_warnings(self) -> list[str]:
        """Surface signs the scout produced a DEGRADED pack before fan-out.

        Distinct from ``validate_page_range`` (which bounds-checks pages):
        this probe looks at *content completeness*. The infopack is a
        compression artifact every downstream face + notes agent consumes
        identically, so a silently dropped note or a mis-stated unit fans the
        loss out to the whole run (the "error-propagation cascade" failure
        mode). These are WARNINGS, never fatal — scout output is advisory
        (gotcha #13); the point is observability so a bad scout is visible at
        the pre-flight boundary instead of only showing up as wrong numbers.

        Returns a list of human-readable warning strings (empty = clean).
        """
        warnings: list[str] = []

        # 1. Unknown scale is the highest-cost ambiguity — a wrong unit is a
        #    silent 1000x error on every value (gotcha #17's sibling). The
        #    prompt already renders a loud VERIFY block in this case, but
        #    surfacing it here lets the operator see the risk up front.
        if self.scale_unit == "unknown":
            warnings.append(
                "scale_unit is 'unknown' — every extracted value depends on "
                "the agent reading the header correctly; verify the filing's "
                "presentation unit (units / thousands / millions)."
            )

        # 2. An empty inventory after a pass actually ran ('text'/'vision')
        #    means discovery found nothing — on a scanned PDF this is the
        #    vision-fallback failing, which leaves notes agents scanning blind.
        if not self.notes_inventory and self.inventory_source in {"text", "vision"}:
            warnings.append(
                f"notes_inventory is empty although the '{self.inventory_source}' "
                "discovery pass ran — notes agents will have no page hints and "
                "must scan the whole PDF."
            )

        # 3. Gaps in the observed note numbering (e.g. 1,2,3,7 -> missing 4-6)
        #    are a cheap, deterministic signal that scout dropped notes between
        #    the lowest and highest it did find.
        note_nums = sorted(
            {e.note_num for e in self.notes_inventory if e.note_num is not None}
        )
        if note_nums:
            expected = set(range(note_nums[0], note_nums[-1] + 1))
            missing = sorted(expected - set(note_nums))
            if missing:
                preview = ", ".join(str(n) for n in missing[:10])
                more = "…" if len(missing) > 10 else ""
                warnings.append(
                    f"notes_inventory has gaps: note number(s) {preview}{more} "
                    f"were not found between Note {note_nums[0]} and "
                    f"Note {note_nums[-1]} — scout may have dropped them."
                )

            # 3b. The interior check above is blind at BOTH ends: a filing whose
            #     Note 1 or highest note was dropped still looks contiguous. Run
            #     74 lost Note 22 (the last one) and produced no warning at all.
            #     A numbering that doesn't start at 1 is the observable tell for
            #     the leading case; the trailing case can't be proven from the
            #     inventory alone, so we flag the risk rather than a fact.
            if note_nums[0] != 1:
                warnings.append(
                    f"notes_inventory starts at Note {note_nums[0]}, not Note 1 "
                    f"— note(s) 1-{note_nums[0] - 1} were never found. Check the "
                    "PDF and add them before running."
                )

        # 3c. A sparse inventory over a wide notes section means discovery
        #     mostly failed — but not completely, so the empty-inventory check
        #     (2) stays silent and the vision fallback never fires (it only
        #     triggers on a strictly empty result). Density is the only signal
        #     available without re-reading the PDF.
        if note_nums:
            page_starts = [
                e.page_range[0] for e in self.notes_inventory if e.page_range
            ]
            page_ends = [e.page_range[1] for e in self.notes_inventory if e.page_range]
            span = (max(page_ends) - min(page_starts) + 1) if page_starts else 0
            if span >= _SPARSE_MIN_SPAN and len(note_nums) < span / _SPARSE_PAGES_PER_NOTE:
                warnings.append(
                    f"notes_inventory looks sparse: only {len(note_nums)} note(s) "
                    f"found across {span} pages of notes. Discovery probably "
                    "missed some — check the PDF, or re-scan with the "
                    "scanned-image option if the text layer is poor."
                )

        # 4. Missing entity / reporting period weakens the scout-context block
        #    every agent reads for cross-referencing.
        if not self.entity_name:
            warnings.append(
                "entity_name was not captured — the scout-context block will "
                "omit the entity name."
            )
        if not self.reporting_period_cy:
            warnings.append(
                "reporting_period_cy was not captured — agents cannot "
                "cross-check the current-year column against a known period."
            )

        return warnings
