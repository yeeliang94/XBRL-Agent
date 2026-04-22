"""Tests for scout notes-inventory extraction (A.2).

The notes inventory is a deterministic PDF-text pass that splits the notes
section into (note_num, title, page_range) entries. Matching content to
template rows is done by the LLM — this helper is navigation only.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from scout.notes_discoverer import (
    NoteInventoryEntry,
    build_notes_inventory,
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


def test_extract_inventory_matches_title_case_headings():
    # Review I3: the regex used to require ALL CAPS titles; many Malaysian
    # filings use Title Case ("4. Property, plant and equipment"). The
    # splitter should handle both.
    pages = [
        (60, "4. Property, plant and equipment\n\nThe Group...\n"),
        (61, "5. Trade receivables\n\nAged analysis...\n"),
    ]
    inv = extract_inventory_from_pages(pages)
    assert [(e.note_num, e.page_range) for e in inv] == [(4, (60, 60)), (5, (61, 61))]
    assert "property" in inv[0].title.lower()


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


# ---------------------------------------------------------------------------
# Vision fallback wiring (Plan Phase 3 Step 3.1)
# These exercise the public build_notes_inventory signature — the actual
# vision logic is tested in test_notes_discoverer_vision.py.
# ---------------------------------------------------------------------------


def _make_scanned_pdf(tmp_path: Path) -> Path:
    """Create a tiny image-only PDF for scanned-PDF behaviour tests.

    fitz.Document.insert_page creates a blank text layer, but calling
    get_text on such pages still returns '' — good enough to exercise
    the "PyMuPDF returned nothing, fall back" branch without shipping a
    real scanned binary into the repo.
    """
    import fitz

    out = tmp_path / "scanned.pdf"
    doc = fitz.open()
    for _ in range(5):
        doc.new_page(width=612, height=792)
    doc.save(str(out))
    doc.close()
    return out


def test_build_notes_inventory_empty_pdf_no_model_returns_empty(tmp_path):
    """Scanned PDF + no vision model → same behaviour as today: []."""
    pdf = _make_scanned_pdf(tmp_path)
    out = build_notes_inventory(str(pdf), notes_start_page=1)
    assert out == []


def test_build_notes_inventory_empty_pdf_with_model_calls_vision(tmp_path):
    """Scanned PDF + vision_model → vision path is invoked and its result returned.

    We patch the async orchestrator so we don't need a real LLM; the point
    is that wiring passes the kwarg through.
    """
    pdf = _make_scanned_pdf(tmp_path)

    captured: dict = {}

    async def fake_vision(*, pdf_path, start, end, model):
        captured.update(pdf_path=pdf_path, start=start, end=end, model=model)
        return [NoteInventoryEntry(note_num=1, title="fake", page_range=(1, 3))]

    with patch("scout.notes_discoverer_vision._vision_inventory", side_effect=fake_vision):
        out = build_notes_inventory(
            str(pdf), notes_start_page=1, vision_model=object(),
        )

    assert [(e.note_num, e.page_range) for e in out] == [(1, (1, 3))]
    assert captured["pdf_path"] == str(pdf)
    assert captured["start"] == 1
    assert captured["end"] == 5


def test_build_notes_inventory_text_pdf_skips_vision(tmp_path):
    """Text-based PDF: vision must NOT fire even when vision_model is set."""
    import fitz

    pdf = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 50), "4. Property, plant and equipment\n\nThe Group...")
    page2 = doc.new_page(width=612, height=792)
    page2.insert_text((50, 50), "5. Trade receivables\n\n...aged analysis...")
    doc.save(str(pdf))
    doc.close()

    vision_called = False

    async def _would_fail(*, pdf_path, start, end, model):
        nonlocal vision_called
        vision_called = True
        return []

    with patch("scout.notes_discoverer_vision._vision_inventory", side_effect=_would_fail):
        out = build_notes_inventory(
            str(pdf), notes_start_page=1, vision_model=object(),
        )

    assert vision_called is False
    assert {e.note_num for e in out} == {4, 5}


def test_build_notes_inventory_start_past_end_returns_empty(tmp_path, caplog):
    """HIGH-peer-review regression: a scout hint with notes_start_page past
    the last PDF page used to raise ValueError from `_chunk` inside the
    vision fallback. It must now short-circuit cleanly to [] with a
    warning log, because scout TOC offsets are occasionally wrong and
    "bad hint" should never be fatal.
    """
    pdf = _make_scanned_pdf(tmp_path)  # 5-page scan
    # vision_model is set (so the old code would have entered the
    # fallback); notes_start_page=50 is well past the 5-page end.
    with caplog.at_level("WARNING", logger="scout.notes_discoverer"):
        out = build_notes_inventory(
            str(pdf), notes_start_page=50, vision_model=object(),
        )
    assert out == []
    assert any(
        "exceeds effective end" in r.message for r in caplog.records
    ), f"expected out-of-bounds warning; got {[r.message for r in caplog.records]}"


def test_build_notes_inventory_notes_end_page_caps_vision_range(tmp_path):
    """MEDIUM-peer-review plumbing: passing notes_end_page narrows the
    vision fallback's scan range so the terminal note can't absorb
    post-notes pages like Directors' Statement.
    """
    pdf = _make_scanned_pdf(tmp_path)  # 5-page scan, PDF length = 5

    captured: dict = {}

    async def fake_vision(*, pdf_path, start, end, model):
        captured.update(start=start, end=end)
        return [NoteInventoryEntry(note_num=1, title="x", page_range=(1, 2))]

    with patch("scout.notes_discoverer_vision._vision_inventory", side_effect=fake_vision):
        build_notes_inventory(
            str(pdf), notes_start_page=1,
            notes_end_page=3,  # notes really end at page 3
            vision_model=object(),
        )
    # Without notes_end_page we would have scanned to pdf_length=5.
    assert captured["end"] == 3, (
        f"vision scan range end must be clamped to notes_end_page=3, got {captured['end']}"
    )


# ---------------------------------------------------------------------------
# force_vision — explicit "this is a scanned PDF" override
# ---------------------------------------------------------------------------


def test_build_notes_inventory_force_vision_skips_regex_on_text_pdf(tmp_path):
    """Operator explicitly marked PDF as scanned: regex must NOT run — the
    vision path runs unconditionally when vision_model is set."""
    import fitz

    pdf = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 50), "4. Property, plant and equipment\n\nThe Group...")
    doc.save(str(pdf))
    doc.close()

    captured: dict = {}

    async def fake_vision(*, pdf_path, start, end, model):
        captured["called"] = True
        return [NoteInventoryEntry(note_num=99, title="from-vision", page_range=(1, 1))]

    with patch("scout.notes_discoverer_vision._vision_inventory", side_effect=fake_vision):
        out = build_notes_inventory(
            str(pdf),
            notes_start_page=1,
            vision_model=object(),
            force_vision=True,
        )

    assert captured.get("called") is True
    assert [(e.note_num, e.title) for e in out] == [(99, "from-vision")]


def test_build_notes_inventory_force_vision_without_model_falls_back_to_regex(
    tmp_path, caplog,
):
    """force_vision=True but no vision_model → log a warning and use the
    regex path (today's behaviour). Skipping regex with nothing to replace
    it would silently return [] which defeats the point of the flag."""
    import fitz

    pdf = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 50), "4. Revenue\n\n...")
    doc.save(str(pdf))
    doc.close()

    with caplog.at_level("WARNING", logger="scout.notes_discoverer"):
        out = build_notes_inventory(
            str(pdf), notes_start_page=1, force_vision=True,  # no vision_model
        )

    assert [e.note_num for e in out] == [4]
    assert any("force_vision" in r.message for r in caplog.records), (
        f"expected a warning when force_vision has no model; got {[r.message for r in caplog.records]}"
    )


def test_build_notes_inventory_async_force_vision_skips_regex(tmp_path):
    """Async sibling honours force_vision the same way."""
    import fitz

    pdf = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 50), "4. Property, plant and equipment\n\n...")
    doc.save(str(pdf))
    doc.close()

    from scout.notes_discoverer import build_notes_inventory_async

    called = {"vision": False}

    async def fake_vision(*, pdf_path, start, end, model):
        called["vision"] = True
        return [NoteInventoryEntry(note_num=7, title="v", page_range=(1, 1))]

    async def _run():
        with patch("scout.notes_discoverer_vision._vision_inventory", side_effect=fake_vision):
            return await build_notes_inventory_async(
                str(pdf),
                notes_start_page=1,
                vision_model=object(),
                force_vision=True,
            )

    out = asyncio.run(_run())
    assert called["vision"] is True
    assert [e.note_num for e in out] == [7]
