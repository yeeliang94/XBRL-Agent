"""Typed vocabulary for notes source integrity — plan Phase 3, Step 3.2.

The point of this feature is that "every part of the source was handled" is a
COUNT rather than a claim. That only holds if the ways a block can be handled
are a closed set. Free-text reasons are how such a count dies: every awkward
block gets a bespoke excuse, nothing is ever left unresolved, and the number
reads 100% forever.

So: fixed dispositions, a fixed reason list, and one reason
(`UNREADABLE_NEEDS_REVIEW`) that deliberately does NOT resolve a block —
"we could not read it" describes the problem, it does not decide it.

Nothing here touches the database; `notes/source_repository.py` does that.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional

ENV_VAR = "XBRL_NOTES_SOURCE_INTEGRITY"


class IntegrityMode(str, Enum):
    """Rollout mode. A boolean cannot express `shadow`, which is the whole
    point of the staged rollout: compute the verdict, record it, change
    nothing, and compare against today's behaviour before trusting it."""

    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"

    @property
    def computes(self) -> bool:
        """Whether the manifest and integrity check run at all."""
        return self is not IntegrityMode.OFF

    @property
    def changes_run_status(self) -> bool:
        """Whether an unresolved block can tip the run to
        `completed_with_errors`. Only `enforce`."""
        return self is IntegrityMode.ENFORCE


class GenerationStatus(str, Enum):
    BUILDING = "building"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class OwnerKind(str, Enum):
    """Who physically owns a block. Exactly one per block."""

    NOTE = "note"
    FURNITURE = "furniture"        # running headers, footers, page numbers
    METADATA = "metadata"          # cover page, document properties
    UNRESOLVED = "unresolved"


class Disposition(str, Enum):
    """What happened to a note-owned block."""

    INCLUDED = "included"                        # rendered into a disclosure
    STRUCTURED_CONSUMED = "structured_consumed"  # read into fields/figures
    ROUTED = "routed"                            # sent to another sheet
    EXCLUDED = "excluded"                        # deliberately not used
    UNRESOLVED = "unresolved"                    # nobody has decided yet


class ContentOrigin(str, Enum):
    """Where a rendered cell's TEXT came from. Separate from `style_source`,
    which records how its FORMATTING was decided."""

    SOURCE_EXACT = "source_exact"
    SOURCE_NORMALIZED = "source_normalized"
    VISION_TRANSCRIBED = "vision_transcribed"
    STRUCTURED_GENERATED = "structured_generated"
    HUMAN_MODIFIED = "human_modified"
    LEGACY = "legacy"


# Closed list. Each entry changes the meaning of "complete", so adding one
# needs product and accounting sign-off — pinned by
# tests/test_notes_source_models.py.
EXCLUSION_REASONS: frozenset[str] = frozenset({
    "PAGE_HEADER",
    "PAGE_FOOTER",
    "PAGE_NUMBER",
    "REPEATED_CONTINUATION_HEADING",
    "DUPLICATE_SOURCE_ARTIFACT",
    "DOCUMENT_METADATA",
    "OUTSIDE_SELECTED_FILING_SCOPE",
    "EXPLICIT_POLICY_ROUTE",
    "APPROVED_DUPLICATE_ROUTE",
    "UNREADABLE_NEEDS_REVIEW",
})

# Reasons that do NOT settle a block. Excluding something because it is a page
# footer is a decision; excluding it because it could not be read is not.
UNRESOLVED_REASONS: frozenset[str] = frozenset({"UNREADABLE_NEEDS_REVIEW"})

# The only reasons that SETTLE an excluded block. Derived rather than written
# out so a new entry in EXCLUSION_REASONS cannot be forgotten here, and an
# unknown code can never fall through as settled.
_SETTLING_REASONS: frozenset[str] = EXCLUSION_REASONS - UNRESOLVED_REASONS

_RESOLVING_DISPOSITIONS = frozenset({
    Disposition.INCLUDED,
    Disposition.STRUCTURED_CONSUMED,
    Disposition.ROUTED,
})

