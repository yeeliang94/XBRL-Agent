"""Phase 1b Step 10 — regex sub-note detection on the text-PDF path.

Synthesises a notes section with realistic Note 2 sub-numbering (2.1–2.14)
and asserts the parent Note 2 carries the right subnotes. Negative tests
confirm sub-numbers from other parents don't leak in, and notes without
sub-numbering carry an empty subnotes list.
"""
from __future__ import annotations

from scout.notes_discoverer import (
    NoteInventoryEntry,
    SubNoteInventoryEntry,
    extract_inventory_from_pages,
    _detect_subnotes_for_parent,
)


def test_numeric_subnotes_under_parent_two():
    page = (
        "2. SIGNIFICANT ACCOUNTING POLICIES\n"
        "\n"
        "2.1 Basis of preparation\n"
        "The financial statements are prepared in accordance with...\n"
        "\n"
        "2.2 Foreign currency translation\n"
        "Items included in the financial statements...\n"
        "\n"
        "2.14 Employee benefits\n"
        "Short-term employee benefits...\n"
    )
    inv = extract_inventory_from_pages([(15, page)])
    assert len(inv) == 1
    note2 = inv[0]
    assert note2.note_num == 2
    refs = [s.subnote_ref for s in note2.subnotes]
    assert refs == ["2.1", "2.2", "2.14"]
    titles = [s.title for s in note2.subnotes]
    assert "Basis of preparation" in titles[0]
    assert "Foreign currency translation" in titles[1]
    assert "Employee benefits" in titles[2]


def test_alpha_subnotes_under_active_parent():
    # Auditors often nest (a) / (b) sub-sections under a numbered note
    # without a numeric ref of their own. Attached to the active parent.
    page = (
        "18. FINANCE COSTS\n"
        "\n"
        "(a) Interest on term loans\n"
        "Interest expense recognised...\n"
        "\n"
        "(b) Interest on lease liabilities\n"
        "Unwinding of lease discount...\n"
    )
    inv = extract_inventory_from_pages([(35, page)])
    refs = [s.subnote_ref for s in inv[0].subnotes]
    assert refs == ["(a)", "(b)"]


def test_subnotes_from_wrong_parent_filtered():
    # A page that mentions "3.1" while Note 2 is the active parent
    # must NOT attach 3.1 to Note 2.
    page = (
        "2. SIGNIFICANT ACCOUNTING POLICIES\n"
        "\n"
        "2.1 Basis of preparation\n"
        "Text\n"
        "\n"
        "3.1 SUB OF NOTE 3 BUT WE'RE STILL IN NOTE 2\n"
        "Text\n"
    )
    refs = _detect_subnotes_for_parent(page, parent_num=2, page_num=15)
    refs_only = [s.subnote_ref for s in refs]
    assert "2.1" in refs_only
    assert "3.1" not in refs_only


def test_subnotes_span_continuation_pages():
    # Note 2 spans pages 15–17; sub-notes appear on multiple pages.
    pages = [
        (15, "2. SIGNIFICANT ACCOUNTING POLICIES\n\n2.1 Basis of preparation\nText\n"),
        (16, "Continuation of Note 2 text.\n\n2.5 Property valuation\nText\n"),
        (17, "More continuation.\n\n2.14 Employee benefits\nText\n"),
    ]
    inv = extract_inventory_from_pages(pages)
    refs = [s.subnote_ref for s in inv[0].subnotes]
    assert refs == ["2.1", "2.5", "2.14"]
    # Page ranges per subnote reflect where each was detected
    pages_seen = {s.subnote_ref: s.page_range for s in inv[0].subnotes}
    assert pages_seen["2.1"] == (15, 15)
    assert pages_seen["2.5"] == (16, 16)
    assert pages_seen["2.14"] == (17, 17)


def test_note_without_subnumbering_has_empty_subnotes():
    # A simple disclosure note with no sub-headings — subnotes stays [].
    page = (
        "4. PROPERTY, PLANT AND EQUIPMENT\n"
        "\n"
        "Land and buildings carried at cost less depreciation. Movement\n"
        "during the year is presented in the following schedule.\n"
    )
    inv = extract_inventory_from_pages([(45, page)])
    assert inv[0].note_num == 4
    assert inv[0].subnotes == []


def test_multiple_top_level_notes_keep_their_own_subnotes():
    pages = [
        (15, "2. SIGNIFICANT ACCOUNTING POLICIES\n\n2.1 Basis of preparation\nText\n"),
        (16, "2.2 Foreign currency\nText\n"),
        (30, "5. INVESTMENTS IN SUBSIDIARIES\n\n5.1 Listing\nText\n"),
    ]
    inv = extract_inventory_from_pages(pages)
    assert len(inv) == 2
    note2_refs = [s.subnote_ref for s in inv[0].subnotes]
    note5_refs = [s.subnote_ref for s in inv[1].subnotes]
    assert note2_refs == ["2.1", "2.2"]
    assert note5_refs == ["5.1"]
