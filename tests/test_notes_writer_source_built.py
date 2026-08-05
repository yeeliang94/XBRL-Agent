"""The `source_built` waiver must survive every writer rebuild.

A payload that `write_note_from_source` built from source blocks carries no
`parent_note` and no `evidence` — the rendered text is the document's own,
already committed with lineage, so `NotesPayload` waives both authoring
contracts via `source_built=True` (gotcha #31).

The writer rebuilds payloads in two places, and both used to drop that flag:

  - `_combine_payloads` — runs for EVERY row, so any source-built payload that
    missed the single-payload fast path failed its own constructor with
    "parent_note is required". The exception is raised inside
    `write_notes_workbook`, which runs inside the `write_notes` tool, so it
    took down the notes agent after the sub-agents had finished their work
    (reported against a Windows run, 2026-08-05).
  - `_inject_headings` — previously unreachable (it returns early when
    `parent_note is None`), but reachable once a combined cell can be both
    source-built and heading-bearing.

Both crash shapes are pinned here.
"""
from __future__ import annotations

import pytest

from notes.payload import NotesPayload
from notes.writer import _combine_payloads, _inject_headings


def _source_built(
    content: str = "<h3>5 Revenue</h3><p>Copied from the source.</p>",
    evidence: str = "",
    pages: list[int] | None = None,
) -> NotesPayload:
    """A payload shaped exactly as `_write_from_source_impl` builds one."""
    return NotesPayload(
        chosen_row_label="Other notes",
        content=content,
        evidence=evidence,
        source_pages=pages if pages is not None else [12],
        note_num=5,
        source_built=True,
    )


def _hand_authored(
    number: str = "6", title: str = "Trade receivables", page: int = 13,
) -> NotesPayload:
    return NotesPayload(
        chosen_row_label="Other notes",
        content=f"<p>Hand-authored note {number}.</p>",
        evidence=f"Page {page}, Note {number}",
        source_pages=[page],
        parent_note={"number": number, "title": title},
    )


def test_combine_carries_source_built_when_notes_share_a_row():
    """A catch-all row legitimately groups DIFFERENT notes (run-79). When one
    of them was built from source, the merge must stay source-built — it has
    no evidence or heading of its own to satisfy the contracts."""
    merged = _combine_payloads([_source_built(), _hand_authored()])

    assert merged.source_built is True
    assert "Copied from the source." in merged.content
    assert "Hand-authored note 6." in merged.content


def test_combine_carries_source_built_past_the_fast_path():
    """The single-payload fast path is skipped when evidence contains ';'.
    That alone used to be enough to crash a source-built write."""
    merged = _combine_payloads([
        _source_built(evidence="Page 12; Page 13"),
    ])

    assert merged.source_built is True
    assert merged.parent_note is None


def test_combine_takes_the_first_contributor_that_carries_a_heading():
    """`parent_note` used to come from `payloads[0]` unconditionally, so a
    source-built payload sorting first silently dropped a hand-authored
    contributor's heading. Page-sort order must not decide that."""
    source_first = _combine_payloads([_source_built(pages=[12]),
                                      _hand_authored(page=13)])
    hand_first = _combine_payloads([_hand_authored(page=10),
                                    _source_built(pages=[12])])

    assert source_first.parent_note == {"number": "6",
                                        "title": "Trade receivables"}
    assert hand_first.parent_note == {"number": "6",
                                      "title": "Trade receivables"}


def test_inject_headings_carries_source_built():
    """A source-built cell that picked up a heading from a deliberate-empty
    contributor has no evidence, so a rebuild that drops the flag raises
    "evidence is required" instead."""
    deliberate_empty = NotesPayload(
        chosen_row_label="Other notes",
        content="",
        evidence="",
        source_pages=[],
        parent_note={"number": "7", "title": "Deferred tax"},
    )
    combined = _combine_payloads([_source_built(), deliberate_empty])
    assert combined.source_built is True and combined.parent_note is not None

    injected = _inject_headings(combined)

    assert injected.source_built is True
    assert injected.content.startswith("<h3>7 Deferred tax</h3>")


def test_deliberate_empty_payload_does_not_strip_a_real_heading():
    """The same crash without any source-integrity involvement, and the more
    likely one on Sheet 12: a deliberate-empty "I looked and found nothing"
    payload carries no `parent_note` and no `source_pages`, so the page sort
    puts it FIRST. Taking `payloads[0].parent_note` then handed the merge a
    None heading for content that has one, and the constructor rejected it."""
    deliberate_empty = NotesPayload(
        chosen_row_label="Other notes",
        content="",
        evidence="",
        source_pages=[],
    )
    real = NotesPayload(
        chosen_row_label="Other notes",
        content="<p>Note 19 body.</p>",
        evidence="Page 41, Note 19",
        source_pages=[41],
        parent_note={"number": "19", "title": "Financial instruments"},
    )

    merged = _combine_payloads([deliberate_empty, real])

    assert merged.parent_note == {"number": "19",
                                  "title": "Financial instruments"}
    assert merged.source_built is False


def test_plain_payloads_still_reject_a_missing_heading():
    """The waiver must not leak: a merge of ordinary payloads is still held to
    the authoring contracts."""
    plain = NotesPayload(
        chosen_row_label="Other notes",
        content="<p>Body.</p>",
        evidence="Page 9",
        source_pages=[9],
        parent_note={"number": "3", "title": "Revenue"},
    )
    merged = _combine_payloads([plain, _hand_authored()])
    assert merged.source_built is False

    with pytest.raises(ValueError, match="parent_note is required"):
        NotesPayload(
            chosen_row_label="Other notes",
            content="<p>Body.</p>",
            evidence="Page 9",
            source_pages=[9],
        )
