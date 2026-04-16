"""NotesPayload — one write emitted by a notes agent for a single template row.

Every cell the notes writer lands in a workbook comes from exactly one
NotesPayload. Prose notes (Sheets 10, 11, 12) use the `content` field;
structured numeric notes (Sheets 13, 14) use `numeric_values`.

The `chosen_row_label` matches the template's col-A label (the writer does
fuzzy label resolution — see `notes/writer.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


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
