"""Phase 1a Step 6 — coordinator includes face_line_refs in page_hints.

Asserts the dict the coordinator hands to ``_run_single_agent`` carries the
new structural fields when scout populated them, and falls back to today's
two-key dict when the infopack is bare.
"""
from __future__ import annotations

from statement_types import StatementType
from scout.infopack import FaceLineRef, Infopack, StatementPageRef
from coordinator import build_face_page_hints


def _build_page_hints(infopack: Infopack, stmt_type: StatementType) -> dict:
    """Drive the REAL coordinator helper (peer-review F6).

    Previously this test duplicated the coordinator's dict construction, so it
    could pass while the runtime wiring drifted. It now calls the same
    `build_face_page_hints` the coordinator calls, so a change to the produced
    shape fails here too.
    """
    if stmt_type not in infopack.statements:
        return None
    return build_face_page_hints(infopack.statements[stmt_type])


def test_forwards_populated_face_line_refs():
    infopack = Infopack(
        toc_page=2,
        page_offset=0,
        statements={
            StatementType.SOFP: StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=5,
                note_pages=[10, 11],
                face_line_refs=[
                    FaceLineRef(
                        label="Property, plant and equipment",
                        note_num=4,
                        section="non-current assets",
                    ),
                ],
                face_read_in_detail=True,
            ),
        },
    )
    hints = _build_page_hints(infopack, StatementType.SOFP)
    assert hints["face_page"] == 5
    assert hints["face_read_in_detail"] is True
    assert len(hints["face_line_refs"]) == 1
    assert hints["face_line_refs"][0]["label"] == "Property, plant and equipment"
    assert hints["face_line_refs"][0]["note_num"] == 4
    assert hints["face_line_refs"][0]["section"] == "non-current assets"


def test_empty_face_line_refs_fall_back_cleanly():
    infopack = Infopack(
        toc_page=2,
        page_offset=0,
        statements={
            StatementType.SOFP: StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=5,
                note_pages=[10, 11],
                # No face_line_refs / face_read_in_detail explicitly set;
                # defaults are empty list / False.
            ),
        },
    )
    hints = _build_page_hints(infopack, StatementType.SOFP)
    # The new keys are still present (so the prompt renderer can branch
    # on them), but they signal "scout didn't enrich" — empty list /
    # False mean the bare hint block renders.
    assert hints["face_line_refs"] == []
    assert hints["face_read_in_detail"] is False


def test_face_line_notes_carry_their_inventory_pages_into_the_prompt():
    """Trace audit (2026-09-25): the statement-level note_pages was often the
    whole notes section, so agents read ~20 pages. Joining each face line's
    note number to the scout inventory tells the agent exactly where to look."""
    from prompts import _build_scoped_navigation
    from scout.notes_discoverer import NoteInventoryEntry

    ref = StatementPageRef(
        variant_suggestion="CuNonCu",
        face_page=8,
        note_pages=list(range(12, 32)),
        face_line_refs=[
            FaceLineRef(label="Inventories", note_num=4, section="current assets"),
            FaceLineRef(label="Trade receivables", note_num=7, section="current assets"),
            FaceLineRef(label="Total assets", note_num=None, section=None),
            FaceLineRef(label="Deferred tax", note_num=99, section=None),
            FaceLineRef(label="Provisions", note_num=12, section=None),
        ],
    )
    inventory = [
        NoteInventoryEntry(note_num=4, title="Inventories", page_range=(19, 19)),
        NoteInventoryEntry(note_num=7, title="Trade receivables", page_range=(20, 21)),
        # (0, 0) = location unknown (operator-added note): never a page hint.
        NoteInventoryEntry(note_num=12, title="Provisions", page_range=(0, 0)),
    ]

    hints = build_face_page_hints(ref, inventory)
    assert hints["referenced_note_pages"] == [19, 20, 21]

    nav = _build_scoped_navigation(hints)
    assert "Pages of the notes referenced on the face: [19, 20, 21]" in nav
    assert "Inventories → Note 4 (page 19)" in nav
    assert "Trade receivables → Note 7 (pages 20-21)" in nav
    # A note missing from the inventory, or with an unknown location, still
    # renders without invented pages; the wider note_pages stays the fallback.
    assert "Deferred tax → Note 99\n" in nav + "\n"
    assert "Provisions → Note 12\n" in nav + "\n"
    assert "page 0" not in nav
    assert "Wider note pages flagged by the scout" in nav
