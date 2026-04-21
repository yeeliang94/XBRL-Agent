"""Phase 6.2 — pin the new `suggested_row_label` field on
`NoteInventoryEntry`.

The field is OPTIONAL and defaults to None. This pass only adds the
plumbing — no deterministic heuristic populates it yet. These tests
lock down the schema so callers can rely on the attribute being
present (even if None) and future phases that populate it don't need
to touch the dataclass again.
"""
from __future__ import annotations

from scout.notes_discoverer import NoteInventoryEntry


def test_suggested_row_label_defaults_to_none():
    """Existing constructors that don't pass the new field must still
    work (backwards compatibility) and the attribute must be None."""
    entry = NoteInventoryEntry(note_num=4, title="Revenue", page_range=(28, 30))
    assert entry.suggested_row_label is None


def test_suggested_row_label_can_be_set():
    """The future heuristic will populate this field; today we just
    pin that it round-trips cleanly when set."""
    entry = NoteInventoryEntry(
        note_num=6,
        title="Cash and bank balances",
        page_range=(32, 32),
        suggested_row_label="Disclosure of cash and cash equivalents",
    )
    assert entry.suggested_row_label == "Disclosure of cash and cash equivalents"


def test_suggested_row_label_accepts_none_explicitly():
    """Explicit None must be accepted (no eager validation that strips
    the field or coerces empty string — the payload layer doesn't want
    to see surprising coercions in logs)."""
    entry = NoteInventoryEntry(
        note_num=1, title="Corporate info", page_range=(18, 18),
        suggested_row_label=None,
    )
    assert entry.suggested_row_label is None
