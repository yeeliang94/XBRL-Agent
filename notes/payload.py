"""NotesPayload — one write emitted by a notes agent for a single template row.

Every cell the notes writer lands in a workbook comes from exactly one
NotesPayload. Prose notes (Sheets 10, 11, 12) use the `content` field;
structured numeric notes (Sheets 13, 14) use `numeric_values`.

The `chosen_row_label` matches the template's col-A label (the writer does
fuzzy label resolution — see `notes/writer.py`).

Every non-empty payload also carries `parent_note` (and optionally
`sub_note`), which the writer uses to prepend one or two `<h3>` heading
lines to the cell so note numbering is consistent across the workbook.
The LLM supplies the note number + title as printed in the PDF; the
writer is responsible for the markup. See `notes/writer.py`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# Recognised keys for NotesPayload.numeric_values. Wrong keys used to fall
# through `dict.get` in the writer and land as silently-empty cells — so
# the payload now validates up front. `cy`/`py` are accepted as company
# aliases for single-filing templates.
_NUMERIC_KEYS = frozenset({
    "group_cy", "group_py", "company_cy", "company_py", "cy", "py",
})


def _validate_heading(field_name: str, heading: Optional[dict]) -> None:
    """Enforce that a heading dict has non-empty `number` and `title`.

    A missing heading is fine (caller decides whether it's required). A
    present heading must have both fields populated so the writer can
    render `<h3>{number} {title}</h3>` cleanly — an empty number would
    render as a leading-space bug; an empty title as a trailing-space bug.
    """
    if heading is None:
        return
    if not isinstance(heading, dict):
        raise ValueError(
            f"{field_name} must be a dict with keys 'number' and 'title'"
        )
    number = heading.get("number")
    title = heading.get("title")
    if not isinstance(number, str) or not number.strip():
        raise ValueError(f"{field_name}['number'] must be a non-empty string")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"{field_name}['title'] must be a non-empty string")


def _coerce_numeric(key: str, value: object) -> float:
    """Return a finite float, or raise ValueError describing the failure.

    Strings, bools, NaN, and +/-inf are rejected so malformed model output
    can't land as literal text or error values in the Issued Capital /
    Related Party movement tables (whose totals depend on numeric cells).
    """
    # bool is a subclass of int — reject it first so True/False don't slip
    # through the numeric check.
    if isinstance(value, bool):
        raise ValueError(f"numeric_values[{key!r}] is a bool, expected number")
    if not isinstance(value, (int, float)):
        raise ValueError(
            f"numeric_values[{key!r}] is {type(value).__name__} "
            f"({value!r}), expected int or float"
        )
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        raise ValueError(
            f"numeric_values[{key!r}] is non-finite ({value!r}); "
            f"Excel would render it as #NUM!"
        )
    return f


@dataclass
class NotesPayload:
    """One row's worth of notes content, ready for the writer."""

    chosen_row_label: str
    content: str
    evidence: str
    source_pages: list[int] = field(default_factory=list)
    # For Sheet-12 sub-coordinator tracing — which sub-agent produced this.
    sub_agent_id: Optional[str] = None
    # Sheets 13/14 carry structured numeric values keyed by column role
    # (group_cy / group_py / company_cy / company_py). None for prose rows.
    numeric_values: Optional[dict[str, float]] = None
    # Sheet-12 sub-agent mode only: the batch note number this payload
    # came from. Powers the per-note provenance check in
    # CoverageReceipt.validate (peer-review MEDIUM #1) — without it the
    # validator could only assert "this label appears somewhere in the
    # sink", letting a receipt falsely claim Note 2 wrote to a row
    # actually written by Note 1. None for single-sheet templates
    # (10/11/13/14) which don't have a batch concept.
    note_num: Optional[int] = None
    # Phase 4.1: PDF note references this payload's content is drawn from
    # (e.g. `["5"]`, `["5.1"]`, or `["5", "5.1", "5.2"]` when a single
    # cell groups a parent note with its sub-notes). Optional — agents
    # populate it during extraction, and the Phase 5 post-validator uses
    # it as the primary dedup signal across Sheets 11 and 12. Empty list
    # = the agent couldn't identify a note number (e.g. policy paragraphs
    # without numbering).
    source_note_refs: list[str] = field(default_factory=list)
    # Note heading hierarchy for the writer's <h3> prepend step.
    #   parent_note = {"number": "5", "title": "Material Accounting Policies"}
    #   sub_note    = {"number": "5.4", "title": "Property, Plant and Equipment"}
    # parent_note is required on any payload with content or numeric_values;
    # sub_note is optional (only present when the cell covers a sub-note).
    # The LLM supplies the numbers/titles verbatim from the PDF; the writer
    # emits `<h3>{number} {title}</h3>` lines. Keeping the markup in the
    # writer (not the model) is what makes headings impossible to drift.
    parent_note: Optional[dict] = None
    sub_note: Optional[dict] = None

    def __post_init__(self) -> None:
        if not self.chosen_row_label or not self.chosen_row_label.strip():
            raise ValueError("chosen_row_label must be non-empty")
        # Mandatory evidence contract (PLAN Section 2 #11). An empty content
        # row may skip the check — it's a deliberate "I looked and there's
        # nothing here" signal. Numeric-only rows are considered non-empty.
        has_payload = bool(self.content.strip()) or bool(self.numeric_values)
        if has_payload and not self.evidence.strip():
            raise ValueError(
                "evidence is required for any payload with content or "
                "numeric_values (PLAN Section 2 #11)"
            )
        # Heading-hierarchy validation. Mirrors the evidence gate: mandatory
        # on any non-empty payload, waived on deliberate-empty "I looked and
        # found nothing" payloads so the agent can still report coverage.
        if has_payload and self.parent_note is None:
            raise ValueError(
                "parent_note is required for any payload with content or "
                "numeric_values — the writer needs {number, title} to emit "
                "the <h3> heading line."
            )
        _validate_heading("parent_note", self.parent_note)
        _validate_heading("sub_note", self.sub_note)
        if self.numeric_values:
            bad = sorted(set(self.numeric_values) - _NUMERIC_KEYS)
            if bad:
                raise ValueError(
                    f"numeric_values contains unknown key(s) {bad} — "
                    f"accepted keys: {sorted(_NUMERIC_KEYS)}"
                )
            # Coerce-and-validate each value. Rebuilds the dict so downstream
            # consumers see canonical floats (not ints, not the raw input).
            self.numeric_values = {
                k: _coerce_numeric(k, v) for k, v in self.numeric_values.items()
            }
