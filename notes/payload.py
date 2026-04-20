"""NotesPayload — one write emitted by a notes agent for a single template row.

Every cell the notes writer lands in a workbook comes from exactly one
NotesPayload. Prose notes (Sheets 10, 11, 12) use the `content` field;
structured numeric notes (Sheets 13, 14) use `numeric_values`.

The `chosen_row_label` matches the template's col-A label (the writer does
fuzzy label resolution — see `notes/writer.py`).
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
