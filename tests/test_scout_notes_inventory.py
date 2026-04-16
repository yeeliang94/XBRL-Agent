"""Tests for scout notes-inventory extraction (A.2).

The notes inventory is a deterministic PDF-text pass that splits the notes
section into (note_num, title, page_range) entries. Matching content to
template rows is done by the LLM — this helper is navigation only.
"""
from __future__ import annotations

import pytest

from scout.notes_discoverer import (
    NoteInventoryEntry,
    extract_inventory_from_pages,
)


def test_inventory_entry_shape():
    e = NoteInventoryEntry(note_num=4, title="Property, plant and equipment", page_range=(20, 22))
    assert e.note_num == 4
    assert e.title == "Property, plant and equipment"
    assert e.page_range == (20, 22)


def test_extract_inventory_from_bare_numbered_headings():
    pages = [
        (20, "4. PROPERTY, PLANT AND EQUIPMENT\n\nThe Group...\n"),
        (21, "continued from page 20\n\n...depreciation policy applied...\n"),
        (22, "5. TRADE RECEIVABLES\n\n...aged analysis...\n"),
        (23, "continuation of 5\n\n...impairment...\n"),
        (24, "6. INVENTORIES\n\nraw materials and finished goods\n"),
    ]
    inv = extract_inventory_from_pages(pages)
    assert len(inv) == 3
    assert inv[0].note_num == 4
    assert "Property, plant and equipment".lower() in inv[0].title.lower()
    assert inv[0].page_range == (20, 21)
    assert inv[1].note_num == 5
    assert inv[1].page_range == (22, 23)
    assert inv[2].note_num == 6
    assert inv[2].page_range == (24, 24)


def test_extract_inventory_handles_note_prefix_style():
    pages = [
        (30, "NOTE 12 - REVENUE\n\nRevenue from contracts...\n"),
        (31, "NOTE 13 - FINANCE COSTS\n\nInterest expense...\n"),
    ]
    inv = extract_inventory_from_pages(pages)
    assert [(e.note_num, e.page_range) for e in inv] == [(12, (30, 30)), (13, (31, 31))]
    assert "revenue" in inv[0].title.lower()
    assert "finance costs" in inv[1].title.lower()


def test_extract_inventory_skips_continuation_pages_with_no_header():
    pages = [
        (40, "4. PROPERTY, PLANT AND EQUIPMENT\n\n"),
        (41, "..."),  # continuation — no header line
        (42, "5. INVESTMENTS\n\n"),
    ]
    inv = extract_inventory_from_pages(pages)
    assert inv[0].page_range == (40, 41)
    assert inv[1].page_range == (42, 42)


def test_extract_inventory_ignores_spurious_number_lines():
    # Things like "2.5" (a ratio) or "4.1(a)" (section) must not be misread
    # as note headers.
    pages = [
        (50, "Discussion of ratio 2.5 in the context of covenants.\n"),
        (51, "4. INVENTORIES\n\nRaw materials...\n"),
    ]
    inv = extract_inventory_from_pages(pages)
    assert len(inv) == 1
    assert inv[0].note_num == 4


def test_extract_inventory_returns_empty_when_no_notes_found():
    inv = extract_inventory_from_pages([(1, "Just some prose with no note headers.\n")])
    assert inv == []


def test_extract_inventory_closes_final_range_correctly():
    pages = [
        (100, "7. INTANGIBLE ASSETS\nsoftware licences"),
        (101, "no header — still note 7"),
        (102, "8. CASH AND BANK BALANCES\n"),
        (103, "also 8"),
        (104, "still 8"),
    ]
    inv = extract_inventory_from_pages(pages)
    assert inv[-1].note_num == 8
    assert inv[-1].page_range == (102, 104)


# ---------------------------------------------------------------------------
# Infopack round-trip including notes_inventory
# ---------------------------------------------------------------------------

def test_infopack_roundtrip_preserves_notes_inventory():
    from scout.infopack import Infopack, StatementPageRef
    from statement_types import StatementType

    original = Infopack(
        toc_page=3,
        page_offset=4,
        statements={
            StatementType.SOFP: StatementPageRef(
                variant_suggestion="CuNonCu", face_page=10, note_pages=[15],
                confidence="HIGH",
            ),
        },
        notes_inventory=[
            NoteInventoryEntry(4, "Property, plant and equipment", (20, 22)),
            NoteInventoryEntry(5, "Trade receivables", (23, 24)),
        ],
    )
    restored = Infopack.from_json(original.to_json())
    assert len(restored.notes_inventory) == 2
    assert restored.notes_inventory[0].note_num == 4
    assert restored.notes_inventory[0].title == "Property, plant and equipment"
    assert restored.notes_inventory[0].page_range == (20, 22)
    assert restored.notes_inventory[1].note_num == 5


def test_infopack_default_notes_inventory_is_empty_list():
    from scout.infopack import Infopack

    pack = Infopack(toc_page=1, page_offset=0)
    assert pack.notes_inventory == []
