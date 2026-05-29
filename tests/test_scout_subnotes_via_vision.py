"""Phase 1b Step 11 — vision discoverer carries sub-notes through.

Tests at the merger level (`_merge_and_stitch`) rather than spinning up a
full PydanticAI vision agent: the agent code itself is wired through
PydanticAI, and the merger is the deterministic boundary where vision
sub-notes turn into NoteInventoryEntry.subnotes. The system-prompt
contract (asking for subnotes) is asserted by string-presence.
"""
from __future__ import annotations

from scout.notes_discoverer import NoteInventoryEntry, SubNoteInventoryEntry
from scout.notes_discoverer_vision import (
    _VisionBatch,
    _VisionNote,
    _VisionSubNote,
    _VISION_SYSTEM_PROMPT,
    _merge_and_stitch,
)


def test_subnotes_carry_through_merger():
    batch = _VisionBatch(entries=[
        _VisionNote(
            note_num=2,
            title="Significant accounting policies",
            first_page=15,
            last_page=22,
            subnotes=[
                _VisionSubNote(
                    subnote_ref="2.1", title="Basis of preparation",
                    first_page=15,
                ),
                _VisionSubNote(
                    subnote_ref="2.14", title="Employee benefits",
                    first_page=20,
                ),
            ],
        ),
        _VisionNote(
            note_num=3,
            title="Revenue",
            first_page=23,
            last_page=24,
        ),
    ])
    out = _merge_and_stitch([batch], notes_end=30)
    assert len(out) == 2
    note2 = out[0]
    assert isinstance(note2, NoteInventoryEntry)
    assert len(note2.subnotes) == 2
    assert {s.subnote_ref for s in note2.subnotes} == {"2.1", "2.14"}
    # Sub-note page_range is a single-page span at first_page (the
    # vision schema doesn't try to estimate end pages for sub-notes).
    by_ref = {s.subnote_ref: s for s in note2.subnotes}
    assert by_ref["2.1"].page_range == (15, 15)
    assert by_ref["2.14"].page_range == (20, 20)

    # Sibling without subnotes still loads cleanly
    note3 = out[1]
    assert note3.subnotes == []


def test_subnotes_unioned_across_overlapping_batches():
    # Two batches both contain Note 2 (overlap region) but each saw a
    # different subset of its sub-notes. The merger must union them by
    # subnote_ref, keeping the earliest first_page when both saw the
    # same ref.
    batch_a = _VisionBatch(entries=[
        _VisionNote(
            note_num=2, title="Significant accounting policies",
            first_page=15, last_page=18,
            subnotes=[
                _VisionSubNote(subnote_ref="2.1", title="Basis", first_page=15),
                _VisionSubNote(subnote_ref="2.2", title="FX", first_page=17),
            ],
        ),
    ])
    batch_b = _VisionBatch(entries=[
        _VisionNote(
            note_num=2, title="Significant accounting policies",
            first_page=15, last_page=22,
            subnotes=[
                _VisionSubNote(subnote_ref="2.2", title="FX", first_page=17),
                _VisionSubNote(subnote_ref="2.14", title="Benefits", first_page=20),
            ],
        ),
    ])
    out = _merge_and_stitch([batch_a, batch_b], notes_end=30)
    assert len(out) == 1
    refs = sorted(s.subnote_ref for s in out[0].subnotes)
    assert refs == ["2.1", "2.14", "2.2"]


def test_vision_prompt_requests_subnotes():
    # The system prompt must actually ask for sub-notes — otherwise the
    # LLM will emit empty subnotes lists and the schema change is a no-op.
    assert "Sub-notes" in _VISION_SYSTEM_PROMPT
    assert "subnotes" in _VISION_SYSTEM_PROMPT
    # The marker-preservation rule is the load-bearing one — it's why
    # we kept subnote_ref as a str.
    assert "do NOT normalise" in _VISION_SYSTEM_PROMPT


def test_no_subnotes_means_empty_list_not_none():
    batch = _VisionBatch(entries=[
        _VisionNote(
            note_num=4, title="PPE", first_page=45, last_page=47,
            # No subnotes argument supplied — defaults to []
        ),
    ])
    out = _merge_and_stitch([batch], notes_end=50)
    assert out[0].subnotes == []
