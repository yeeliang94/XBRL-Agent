"""Tests for scout Infopack data model — shape, serialisation, validation."""
from __future__ import annotations

import json
import pytest

from statement_types import StatementType
from scout.infopack import Infopack, StatementPageRef


class TestInfopackShape:
    """Step 3.1 RED: Infopack dataclass has the correct fields."""

    def test_statement_page_ref_fields(self):
        ref = StatementPageRef(
            variant_suggestion="CuNonCu",
            face_page=48,
            note_pages=[60, 61, 62],
            confidence="HIGH",
        )
        assert ref.variant_suggestion == "CuNonCu"
        assert ref.face_page == 48
        assert ref.note_pages == [60, 61, 62]
        assert ref.confidence == "HIGH"

    def test_infopack_construction(self):
        ref = StatementPageRef(
            variant_suggestion="CuNonCu",
            face_page=48,
            note_pages=[60, 61],
            confidence="HIGH",
        )
        pack = Infopack(
            toc_page=5,
            page_offset=6,
            statements={StatementType.SOFP: ref},
        )
        assert pack.toc_page == 5
        assert pack.page_offset == 6
        assert StatementType.SOFP in pack.statements
        assert pack.statements[StatementType.SOFP].face_page == 48

    def test_infopack_default_statements_empty(self):
        pack = Infopack(toc_page=3, page_offset=0)
        assert pack.statements == {}


class TestInfopackSerialisation:
    """Round-trip JSON serialisation."""

    def _make_infopack(self) -> Infopack:
        return Infopack(
            toc_page=5,
            page_offset=6,
            statements={
                StatementType.SOFP: StatementPageRef(
                    variant_suggestion="CuNonCu",
                    face_page=48,
                    note_pages=[60, 61],
                    confidence="HIGH",
                ),
                StatementType.SOPL: StatementPageRef(
                    variant_suggestion="Function",
                    face_page=50,
                    note_pages=[63],
                    confidence="MEDIUM",
                ),
            },
        )

    def test_to_json_returns_valid_json(self):
        pack = self._make_infopack()
        raw = pack.to_json()
        parsed = json.loads(raw)
        assert parsed["toc_page"] == 5
        assert "SOFP" in parsed["statements"]

    def test_round_trip(self):
        original = self._make_infopack()
        restored = Infopack.from_json(original.to_json())
        assert restored.toc_page == original.toc_page
        assert restored.page_offset == original.page_offset
        assert len(restored.statements) == len(original.statements)
        for st in original.statements:
            assert restored.statements[st].face_page == original.statements[st].face_page
            assert restored.statements[st].note_pages == original.statements[st].note_pages
            assert restored.statements[st].variant_suggestion == original.statements[st].variant_suggestion
            assert restored.statements[st].confidence == original.statements[st].confidence


class TestInfopackValidation:
    """Validation rules on construction."""

    def test_face_page_must_be_positive(self):
        with pytest.raises(ValueError, match="face_page"):
            StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=0,
                note_pages=[],
                confidence="HIGH",
            )

    def test_face_page_negative_rejected(self):
        with pytest.raises(ValueError, match="face_page"):
            StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=-1,
                note_pages=[],
                confidence="HIGH",
            )

    def test_note_pages_must_be_positive(self):
        with pytest.raises(ValueError, match="note_pages"):
            StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=5,
                note_pages=[0],
                confidence="HIGH",
            )

    def test_confidence_must_be_valid(self):
        with pytest.raises(ValueError, match="confidence"):
            StatementPageRef(
                variant_suggestion="CuNonCu",
                face_page=5,
                note_pages=[],
                confidence="INVALID",
            )

    def test_validate_page_range(self):
        """Note pages beyond pdf_length are caught by validate_page_range."""
        ref = StatementPageRef(
            variant_suggestion="CuNonCu",
            face_page=5,
            note_pages=[100],
            confidence="HIGH",
        )
        pack = Infopack(toc_page=1, page_offset=0, statements={StatementType.SOFP: ref})
        errors = pack.validate_page_range(pdf_length=50)
        assert len(errors) > 0
        assert "100" in errors[0]
