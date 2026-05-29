"""Phase 1a Step 1 — schema tests for FaceLineRef + face_read_in_detail.

Locks the dataclass shape, the post-init validation, and the JSON round-trip
through Infopack.to_json / from_json. Backward compatibility is asserted by
loading a pre-Phase-1a JSON payload (no face_line_refs / face_read_in_detail
keys) and confirming the loader falls back to empty list / False.
"""
from __future__ import annotations

import json

import pytest

from statement_types import StatementType
from scout.infopack import FaceLineRef, Infopack, StatementPageRef


class TestFaceLineRefShape:
    def test_constructs_with_all_fields(self):
        ref = FaceLineRef(
            label="Property, plant and equipment",
            note_num=4,
            section="non-current assets",
        )
        assert ref.label == "Property, plant and equipment"
        assert ref.note_num == 4
        assert ref.section == "non-current assets"

    def test_constructs_with_only_label(self):
        # Lines without a cited note (e.g. "Total non-current assets") are
        # still observable structure — agents may use them for section
        # context even when there's no note pointer.
        ref = FaceLineRef(label="Total non-current assets")
        assert ref.note_num is None
        assert ref.section is None

    def test_empty_label_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            FaceLineRef(label="")

    def test_whitespace_label_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            FaceLineRef(label="   ")

    def test_zero_note_num_rejected(self):
        with pytest.raises(ValueError, match=">= 1"):
            FaceLineRef(label="X", note_num=0)


class TestStatementPageRefExtensions:
    def test_defaults_are_empty(self):
        # Backward-compat: existing callers don't pass the new fields.
        ref = StatementPageRef(
            variant_suggestion="CuNonCu",
            face_page=5,
        )
        assert ref.face_line_refs == []
        assert ref.face_read_in_detail is False

    def test_carries_face_line_refs(self):
        ref = StatementPageRef(
            variant_suggestion="CuNonCu",
            face_page=5,
            face_line_refs=[
                FaceLineRef(label="PPE", note_num=4),
                FaceLineRef(label="Trade receivables", note_num=7,
                            section="current assets"),
            ],
            face_read_in_detail=True,
        )
        assert len(ref.face_line_refs) == 2
        assert ref.face_line_refs[0].note_num == 4
        assert ref.face_read_in_detail is True


class TestInfopackSerdeRoundTrip:
    def _make(self) -> Infopack:
        return Infopack(
            toc_page=2,
            page_offset=4,
            statements={
                StatementType.SOFP: StatementPageRef(
                    variant_suggestion="CuNonCu",
                    face_page=5,
                    note_pages=[10, 11],
                    confidence="HIGH",
                    face_line_refs=[
                        FaceLineRef(
                            label="Property, plant and equipment",
                            note_num=4,
                            section="non-current assets",
                        ),
                        FaceLineRef(label="Cash and bank balances", note_num=7),
                    ],
                    face_read_in_detail=True,
                ),
            },
        )

    def test_round_trip_preserves_face_line_refs(self):
        original = self._make()
        restored = Infopack.from_json(original.to_json())
        assert StatementType.SOFP in restored.statements
        ref = restored.statements[StatementType.SOFP]
        assert ref.face_read_in_detail is True
        assert len(ref.face_line_refs) == 2
        assert ref.face_line_refs[0].label == "Property, plant and equipment"
        assert ref.face_line_refs[0].note_num == 4
        assert ref.face_line_refs[0].section == "non-current assets"
        assert ref.face_line_refs[1].note_num == 7
        assert ref.face_line_refs[1].section is None

    def test_legacy_payload_without_new_fields_loads(self):
        # Simulate a pre-Phase-1a infopack stored from an older run — the
        # loader must default the new fields without erroring.
        legacy = {
            "toc_page": 2,
            "page_offset": 4,
            "detected_standard": "unknown",
            "statements": {
                "SOFP": {
                    "variant_suggestion": "CuNonCu",
                    "face_page": 5,
                    "note_pages": [10, 11],
                    "confidence": "HIGH",
                },
            },
            "notes_inventory": [],
        }
        restored = Infopack.from_json(json.dumps(legacy))
        ref = restored.statements[StatementType.SOFP]
        assert ref.face_line_refs == []
        assert ref.face_read_in_detail is False

    def test_malformed_face_line_refs_skipped_not_fatal(self):
        # An entry with an empty label or a non-dict value should be
        # dropped silently — never abort the whole infopack load.
        payload = {
            "toc_page": 2,
            "page_offset": 0,
            "detected_standard": "unknown",
            "statements": {
                "SOFP": {
                    "variant_suggestion": "CuNonCu",
                    "face_page": 5,
                    "note_pages": [],
                    "confidence": "HIGH",
                    "face_line_refs": [
                        {"label": "OK", "note_num": 4},
                        {"label": "", "note_num": 5},        # empty label
                        "not a dict",                         # bad shape
                        {"label": "Also OK", "note_num": "not-an-int"},
                    ],
                    "face_read_in_detail": True,
                },
            },
            "notes_inventory": [],
        }
        restored = Infopack.from_json(json.dumps(payload))
        ref = restored.statements[StatementType.SOFP]
        labels = [r.label for r in ref.face_line_refs]
        assert labels == ["OK", "Also OK"]
        # Non-integer note_num gets nulled out rather than dropping the row
        assert ref.face_line_refs[1].note_num is None
