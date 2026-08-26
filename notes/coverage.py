"""Batch coverage receipts for Sheet-12 sub-agents.

Label-comparison normalization (peer-review S6):
  Receipt labels and sink labels are compared after the same
  normalization the writer uses (`_normalize` — strip leading `*`,
  lowercase). The agent often picks the template's raw label
  (`*Disclosure of X`) for the receipt while passing a plain
  `Disclosure of X` to write_notes; without normalization the validator
  would force a spurious retry. Coverage validates provenance only. It
  does not infer template-field identity from requested sink-label
  spellings because distinct fuzzy spellings can resolve to the same real
  row; the write guard owns that resolved-row invariant.

Per-note provenance (peer-review MEDIUM #1):
  `validate(written_row_labels=...)` accepts EITHER a flat `set[str]`
  (legacy/looser check) OR a `dict[note_num -> set[str]]` (preferred,
  catches cross-note attribution confusion). The dict form lets the
  validator assert each receipt entry's row_labels came from THAT
  note's own payloads — an agent claiming Note 2 wrote a row when
  only Note 1 wrote it is now a structural error.

Every Sheet-12 sub-agent is handed a batch of N notes from scout's
inventory. Before finishing, the sub-agent must submit a CoverageReceipt
— one entry per batch note, either:

- `"written"` with the single row label where the complete note landed, or
- `"skipped"` with a one-sentence reason (e.g. "belongs on Sheet 10",
  "no Sheet-12 row fits this disclosure").

The receipt closes the "silent skip" failure mode seen on a real run
where a sub-agent quietly omitted Note 5 with no trace. Forgetting a
note now produces a visible error the sub-agent can retry against, and
a legitimate skip becomes a yellow warning the user can act on.

Structural validation only — the receipt is not a content check. It
verifies the sub-agent is internally consistent (didn't claim to write
to a row it never landed, didn't forget a batch note, didn't invent a
note number) but cannot judge whether the content the agent DID write
is correct. That's the job of the (deferred) post-run validator.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Union

from notes.labels import normalize_label


def _normalize_label(s: str) -> str:
    """Mirror of `notes.writer._normalize` so the receipt and the
    writer agree on what "the same label" means. Both delegate to
    `notes.labels.normalize_label` so the two layers cannot drift
    apart — a change to the normalisation rules lands in one place
    and flows to both comparators.

    Why a thin wrapper instead of a direct import re-export: coverage.py
    calls this identifier heavily already; keeping the private name
    lets the implementation swap underneath without touching call sites."""
    return normalize_label(s)


# Closed set — any other action string is a typo and must be rejected
# so the agent's intent isn't silently mis-interpreted.
_VALID_ACTIONS = frozenset({"written", "skipped"})


@dataclass
class CoverageEntry:
    """One note's worth of coverage.

    The dataclass guards mutual-exclusion rules on construction so
    downstream consumers (the validator, the side-log writer) can trust
    the shape without re-checking. Breaking these guards is always a
    receipt-level bug, not a payload-level one — surface it early.
    """

    note_num: int
    action: str  # "written" | "skipped"
    row_labels: list[str] = field(default_factory=list)
    reason: str = ""

    def __post_init__(self) -> None:
        if self.action not in _VALID_ACTIONS:
            raise ValueError(
                f"Unknown action {self.action!r} — must be one of "
                f"{sorted(_VALID_ACTIONS)}"
            )
        if self.action == "written":
            if not self.row_labels:
                raise ValueError(
                    f"note {self.note_num}: 'written' entries must populate "
                    f"row_labels with at least one label"
                )
        else:  # skipped
            if not self.reason or not self.reason.strip():
                raise ValueError(
                    f"note {self.note_num}: 'skipped' entries must include "
                    f"a non-empty reason"
                )
            if self.row_labels:
                raise ValueError(
                    f"note {self.note_num}: 'skipped' entries must NOT list "
                    f"row_labels (row_labels only apply to 'written' entries)"
                )

    def to_dict(self) -> dict[str, Any]:
        """Stable JSON-friendly projection for the notes12_coverage.json
        side-log. Only the fields relevant to the action are emitted so
        the side-log stays tight and readable."""
        out: dict[str, Any] = {"note_num": self.note_num, "action": self.action}
        if self.action == "written":
            out["row_labels"] = list(self.row_labels)
        else:
            out["reason"] = self.reason
        return out


@dataclass
class CoverageReceipt:
    """Ordered collection of CoverageEntry plus a validator.

    Ordered (not set-like) because the agent submits entries in a
    meaningful sequence (usually the note-number order of its batch)
    and the side-log preserves that order for audit readability. The
    validator is order-independent.
    """

    entries: list[CoverageEntry] = field(default_factory=list)

    # -----------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------

    def validate(
        self,
        batch_note_nums: list[int],
        written_row_labels: Union[set[str], dict[int, set[str]]],
    ) -> list[str]:
        """Return a list of structural error messages (empty = valid).

        The validator accumulates all errors instead of failing on the
        first one: if the receipt has multiple issues, the agent should
        see them all at once so a retry can fix everything in a single
        turn instead of playing whack-a-mole across several turns.

        `batch_note_nums` — the note numbers the sub-agent was assigned.
        `written_row_labels` — labels the sub-agent landed in its
        payload_sink. Two accepted shapes:
        - `set[str]` (legacy): flat set of all labels. Looser check —
          confirms the label exists somewhere but cannot detect cross-
          note attribution confusion (e.g. receipt claims Note 2 wrote
          a row that only Note 1's payload landed).
        - `dict[note_num -> set[str]]` (preferred): per-note sink. The
          validator checks each "written" entry's row_labels are a
          subset of THAT note's own labels.

        Both shapes normalize labels (strip `*`, lowercase) on both
        sides — the writer does the same, so the two layers cannot
        disagree on what "matches" means (peer-review S6).
        """
        errors: list[str] = []
        batch_set = set(batch_note_nums)
        receipt_nums = [e.note_num for e in self.entries]

        seen: set[int] = set()
        for num in receipt_nums:
            if num in seen:
                errors.append(
                    f"Duplicate entry for note {num} — each batch note must "
                    f"appear exactly once."
                )
            seen.add(num)

        # Missing: in batch, not in receipt.
        for num in sorted(batch_set - seen):
            errors.append(
                f"Missing coverage for note {num} — every batch note must be "
                f"either 'written' or 'skipped'."
            )

        # Extra: in receipt, not in batch.
        for num in sorted(seen - batch_set):
            errors.append(
                f"Note {num} is not in your batch — remove this entry. Your "
                f"batch covers notes {sorted(batch_set)}."
            )

        # Build a per-note normalised sink for the row-label check.
        # Accept both shapes — the legacy flat set degrades to the old
        # behaviour ("label exists somewhere") for callers that haven't
        # migrated yet (or for single-sheet templates that legitimately
        # have no per-note structure).
        per_note_sink: dict[int, set[str]] = {}
        flat_normalized: set[str] = set()
        if isinstance(written_row_labels, dict):
            for n, labels in written_row_labels.items():
                per_note_sink[n] = {_normalize_label(s) for s in labels}
        else:
            flat_normalized = {_normalize_label(s) for s in written_row_labels}

        # Row-label consistency for 'written' entries.
        for entry in self.entries:
            if entry.action != "written":
                continue
            # Constrain the model-authored receipt itself. Do not count sink
            # strings here: the sink preserves requested label spellings, and
            # several fuzzy spellings may resolve to one template row. The
            # write guard enforces one-field routing against resolved rows.
            if len(entry.row_labels) != 1:
                errors.append(
                    f"Note {entry.note_num}: a complete top-level note must land "
                    f"in exactly one List-of-Notes field, but the receipt lists "
                    f"{len(entry.row_labels)} row labels. Choose one row from "
                    f"the note's top-level heading and put the full note there."
                )
            for label in entry.row_labels:
                normalized = _normalize_label(label)
                if per_note_sink:
                    note_labels = per_note_sink.get(entry.note_num, set())
                    if normalized not in note_labels:
                        errors.append(
                            f"Note {entry.note_num}: claims 'written' to "
                            f"'{label}' but no payload from this note was "
                            f"submitted with that label. (Note "
                            f"{entry.note_num}'s payloads landed: "
                            f"{sorted(note_labels) or '<none>'}.) "
                            f"Either write the row from this note first, "
                            f"or correct the receipt."
                        )
                else:
                    if normalized not in flat_normalized:
                        errors.append(
                            f"Note {entry.note_num}: claims 'written' to "
                            f"'{label}' but no payload with that label was "
                            f"submitted via write_notes. Either write the "
                            f"row first or correct the label in the receipt."
                        )
        return errors

    # -----------------------------------------------------------------
    # Serialisation
    # -----------------------------------------------------------------

    @classmethod
    def from_json(cls, raw: str) -> "CoverageReceipt":
        """Parse a JSON-encoded receipt from a persisted/legacy representation.

        The expected shape is a top-level list of entry objects —
        keeping the root a list rather than an envelope dict avoids
        bikeshedding over key names ("entries" vs "coverage" vs "notes")
        and makes the wire format obvious from inspection.
        """
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(
                "Coverage receipt must be a JSON list of entry objects "
                "(got {})".format(type(data).__name__)
            )
        entries: list[CoverageEntry] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(
                    f"Entry {i} is not an object (got {type(item).__name__})"
                )
            try:
                entries.append(CoverageEntry(
                    note_num=int(item["note_num"]),
                    action=str(item["action"]),
                    row_labels=[str(s) for s in item.get("row_labels", [])],
                    reason=str(item.get("reason", "")),
                ))
            except KeyError as e:
                raise ValueError(f"Entry {i} missing required key: {e}")
        return cls(entries=entries)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable snapshot for side-log persistence."""
        return {"entries": [e.to_dict() for e in self.entries]}


def parse_coverage_entries(raw: Any) -> tuple[CoverageReceipt, list[str]]:
    """Parse model-authored coverage without framework-level rejection."""
    if not isinstance(raw, list):
        return CoverageReceipt(), [
            f"entries must be a list of objects (got {type(raw).__name__})"
        ]
    entries: list[CoverageEntry] = []
    errors: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"entry {index} is not an object")
            continue
        try:
            note_num = item["note_num"]
            action = item["action"]
            if isinstance(note_num, bool) or not isinstance(note_num, int):
                raise ValueError("note_num must be an integer")
            if note_num <= 0:
                raise ValueError("note_num must be greater than zero")
            if not isinstance(action, str):
                raise ValueError("action must be a string")
            row_labels = item.get("row_labels", [])
            if not isinstance(row_labels, list) or not all(
                isinstance(label, str) for label in row_labels
            ):
                raise ValueError("row_labels must be a list of strings")
            reason = item.get("reason", "")
            if not isinstance(reason, str):
                raise ValueError("reason must be a string")
            entries.append(CoverageEntry(
                note_num=note_num,
                action=action,
                row_labels=list(row_labels),
                reason=reason,
            ))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"entry {index}: {exc}")
    return CoverageReceipt(entries=entries), errors
