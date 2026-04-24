"""Per-note provenance for coverage receipts (peer-review MEDIUM).

Pre-fix, `CoverageReceipt.validate` checked claimed `row_labels`
against a flat `set[str]` of all labels in the sink — so a receipt
could claim Note 2 wrote to row X when actually only Note 1 wrote to
X. The structural check missed cross-note attribution confusion.

Fix: NotesPayload carries `note_num` (sub-agent mode) and the validator
takes a `dict[note_num -> set[row_labels]]` so each receipt entry must
match the labels its OWN note actually wrote.

Also covers S6 (label normalization in validator) — the writer
normalizes labels for matching (`*` strip + lowercase) but the
receipt was case-sensitive, causing spurious retries when the agent
copied a `*Disclosure of X` label verbatim from the template.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from notes.coverage import CoverageEntry, CoverageReceipt
from notes.payload import NotesPayload


def _payload(label: str, note_num: int) -> NotesPayload:
    return NotesPayload(
        chosen_row_label=label,
        content="body",
        evidence="p.1",
        source_pages=[1],
        note_num=note_num,
        parent_note={"number": "1", "title": "Test Note"},
    )


# ---------------------------------------------------------------------------
# NotesPayload schema
# ---------------------------------------------------------------------------

def test_notes_payload_accepts_optional_note_num():
    """Sub-agent payloads carry note_num so the validator can build a
    per-note provenance map. Single-sheet templates (10/11/13/14)
    don't have a batch concept and pass note_num=None."""
    p = NotesPayload(
        chosen_row_label="X",
        content="body",
        evidence="p.1",
        source_pages=[1],
        note_num=5,
        parent_note={"number": "1", "title": "Test Note"},
    )
    assert p.note_num == 5


def test_notes_payload_note_num_defaults_to_none():
    """Backwards compat: existing single-sheet code paths construct
    payloads without note_num. Field must be optional."""
    p = NotesPayload(
        chosen_row_label="X", content="body", evidence="p.1", source_pages=[1],
        parent_note={"number": "1", "title": "Test Note"},
    )
    assert p.note_num is None


# ---------------------------------------------------------------------------
# CoverageReceipt.validate with per-note provenance
# ---------------------------------------------------------------------------

def test_validate_accepts_when_each_entry_matches_its_own_note_provenance():
    """Happy path: receipt's per-note labels match the per-note sink."""
    receipt = CoverageReceipt(entries=[
        CoverageEntry(note_num=1, action="written", row_labels=["A"]),
        CoverageEntry(note_num=2, action="written", row_labels=["B"]),
    ])
    errors = receipt.validate(
        batch_note_nums=[1, 2],
        written_row_labels={1: {"A"}, 2: {"B"}},
    )
    assert errors == []


def test_validate_rejects_cross_note_attribution(tmp_path: Path):
    """The MEDIUM-severity hole: receipt claims note 2 wrote row X
    but only note 1's payload had row X. Pre-fix this passed because
    the validator only checked label existence. Post-fix it must
    catch the false attribution."""
    receipt = CoverageReceipt(entries=[
        CoverageEntry(note_num=1, action="written", row_labels=["X"]),
        CoverageEntry(note_num=2, action="written", row_labels=["X"]),  # false
    ])
    errors = receipt.validate(
        batch_note_nums=[1, 2],
        written_row_labels={1: {"X"}, 2: set()},  # note 2 has no payloads
    )
    assert any("note 2" in e.lower() for e in errors)
    assert any("'x'" in e.lower() for e in errors)


def test_validate_normalizes_labels_for_comparison():
    """S6: writer normalizes labels (strip `*`, lowercase) for fuzzy
    matching. Receipt validator must match the same normalization so an
    agent that copies `*Disclosure of X` (template's raw label) into
    the receipt while passing `Disclosure of X` (its own version) to
    write_notes doesn't trigger spurious retries."""
    receipt = CoverageReceipt(entries=[
        CoverageEntry(
            note_num=1, action="written",
            row_labels=["*Disclosure of X"],  # template-style with `*`
        ),
    ])
    errors = receipt.validate(
        batch_note_nums=[1],
        written_row_labels={1: {"Disclosure of X"}},  # plain in sink
    )
    assert errors == [], errors


def test_validate_normalizes_case_when_comparing():
    receipt = CoverageReceipt(entries=[
        CoverageEntry(
            note_num=1, action="written", row_labels=["DISCLOSURE OF X"],
        ),
    ])
    errors = receipt.validate(
        batch_note_nums=[1],
        written_row_labels={1: {"Disclosure of X"}},
    )
    assert errors == []


def test_validate_skipped_entries_still_independent_of_provenance():
    """Skipped entries don't have row_labels — they're independent of
    per-note provenance. Validator must accept skip with no payloads
    in any note's slot."""
    receipt = CoverageReceipt(entries=[
        CoverageEntry(note_num=1, action="skipped", reason="off-sheet"),
    ])
    errors = receipt.validate(
        batch_note_nums=[1],
        written_row_labels={1: set()},
    )
    assert errors == []


def test_validate_multiple_row_labels_on_one_note():
    """Note 12 (financial risk management) splits across 3 rows. All
    three labels must come from note 12's own provenance."""
    receipt = CoverageReceipt(entries=[
        CoverageEntry(
            note_num=12, action="written",
            row_labels=["Disclosure of A", "Disclosure of B", "Disclosure of C"],
        ),
    ])
    errors = receipt.validate(
        batch_note_nums=[12],
        written_row_labels={
            12: {"Disclosure of A", "Disclosure of B", "Disclosure of C"},
        },
    )
    assert errors == []


def test_validate_legacy_set_signature_still_supported_for_backcompat():
    """During the transition some callers may still pass the old flat
    set. Validator should accept both shapes — flat set behaves like
    "label exists somewhere", which is the pre-fix behaviour for those
    callers (they get the looser check, not a TypeError)."""
    # Flat-set form (pre-fix call shape).
    receipt = CoverageReceipt(entries=[
        CoverageEntry(note_num=1, action="written", row_labels=["X"]),
    ])
    errors = receipt.validate(
        batch_note_nums=[1],
        written_row_labels={"X"},  # flat set, not dict
    )
    assert errors == []
