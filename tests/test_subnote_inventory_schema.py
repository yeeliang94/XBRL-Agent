"""Phase 1b Step 9 — subnote schema + Infopack serde.

Pins:
- SubNoteInventoryEntry validation
- NoteInventoryEntry.subnotes default + round-trip through Infopack JSON
- Backward compatibility with pre-Phase-1b payloads (no subnotes key)
- Malformed subnote entries dropped silently
"""
from __future__ import annotations

import json

import pytest

from scout.infopack import Infopack
from scout.notes_discoverer import NoteInventoryEntry, SubNoteInventoryEntry


class TestSubNoteShape:
    def test_constructs_with_numeric_ref(self):
        s = SubNoteInventoryEntry(
            subnote_ref="2.1",
            title="Basis of preparation",
            page_range=(15, 15),
        )
        assert s.subnote_ref == "2.1"
        assert s.page_range == (15, 15)

    def test_constructs_with_alpha_ref(self):
        # Real Malaysian filings use "(a)" / "(b)(i)" forms freely.
        s = SubNoteInventoryEntry(
            subnote_ref="(a)",
            title="Short term benefits",
            page_range=(20, 20),
        )
        assert s.subnote_ref == "(a)"

    def test_empty_ref_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            SubNoteInventoryEntry(
                subnote_ref="",
                title="x",
                page_range=(1, 1),
            )

    def test_bad_page_range_rejected(self):
        with pytest.raises(ValueError, match=">= 1"):
            SubNoteInventoryEntry(
                subnote_ref="2.1",
                title="x",
                page_range=(0, 5),
            )

    def test_non_tuple_page_range_rejected(self):
        with pytest.raises(ValueError, match="2-tuple"):
            SubNoteInventoryEntry(
                subnote_ref="2.1",
                title="x",
                page_range=[1, 5],  # list, not tuple
            )


class TestNoteInventoryWithSubnotes:
    def test_default_subnotes_empty(self):
        e = NoteInventoryEntry(
            note_num=4,
            title="Property, plant and equipment",
            page_range=(45, 47),
        )
        assert e.subnotes == []

    def test_holds_subnotes(self):
        e = NoteInventoryEntry(
            note_num=2,
            title="Significant accounting policies",
            page_range=(15, 22),
            subnotes=[
                SubNoteInventoryEntry("2.1", "Basis of preparation", (15, 15)),
                SubNoteInventoryEntry("2.14", "Employee benefits", (20, 20)),
            ],
        )
        assert len(e.subnotes) == 2
        assert e.subnotes[0].subnote_ref == "2.1"


class TestInfopackSerdeSubnotes:
    def _make(self) -> Infopack:
        return Infopack(
            toc_page=2,
            page_offset=0,
            statements={},
            notes_inventory=[
                NoteInventoryEntry(
                    note_num=2,
                    title="Significant accounting policies",
                    page_range=(15, 22),
                    subnotes=[
                        SubNoteInventoryEntry("2.1", "Basis", (15, 15)),
                        SubNoteInventoryEntry("(a)", "Sub-section a", (16, 16)),
                    ],
                ),
                NoteInventoryEntry(
                    note_num=4,
                    title="PPE",
                    page_range=(45, 47),
                ),
            ],
        )

    def test_round_trip_preserves_subnotes(self):
        original = self._make()
        restored = Infopack.from_json(original.to_json())
        assert len(restored.notes_inventory) == 2
        note2 = restored.notes_inventory[0]
        assert note2.note_num == 2
        assert len(note2.subnotes) == 2
        assert note2.subnotes[0].subnote_ref == "2.1"
        assert note2.subnotes[1].subnote_ref == "(a)"
        # Sibling without subnotes still loads cleanly
        note4 = restored.notes_inventory[1]
        assert note4.note_num == 4
        assert note4.subnotes == []

    def test_legacy_payload_without_subnotes_key_loads(self):
        legacy = {
            "toc_page": 2,
            "page_offset": 0,
            "detected_standard": "unknown",
            "statements": {},
            "notes_inventory": [
                {
                    "note_num": 4,
                    "title": "PPE",
                    "page_range": [45, 47],
                    # No subnotes key — pre-Phase-1b payload
                },
            ],
        }
        restored = Infopack.from_json(json.dumps(legacy))
        assert restored.notes_inventory[0].subnotes == []

    def test_malformed_subnote_entries_dropped(self):
        payload = {
            "toc_page": 2,
            "page_offset": 0,
            "detected_standard": "unknown",
            "statements": {},
            "notes_inventory": [
                {
                    "note_num": 2,
                    "title": "x",
                    "page_range": [10, 12],
                    "subnotes": [
                        {"subnote_ref": "2.1", "title": "ok",
                         "page_range": [10, 10]},     # OK
                        {"subnote_ref": "", "title": "x",
                         "page_range": [10, 10]},     # empty ref → drop
                        "not a dict",                   # bad shape → drop
                        {"subnote_ref": "2.2", "title": "x",
                         "page_range": [0, 1]},        # bad page → drop
                        {"subnote_ref": "2.3", "title": "x",
                         "page_range": "bad"},          # bad page → drop
                    ],
                },
            ],
        }
        restored = Infopack.from_json(json.dumps(payload))
        refs = [s.subnote_ref for s in restored.notes_inventory[0].subnotes]
        assert refs == ["2.1"]
