"""The integrity checks — plan Phase 7, Step 7.1.

Pure functions over a snapshot. No database, no network, no clock: given the
same manifest, dispositions and cells, these return the same findings, which is
what makes a stored verdict re-explainable months later.

Every check answers one question of the form "is there a part of the source
this run cannot account for?". They are deliberately separate functions in one
registry so each can have a fixture that fails it and a fixture that passes it,
and so `RULE_VERSION` moves when the SET of questions changes.

Severity is a two-value thing on purpose:

* ``UNRESOLVED`` — something is unaccounted for. In `enforce` this tips the run.
* ``WARNING`` — worth showing, never blocking.

There is no third level, because a middle severity is where "we know this is
wrong but we ship anyway" accumulates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from notes.source_models import (
    Disposition,
    OwnerKind,
    SourceBlock,
    SourceNote,
    is_resolved,
)

# Bump when a check is added, removed, or changes what it counts as a problem.
# Stored on every result so an old verdict is never silently re-interpreted
# under new rules.
RULE_VERSION = "integrity-1"

UNRESOLVED = "unresolved"
WARNING = "warning"


@dataclass
class Finding:
    check: str
    severity: str
    message: str
    block_ids: list[str] = field(default_factory=list)
    note_num: Optional[str] = None

    @property
    def blocking(self) -> bool:
        return self.severity == UNRESOLVED


@dataclass
class CellRecord:
    """What a rendered notes cell claims about itself."""

    sheet: str
    row: int
    block_ids: list[str] = field(default_factory=list)
    rendered_sha256: Optional[str] = None
    current_sha256: Optional[str] = None
    content_origin: Optional[str] = None
    rendered_chars: int = 0
    cap: int = 0
    note_num: Optional[str] = None


@dataclass
class IntegrityInput:
    blocks: Sequence[SourceBlock] = ()
    notes: Sequence[SourceNote] = ()
    usages: dict = field(default_factory=dict)   # block_id -> row-like mapping
    cells: Sequence[CellRecord] = ()
    boundary_disagreements: Sequence = ()
    scout_available: bool = False
    pages_expected: int = 0
    pages_processed: int = 0
    approved_duplicate_block_ids: frozenset = frozenset()


@dataclass
class IntegrityResult:
    findings: list[Finding]
    rule_version: str = RULE_VERSION

    @property
    def requires_review(self) -> bool:
        return any(f.blocking for f in self.findings)

    def by_check(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.check, []).append(f)
        return out


def _disposition_of(usages: dict, block_id: str) -> tuple[Optional[Disposition], Optional[str]]:
    u = usages.get(block_id)
    if u is None:
        return None, None
    raw = u["disposition"] if hasattr(u, "keys") else getattr(u, "disposition", None)
    reason = u["reason_code"] if hasattr(u, "keys") else getattr(u, "reason_code", None)
    try:
        return Disposition(raw), reason
    except ValueError:
        return None, reason


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------

def check_page_receipts(inp: IntegrityInput) -> list[Finding]:
    """Every page of the source was read."""
    if inp.pages_expected and inp.pages_processed < inp.pages_expected:
        return [Finding(
            "page_receipts", UNRESOLVED,
            f"{inp.pages_processed} of {inp.pages_expected} source pages were "
            "read; the rest are unaccounted for",
        )]
    return []


def check_block_ownership(inp: IntegrityInput) -> list[Finding]:
    """Every block has exactly one owner, and none is left unowned."""
    out: list[Finding] = []
    for b in inp.blocks:
        if b.owner_kind is OwnerKind.UNRESOLVED:
            out.append(Finding(
                "block_ownership", UNRESOLVED,
                f"block {b.block_id} was not assigned to a note, to furniture "
                "or to material outside the notes scope",
                [b.block_id],
            ))
        elif b.owner_kind is OwnerKind.NOTE and not b.source_note_id:
            out.append(Finding(
                "block_ownership", UNRESOLVED,
                f"block {b.block_id} is marked as note content but names no note",
                [b.block_id],
            ))
    return out


def check_dispositions(inp: IntegrityInput) -> list[Finding]:
    """Every block has a decision, and the decision is one we recognise."""
    out: list[Finding] = []
    for b in inp.blocks:
        disposition, reason = _disposition_of(inp.usages, b.block_id)
        if disposition is None:
            out.append(Finding(
                "disposition", UNRESOLVED,
                f"block {b.block_id} has no recognised decision recorded",
                [b.block_id], b.source_note_id,
            ))
            continue
        if disposition is Disposition.UNRESOLVED:
            out.append(Finding(
                "disposition", UNRESOLVED,
                f"block {b.block_id} is still unresolved",
                [b.block_id], b.source_note_id,
            ))
        elif not is_resolved(disposition, reason):
            out.append(Finding(
                "disposition", UNRESOLVED,
                f"block {b.block_id} was excluded as {reason or 'no reason'}, "
                "which describes a problem rather than settling it",
                [b.block_id], b.source_note_id,
            ))
    return out


def check_prose_note_coverage(inp: IntegrityInput) -> list[Finding]:
    """A prose note is complete only when every one of its blocks is settled."""
    out: list[Finding] = []
    for n in inp.notes:
        missing = [
            bid for bid in n.block_ids
            if not is_resolved(*_disposition_of(inp.usages, bid))
            or _disposition_of(inp.usages, bid)[0] is None
        ]
        if missing:
            out.append(Finding(
                "note_coverage", UNRESOLVED,
                f"note {n.top_note_num} has {len(missing)} of "
                f"{len(n.block_ids)} parts still unaccounted for",
                missing, n.top_note_num,
            ))
    return out


def check_table_groups(inp: IntegrityInput) -> list[Finding]:
    """A table split across a page break is one disclosure, so its segments
    must share a fate. Half a table rendered while the other half is dropped
    reads as complete on a per-block count."""
    groups: dict[str, list[SourceBlock]] = {}
    for b in inp.blocks:
        if b.table_group_id:
            groups.setdefault(b.table_group_id, []).append(b)

    out: list[Finding] = []
    for group, members in groups.items():
        outcomes = {
            _disposition_of(inp.usages, b.block_id)[0] for b in members
        }
        if len(outcomes) > 1:
            out.append(Finding(
                "table_group", UNRESOLVED,
                f"the segments of one table ({group}) were handled "
                "differently; a table split across a page break is one "
                "disclosure",
                [b.block_id for b in members],
                members[0].source_note_id,
            ))
    return out


def check_note_continuity(inp: IntegrityInput) -> list[Finding]:
    """Numbered notes run without a hole, or the hole is explained."""
    nums = sorted(
        int(n.top_note_num) for n in inp.notes if str(n.top_note_num).isdigit()
    )
    out: list[Finding] = []
    for lo, hi in zip(nums, nums[1:]):
        for missing in range(lo + 1, hi):
            out.append(Finding(
                "note_continuity", UNRESOLVED,
                f"note {missing} is missing between {lo} and {hi}",
                note_num=str(missing),
            ))
    return out


def check_boundaries(inp: IntegrityInput) -> list[Finding]:
    """Step 4.4 — an unresolved boundary disagreement tips the run the same way
    an unresolved block does. Measuring it without gating on it was the peer
    finding that made this a blocker: a mis-assigned block shows 100%
    completeness and a wrong answer."""
    out: list[Finding] = []
    for d in inp.boundary_disagreements:
        kind = d["kind"] if hasattr(d, "keys") else getattr(d, "kind", "boundary")
        detail = d["detail"] if hasattr(d, "keys") else getattr(d, "detail", "")
        num = d["note_num"] if hasattr(d, "keys") else getattr(d, "note_num", None)
        out.append(Finding("boundary", UNRESOLVED, detail or kind, note_num=num))
    return out


def check_render_matches_selection(inp: IntegrityInput) -> list[Finding]:
    """The cell in the workbook is the cell those blocks produce.

    A human edit is allowed and is NOT a failure — it is recorded as diverged
    (`human_modified`) and shown as such. What fails is a cell whose text
    silently stopped matching its own lineage with nobody recording why.
    """
    out: list[Finding] = []
    for c in inp.cells:
        if not c.block_ids:
            continue
        if c.content_origin == "human_modified":
            out.append(Finding(
                "render_match", WARNING,
                f"{c.sheet} row {c.row} was edited by hand, so it no longer "
                "matches the source exactly",
                list(c.block_ids), c.note_num,
            ))
            continue
        if (
            c.rendered_sha256 and c.current_sha256
            and c.rendered_sha256 != c.current_sha256
        ):
            out.append(Finding(
                "render_match", UNRESOLVED,
                f"{c.sheet} row {c.row} no longer matches the source parts it "
                "was built from, and no edit was recorded",
                list(c.block_ids), c.note_num,
            ))
    return out


def check_character_cap(inp: IntegrityInput) -> list[Finding]:
    """Nothing was lost to the cell character limit.

    Per the Step 0.6 decision an oversized note is not rendered link-only; it
    is flagged here so it reaches a person rather than a truncated cell.
    """
    out: list[Finding] = []
    for c in inp.cells:
        if c.cap and c.rendered_chars > c.cap:
            out.append(Finding(
                "character_cap", UNRESOLVED,
                f"{c.sheet} row {c.row} needs {c.rendered_chars:,} characters "
                f"but the cell holds {c.cap:,}; it was left for the authoring "
                "path rather than cut short",
                list(c.block_ids), c.note_num,
            ))
    return out


def check_approved_duplicates(inp: IntegrityInput) -> list[Finding]:
    """One block used in two places is a duplication unless it was approved."""
    seen: dict[str, list[str]] = {}
    for c in inp.cells:
        for bid in c.block_ids:
            seen.setdefault(bid, []).append(f"{c.sheet}:{c.row}")
    out: list[Finding] = []
    for bid, places in seen.items():
        if len(places) > 1 and bid not in inp.approved_duplicate_block_ids:
            out.append(Finding(
                "approved_duplicate", UNRESOLVED,
                f"source part {bid} was used in {len(places)} places "
                f"({', '.join(places)}) without an approved routing",
                [bid],
            ))
    return out


def check_scout_agreement(inp: IntegrityInput) -> list[Finding]:
    """Absent scout data is unknown, not agreement. Say so rather than
    reporting a clean comparison nobody ran."""
    if not inp.scout_available and inp.notes:
        return [Finding(
            "scout_agreement", WARNING,
            "there is no scout inventory to compare the document reading "
            "against, so the note boundaries are unconfirmed",
        )]
    return []


CHECKS: tuple[Callable[[IntegrityInput], list[Finding]], ...] = (
    check_page_receipts,
    check_block_ownership,
    check_dispositions,
    check_prose_note_coverage,
    check_table_groups,
    check_note_continuity,
    check_boundaries,
    check_render_matches_selection,
    check_character_cap,
    check_approved_duplicates,
    check_scout_agreement,
)


def run_checks(inp: IntegrityInput) -> IntegrityResult:
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(inp))
    return IntegrityResult(findings=findings)


def missing_block_ids(result: IntegrityResult) -> list[str]:
    """The exact parts a targeted retry must fill (Step 7.2).

    Only the checks a retry could actually repair — asking an agent to re-do a
    note because its page count is short would burn a turn on something it
    cannot change.
    """
    repairable = {"disposition", "note_coverage", "table_group"}
    out: list[str] = []
    for f in result.findings:
        if f.blocking and f.check in repairable:
            out.extend(b for b in f.block_ids if b not in out)
    return out