# Accepted for backwards compatibility with .env files and operators who write
# a boolean. A stale "1" enforcing silently would be worse than mapping it.
_TRUTHY = {"1", "true", "yes", "on"}
_FALSEY = {"0", "false", "no", "off", ""}


def integrity_mode(env: Optional[Mapping[str, str]] = None) -> IntegrityMode:
    """Resolve the rollout mode. Unrecognised values fail CLOSED to `off`:
    a typo must not start failing runs."""
    source = os.environ if env is None else env
    raw = str(source.get(ENV_VAR, "") or "").strip().lower()
    if raw in _FALSEY:
        return IntegrityMode.OFF
    if raw in _TRUTHY:
        return IntegrityMode.ENFORCE
    try:
        return IntegrityMode(raw)
    except ValueError:
        return IntegrityMode.OFF


def validate_disposition(
    disposition: Disposition, reason_code: Optional[str]
) -> None:
    """Raise ValueError if this pairing is not allowed."""
    if disposition is Disposition.EXCLUDED:
        if not reason_code:
            raise ValueError(
                "an excluded block needs a reason code from the approved list: "
                f"{', '.join(sorted(EXCLUSION_REASONS))}"
            )
        if reason_code not in EXCLUSION_REASONS:
            raise ValueError(
                f"unknown reason code {reason_code!r}. Approved codes: "
                f"{', '.join(sorted(EXCLUSION_REASONS))}"
            )
    elif reason_code is not None and reason_code not in EXCLUSION_REASONS:
        raise ValueError(f"unknown reason code {reason_code!r}")


def is_resolved(disposition: Disposition, reason_code: Optional[str]) -> bool:
    """Whether this block is settled, for run-status purposes.

    An exclusion settles a block only when its reason is one of the APPROVED
    codes and is not itself an unresolved one. Checking merely "not in
    UNRESOLVED_REASONS" would let an unrecognised code — a typo, a value from a
    newer build, a hand-edited row — read as settled. The `reason_code` column
    deliberately has no CHECK constraint (gotcha #11), so unknown values can
    exist, and treating one as resolved is precisely the false-green this
    feature exists to prevent. Unknown fails CLOSED.
    """
    if disposition in _RESOLVING_DISPOSITIONS:
        return True
    if disposition is Disposition.EXCLUDED:
        return reason_code in _SETTLING_REASONS
    return False


# --------------------------------------------------------------------------
# row shapes
# --------------------------------------------------------------------------

@dataclass
class SourceBlock:
    """One frozen piece of the source document."""

    block_id: str
    block_kind: str
    reading_order: int
    canonical_html: str = ""
    content_sha256: Optional[str] = None
    page: Optional[int] = None
    locator: Optional[dict] = None
    source_note_id: Optional[str] = None
    owner_kind: OwnerKind = OwnerKind.UNRESOLVED
    capture_confidence: Optional[float] = None
    table_group_id: Optional[str] = None
    continues_block_id: Optional[str] = None


@dataclass
class SourceNote:
    source_note_id: str
    top_note_num: str = ""
    title: str = ""
    block_ids: list[str] = field(default_factory=list)
    page_lo: Optional[int] = None
    page_hi: Optional[int] = None
    boundary_confidence: Optional[float] = None
    content_sha256: Optional[str] = None
    status: str = "frozen"


@dataclass
class BlockUsage:
    block_id: str
    disposition: Disposition = Disposition.UNRESOLVED
    reason_code: Optional[str] = None
    sheet: Optional[str] = None
    row: Optional[int] = None
    concept_uuid: Optional[str] = None
    target_kind: Optional[str] = None
    route_type: Optional[str] = None
    created_by: str = "system"

    def validate(self) -> None:
        validate_disposition(self.disposition, self.reason_code)

    @property
    def resolved(self) -> bool:
        return is_resolved(self.disposition, self.reason_code)
