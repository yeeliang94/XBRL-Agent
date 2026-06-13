"""Face-agent coverage receipts (PLAN-orchestration-hardening item 23).

The Sheet-12 fan-out's coverage receipts — every batch note accounted for as
written or skipped-with-reason, anything unaccounted becoming a loud warning —
are the best anti-silent-omission pattern in the codebase. Face extraction
agents have an equivalent expectation list sitting unused: the scout's
``face_line_refs`` (one ``FaceLineRef(label, note_num, section)`` per visible
face line).

This module is a focused, string-keyed sibling of ``notes/coverage.py`` (kept
separate rather than retrofitting the int-``note_num``-keyed notes machinery —
that would ripple through the notes tests for no gain). A face agent that
received non-empty ``face_line_refs`` submits a receipt accounting for each
scout-observed line as ``written`` or ``skipped`` (with a reason). A line left
unaccounted becomes an ``AgentResult.warnings`` entry — never a save block
(gotcha #13: hints are advisory; gotcha #17: an explicit "could not find" skip
is a valid receipt entry, the agent is never forced to plug).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_VALID_ACTIONS = frozenset({"written", "skipped"})


def _normalize_ref(s: str) -> str:
    """Lowercase + collapse whitespace so 'Trade receivables' and
    '*Trade Receivables ' compare equal (the agent may quote the label loosely)."""
    return " ".join((s or "").strip().lstrip("*").lower().split())


def expected_ref_label(ref: dict) -> str:
    """Human-readable display for one scout face-line ref: 'Label (Note N)'."""
    label = str(ref.get("label", "")).strip()
    note = ref.get("note_num")
    return f"{label} (Note {note})" if note else label


@dataclass
class FaceCoverageEntry:
    ref: str
    action: str  # "written" | "skipped"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.action not in _VALID_ACTIONS:
            raise ValueError(
                f"Unknown action {self.action!r} — must be one of "
                f"{sorted(_VALID_ACTIONS)}"
            )
        if self.action == "skipped" and not (self.reason and self.reason.strip()):
            raise ValueError(
                f"ref {self.ref!r}: 'skipped' entries must include a reason"
            )


@dataclass
class FaceCoverageReceipt:
    entries: list[FaceCoverageEntry] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: str) -> "FaceCoverageReceipt":
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(
                "Face coverage receipt must be a JSON list of entry objects "
                f"(got {type(data).__name__})"
            )
        entries: list[FaceCoverageEntry] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"Entry {i} is not an object")
            try:
                entries.append(FaceCoverageEntry(
                    ref=str(item["ref"]),
                    action=str(item["action"]),
                    reason=str(item.get("reason", "")),
                ))
            except KeyError as e:
                raise ValueError(f"Entry {i} missing required key: {e}")
        return cls(entries=entries)

    def validate(self, expected_refs: list[dict]) -> list[str]:
        """Structural errors only (empty = clean). An entry whose ref matches
        no scout-observed line is flagged so a typo'd receipt is visible; a
        missing expected line is NOT an error here — it surfaces as a warning
        (coverage is advisory, never a block)."""
        errors: list[str] = []
        expected_norm = {_normalize_ref(r.get("label", "")) for r in expected_refs}
        for e in self.entries:
            if _normalize_ref(e.ref) not in expected_norm:
                errors.append(
                    f"ref {e.ref!r} is not one of the scout-observed face "
                    f"lines — check the label or drop the entry."
                )
        return errors

    def accounted_refs(self) -> set[str]:
        """Normalised refs the agent accounted for (written OR skipped)."""
        return {_normalize_ref(e.ref) for e in self.entries}


def face_coverage_warnings(
    expected_refs: list[dict],
    receipt: "FaceCoverageReceipt | None",
) -> list[str]:
    """One warning per scout-observed face line the agent never accounted for.

    ``receipt`` is ``None`` when the agent finished without submitting one — in
    which case every expected ref is unaccounted. A line marked ``written`` or
    ``skipped`` (with reason) is accounted and produces no warning.
    """
    if not expected_refs:
        return []
    accounted = receipt.accounted_refs() if receipt is not None else set()
    warnings: list[str] = []
    for ref in expected_refs:
        if _normalize_ref(ref.get("label", "")) not in accounted:
            warnings.append(
                f"scout saw '{expected_ref_label(ref)}' on the face page — "
                f"agent did not account for it (write or skip-with-reason)."
            )
    return warnings
