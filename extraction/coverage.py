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
scout-observed line as ``written`` or ``skipped`` (with a reason). ``written``
must name a target label or cell that actually landed through ``write_facts``.
Canonical facts are clean; workbook-only writes with no concept mapping are
accepted with an explicit human-review warning.
Unaccounted or unbacked entries block a clean save; a scout hint remains
advisory because the agent can explicitly skip it with a reason after checking
the PDF. This prevents a balanced but source-incomplete statement from being
reported as complete without forcing a balancing plug.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_VALID_ACTIONS = frozenset({"written", "skipped"})


# A trailing note decoration on a submitted ref. The agent never sees the bare
# label alone: the prompt renders each line as "Label → Note 2" and the old
# failure feedback echoed "Label (Note 2)" — so both decorated spellings came
# back as refs on a live run (2026-08-05 SOFP), matched nothing, and all 15
# lines warned on a fully-filled statement. Both sides normalise through this,
# so any of the three spellings compare equal.
_TRAILING_NOTE_REF_RE = re.compile(
    r"\s*(?:\(\s*note\s+\d{1,3}[a-z]?\s*\)|(?:→|->)\s*note\s+\d{1,3}[a-z]?)\s*$",
    re.IGNORECASE,
)


def _normalize_ref(s: str) -> str:
    """Lowercase + collapse whitespace so 'Trade receivables' and
    '*Trade Receivables ' compare equal (the agent may quote the label loosely),
    then strip a trailing '(Note N)' / '→ Note N' decoration — the two forms
    the agent is shown and will plausibly quote back."""
    base = " ".join((s or "").strip().lstrip("*").lower().split())
    return _TRAILING_NOTE_REF_RE.sub("", base).strip()


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
    # Template field label supplied to ``write_facts``. Defaults to ``ref``
    # for the common one-to-one case; set it when the source line was rolled
    # into a differently named canonical field.
    target: str = ""

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

    def validate(
        self,
        expected_refs: list[dict],
        *,
        written_targets: set[str] | None = None,
        workbook_only_targets: set[str] | None = None,
    ) -> list[str]:
        """Structural errors only (empty = clean). An entry whose ref matches
        no scout-observed line is flagged so a typo'd receipt is visible; a
        missing expected line is NOT an error here — the caller reports it as
        unresolved coverage. When ``written_targets`` is supplied, every
        ``written`` entry must reconcile to a successful write target. A
        workbook-only target is accepted here because the caller reports that
        distinct, non-canonical outcome as a warning."""
        errors: list[str] = []
        expected_norm = {_normalize_ref(r.get("label", "")) for r in expected_refs}
        validate_written_targets = (
            written_targets is not None or workbook_only_targets is not None
        )
        landed_norm = {
            _normalize_ref(target) for target in (written_targets or set())
        }
        workbook_only_norm = {
            _normalize_ref(target) for target in (workbook_only_targets or set())
        }
        available_targets = sorted(
            {
                str(target).strip()
                for target in (
                    (written_targets or set()) | (workbook_only_targets or set())
                )
                if str(target).strip()
            },
            key=str.casefold,
        )
        for e in self.entries:
            if _normalize_ref(e.ref) not in expected_norm:
                errors.append(
                    f"ref {e.ref!r} is not one of the scout-observed face "
                    f"lines — check the label or drop the entry."
                )
                continue
            if (
                e.action == "written"
                and validate_written_targets
            ):
                target = e.target.strip() or e.ref
                target_norm = _normalize_ref(target)
                if (
                    target_norm not in landed_norm
                    and target_norm not in workbook_only_norm
                ):
                    choices = (
                        " Available successful targets: "
                        + "; ".join(repr(item) for item in available_targets)
                        + "."
                        if available_targets
                        else " No successful write targets were recorded."
                    )
                    errors.append(
                        f"written ref {e.ref!r} targets {target!r}, but no "
                        "workbook write or canonical fact was recorded for that "
                        "target in this agent run."
                        + choices
                        + " Correct the target or the write; mark the source "
                        "line skipped only if it was not written."
                    )
        return errors

    def workbook_only_warnings(
        self,
        *,
        written_targets: set[str],
        workbook_only_targets: set[str],
    ) -> list[str]:
        """Warnings for honest writes that landed only in the scratch workbook."""
        canonical_norm = {_normalize_ref(target) for target in written_targets}
        workbook_only_norm = {
            _normalize_ref(target) for target in workbook_only_targets
        }
        warnings: list[str] = []
        for entry in self.entries:
            if entry.action != "written":
                continue
            target = entry.target.strip() or entry.ref
            target_norm = _normalize_ref(target)
            if target_norm in workbook_only_norm and target_norm not in canonical_norm:
                warnings.append(
                    f"written ref {entry.ref!r} target {target!r} landed in the "
                    "workbook but did not map to a canonical concept; it needs "
                    "human review."
                )
        return warnings

    def accounted_refs(self) -> set[str]:
        """Normalised refs the agent accounted for (written OR skipped)."""
        return {_normalize_ref(e.ref) for e in self.entries}


def parse_face_coverage_entries(
    raw: Any,
) -> tuple[FaceCoverageReceipt, list[str]]:
    """Parse model-authored receipt entries without rejecting valid siblings.

    Coverage is advisory. A malformed entry must be visible in the tool result,
    but it must not trigger framework-level argument retries or prevent the
    remaining receipt from being recorded.
    """
    if not isinstance(raw, list):
        return FaceCoverageReceipt(), [
            f"entries must be a list of objects (got {type(raw).__name__})"
        ]
    entries: list[FaceCoverageEntry] = []
    errors: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"entry {index} is not an object")
            continue
        try:
            ref = item["ref"]
            action = item["action"]
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("ref must be a non-empty string")
            if not isinstance(action, str):
                raise ValueError("action must be a string")
            reason = item.get("reason", "")
            if not isinstance(reason, str):
                raise ValueError("reason must be a string")
            target = item.get("target", "")
            if not isinstance(target, str):
                raise ValueError("target must be a string")
            entries.append(FaceCoverageEntry(
                ref=ref, action=action, reason=reason, target=target,
            ))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"entry {index}: {exc}")
    return FaceCoverageReceipt(entries=entries), errors


def unaccounted_labels(
    expected_refs: list[dict],
    receipt: "FaceCoverageReceipt | None",
) -> list[str]:
    """The BARE labels still unaccounted — the exact spellings the tool accepts.

    This feeds the tool's failure reply. The old reply echoed the display
    sentence ("scout saw 'Label (Note 2)' on the face page"), which taught the
    agent a decorated spelling; its corrected resubmission then matched nothing
    either, so the retry loop could never converge (2026-08-05 SOFP run: two
    attempts, 15/15 still warned). Feedback must hand back the accepted input,
    not the human-facing report.
    """
    accounted = receipt.accounted_refs() if receipt is not None else set()
    return [
        str(r.get("label", "")).strip()
        for r in expected_refs
        if _normalize_ref(r.get("label", "")) not in accounted
    ]


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
