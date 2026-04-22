"""Slice 6 — coverage warnings surface through the coordinator.

The sub-coordinator collects receipts and writes a side-log; the
coordinator must translate uncovered-note and skipped-with-reason
entries into human-readable warning lines that flow through the
existing SSE warning channel (and the History/UI).
"""
from __future__ import annotations

import pytest

from notes.coordinator import _build_write_warnings
from notes.coverage import CoverageEntry, CoverageReceipt
from notes.listofnotes_subcoordinator import (
    ListOfNotesSubResult,
    SubAgentRunResult,
)
from notes.payload import NotesPayload
from scout.notes_discoverer import NoteInventoryEntry


def _inv(note_num: int) -> NoteInventoryEntry:
    return NoteInventoryEntry(
        note_num=note_num, title=f"Note {note_num}", page_range=(20, 20),
    )


class _StubWriteResult:
    """The real NotesWriteResult shape is heavier than these tests need
    — mirror only the fields `_build_write_warnings` reads."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.fuzzy_matches: list[tuple[str, str, float]] = []


def _sub_result_with_skip(note_num: int, reason: str) -> SubAgentRunResult:
    receipt = CoverageReceipt(entries=[
        CoverageEntry(note_num=note_num, action="skipped", reason=reason),
    ])
    return SubAgentRunResult(
        sub_agent_id="sub0",
        batch=[_inv(note_num)],
        payloads=[],
        status="succeeded",
        coverage=receipt,
    )


def _sub_result_uncovered(note_nums: list[int]) -> SubAgentRunResult:
    """Sub-agent finished (succeeded or failed) with no submitted
    receipt. The batch notes are considered uncovered."""
    return SubAgentRunResult(
        sub_agent_id="sub0",
        batch=[_inv(n) for n in note_nums],
        payloads=[NotesPayload(
            chosen_row_label="Disclosure of X",
            content="body",
            evidence="p.20",
            source_pages=[20],
        )],
        status="succeeded",
        coverage=None,
    )


def test_skipped_entries_become_warning_lines():
    """Each skipped note in any sub-agent's receipt produces one
    'Note N skipped: <reason>' line so the user can eyeball whether
    the skip was legitimate (cross-sheet) or a missed disclosure."""
    sub_result = ListOfNotesSubResult(
        sub_agent_results=[
            _sub_result_with_skip(1, "Corporate information — Sheet 10"),
            _sub_result_with_skip(11, "Related party — Sheet 14"),
        ],
    )
    warnings = _build_write_warnings(_StubWriteResult(), sub_result)
    joined = " | ".join(warnings)
    assert "Note 1 skipped" in joined
    assert "Sheet 10" in joined
    assert "Note 11 skipped" in joined
    assert "Sheet 14" in joined


def test_uncovered_notes_become_warning_lines():
    """Sub-agents that didn't submit a receipt (iteration-cap-exhausted
    or otherwise crashed out of the terminal call) produce uncovered-
    note warnings — one line per missing note_num, so a 3-note batch
    that went uncovered shows up as three distinct warnings."""
    sub_result = ListOfNotesSubResult(
        sub_agent_results=[_sub_result_uncovered([4, 5, 6])],
    )
    warnings = _build_write_warnings(_StubWriteResult(), sub_result)
    joined = " | ".join(warnings)
    assert "Note 4" in joined and "uncovered" in joined.lower()
    assert "Note 5" in joined
    assert "Note 6" in joined


def test_clean_run_produces_no_coverage_warnings():
    """Fully-covered receipts with no skips must NOT emit coverage
    warnings — otherwise happy-path runs fill the UI with noise."""
    clean_receipt = CoverageReceipt(entries=[
        CoverageEntry(
            note_num=1, action="written", row_labels=["Disclosure of X"],
        ),
    ])
    sub_result = ListOfNotesSubResult(
        sub_agent_results=[
            SubAgentRunResult(
                sub_agent_id="sub0",
                batch=[_inv(1)],
                payloads=[],
                status="succeeded",
                coverage=clean_receipt,
            ),
        ],
    )
    warnings = _build_write_warnings(_StubWriteResult(), sub_result)
    joined = " | ".join(warnings)
    assert "skipped" not in joined
    assert "uncovered" not in joined.lower()


def test_coverage_warnings_coexist_with_existing_warnings():
    """Mix of existing writer/fuzzy warnings + new coverage warnings —
    both should land in the same list so the UI renders them together."""
    writer = _StubWriteResult()
    writer.fuzzy_matches = [
        ("Disclosure of something", "Disclosure of something else", 0.82),
    ]
    sub_result = ListOfNotesSubResult(
        sub_agent_results=[
            _sub_result_with_skip(1, "belongs elsewhere"),
        ],
    )
    warnings = _build_write_warnings(writer, sub_result)
    joined = " | ".join(warnings)
    # Existing fuzzy-match warning still present.
    assert "borderline fuzzy match" in joined
    # New skip warning also present.
    assert "Note 1 skipped" in joined
