"""Phase 1b Step 14 — Sheet-12 fan-out MUST ignore subnotes.

This is the load-bearing invariant the peer review flagged. Sub-notes
must NEVER appear as peer entries in the iteration that drives Sheet-12
batching / coverage validation — otherwise Note "2" and Note "2.1" would
both be assigned to an agent for coverage, double-billing the agent and
breaking the receipt validator (which only knows about int note_nums).

The structural guarantee comes from putting subnotes inside the parent
NoteInventoryEntry (nested) rather than as peer entries. This test
pins:
- split_inventory_contiguous batches contain only top-level entries
- batch_note_nums (the per-agent assignment list) only contains
  top-level ints — never a "2.1" sub-ref
"""
from __future__ import annotations

from scout.notes_discoverer import NoteInventoryEntry, SubNoteInventoryEntry
from notes.listofnotes_subcoordinator import split_inventory_contiguous


def _inventory_with_subnotes() -> list[NoteInventoryEntry]:
    """Realistic-shape inventory: Note 2 has 14 sub-notes, others flat."""
    note2 = NoteInventoryEntry(
        note_num=2,
        title="Significant accounting policies",
        page_range=(15, 22),
        subnotes=[
            SubNoteInventoryEntry(f"2.{i}", f"Policy {i}", (15 + i // 2, 15 + i // 2))
            for i in range(1, 15)  # 2.1 through 2.14
        ],
    )
    return [
        note2,
        NoteInventoryEntry(note_num=3, title="Revenue", page_range=(23, 24)),
        NoteInventoryEntry(note_num=4, title="PPE", page_range=(45, 47)),
        NoteInventoryEntry(note_num=5, title="Receivables", page_range=(51, 51)),
    ]


def test_split_inventory_does_not_promote_subnotes():
    """Every batch entry must be a top-level NoteInventoryEntry — no
    sub-note ever becomes a peer."""
    inv = _inventory_with_subnotes()
    batches = split_inventory_contiguous(inv, n_batches=5)
    flat = [e for batch in batches for e in batch]
    # The total entry count across all batches equals the top-level
    # count (4), NOT the parent + sub total (4 + 14 = 18).
    assert len(flat) == 4
    # Every entry's note_num is one of {2, 3, 4, 5} — no "2.1" leakage
    note_nums = [e.note_num for e in flat]
    assert sorted(note_nums) == [2, 3, 4, 5]
    # Every entry is a NoteInventoryEntry, not a SubNoteInventoryEntry
    for e in flat:
        assert isinstance(e, NoteInventoryEntry)
        assert not isinstance(e, SubNoteInventoryEntry)


def test_batch_note_nums_only_contains_top_level_ints():
    """Reproduce the exact derivation used in listofnotes_subcoordinator.py:560.

    ``batch_note_nums = [entry.note_num for entry in batch]`` must
    produce a list[int] — no strings, no sub-refs. The receipt
    validator at notes/agent.py:868 assumes int note_nums; anything
    else would silently fail.
    """
    inv = _inventory_with_subnotes()
    batches = split_inventory_contiguous(inv, n_batches=5)
    for batch in batches:
        batch_note_nums = [entry.note_num for entry in batch]
        for nn in batch_note_nums:
            assert isinstance(nn, int)
            # And in the expected range — none of the sub-ref shapes
            # ("2.1") would even type-check as int.
            assert nn in {2, 3, 4, 5}


def test_subnotes_still_accessible_on_parent_entry():
    """Sanity: the subnotes haven't been thrown away — they're nested
    on the parent where the prompt renderer can find them."""
    inv = _inventory_with_subnotes()
    batches = split_inventory_contiguous(inv, n_batches=5)
    flat = [e for batch in batches for e in batch]
    note2 = next(e for e in flat if e.note_num == 2)
    refs = [s.subnote_ref for s in note2.subnotes]
    assert len(refs) == 14
    assert refs[0] == "2.1"
    assert refs[-1] == "2.14"
