"""PLAN-orchestration-hardening items 3+4+5 — scout save-time observability.

Item 3: infopack coercions warn loudly and land in the save-summary
telemetry (no silent coercions). Item 4: implausible note_num refs are
dropped with a warning. Item 5: the scanned-PDF case is an explicit signal
— in the read_face_structure tool return AND at save time — never a silent
empty list.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import fitz
import pytest
from pydantic import ValidationError

from scout.agent import (
    ScoutDeps,
    _read_face_structure_impl,
    _save_infopack_impl,
)
from scout.infopack import MAX_PLAUSIBLE_NOTE_NUM, Infopack
from scout.notes_discoverer import NoteInventoryEntry
from statement_types import StatementType


def _deps(tmp_path, pdf_length: int = 10) -> ScoutDeps:
    return ScoutDeps(
        pdf_path=tmp_path / "f.pdf", pdf_length=pdf_length,
        statements_to_find=None, on_progress=None,
    )


def _make_pdf(tmp_path, text: str | None) -> Path:
    """A 1-page PDF; ``text=None`` leaves the page image-only (scanned-like)."""
    path = tmp_path / "f.pdf"
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# Item 3 — loud warnings on coercions
# ---------------------------------------------------------------------------

class TestCoercionTelemetry:
    def test_invalid_scale_unit_coerced_warned_and_counted(self, tmp_path, caplog):
        deps = _deps(tmp_path)
        with caplog.at_level(logging.WARNING, logger="scout.agent"):
            msg = _save_infopack_impl(deps, json.dumps({
                "toc_page": 1, "page_offset": 0,
                "scale_unit": "thousands_of_millions",
            }))
        assert deps.infopack is not None
        # Coercion behaviour unchanged (graceful degradation, gotcha #13)...
        assert deps.infopack.scale_unit == "unknown"
        # ...but no longer silent: warned and surfaced in the telemetry msg.
        assert any(
            "invalid scale_unit" in r.getMessage() for r in caplog.records
        ), "coercion must log a warning"
        assert "scale_unit" in msg and "Coerced" in msg

    def test_invalid_consolidation_and_standard_counted(self, tmp_path, caplog):
        deps = _deps(tmp_path)
        with caplog.at_level(logging.WARNING, logger="scout.agent"):
            msg = _save_infopack_impl(deps, json.dumps({
                "toc_page": 1, "page_offset": 0,
                "consolidation_level": "consolidated-ish",
                "detected_standard": "ifrs",
            }))
        assert deps.infopack.consolidation_level == "unknown"
        assert deps.infopack.detected_standard == "unknown"
        assert "consolidation_level" in msg and "detected_standard" in msg

    def test_valid_fields_produce_no_coercion_noise(self, tmp_path):
        deps = _deps(tmp_path)
        msg = _save_infopack_impl(deps, json.dumps({
            "toc_page": 1, "page_offset": 0,
            "scale_unit": "thousands",
            "consolidation_level": "group",
            "detected_standard": "mfrs",
        }))
        assert "Coerced" not in msg
        assert deps.infopack.scale_unit == "thousands"


# ---------------------------------------------------------------------------
# Item 4 — note_num sanity bounds
# ---------------------------------------------------------------------------

def _save_with_ref(deps, note_num):
    return _save_infopack_impl(deps, json.dumps({
        "toc_page": 1, "page_offset": 0,
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu",
                "face_page": 2,
                "face_line_refs": [
                    {"label": "Trade receivables", "note_num": note_num},
                ],
                "face_read_in_detail": True,
            },
        },
    }))


class TestNoteNumBounds:
    def test_implausible_note_num_dropped_and_warned(self, tmp_path, caplog):
        deps = _deps(tmp_path)
        with caplog.at_level(logging.WARNING, logger="scout.agent"):
            msg = _save_with_ref(deps, 743)
        refs = deps.infopack.statements[StatementType.SOFP].face_line_refs
        assert refs == [], "note_num=743 must be dropped"
        assert any(
            "implausible" in r.getMessage() for r in caplog.records
        )
        assert "dropped" in msg

    def test_boundary_note_num_kept(self, tmp_path):
        deps = _deps(tmp_path)
        msg = _save_with_ref(deps, MAX_PLAUSIBLE_NOTE_NUM)
        refs = deps.infopack.statements[StatementType.SOFP].face_line_refs
        assert len(refs) == 1 and refs[0].note_num == MAX_PLAUSIBLE_NOTE_NUM
        assert "dropped" not in msg

    def test_inventory_tightens_the_bound(self, tmp_path):
        """With a deterministic inventory, the bound is max(note_num) + 5 —
        evidence-based and much tighter than the hard ceiling."""
        deps = _deps(tmp_path)
        deps.notes_inventory = [
            NoteInventoryEntry(note_num=30, title="Borrowings",
                               page_range=(5, 6)),
        ]
        _save_with_ref(deps, 40)  # > 30 + 5 → dropped
        assert deps.infopack.statements[StatementType.SOFP].face_line_refs == []

        deps2 = _deps(tmp_path)
        deps2.notes_inventory = list(deps.notes_inventory)
        _save_with_ref(deps2, 35)  # == 30 + 5 → kept
        assert len(
            deps2.infopack.statements[StatementType.SOFP].face_line_refs
        ) == 1

    def test_vision_schema_rejects_implausible_note_num(self):
        from scout.notes_discoverer_vision import _VisionNote

        with pytest.raises(ValidationError):
            _VisionNote(note_num=743, title="Hallucinated note",
                        first_page=1, last_page=1)
        # ge=1 contract (gotcha #13) untouched.
        with pytest.raises(ValidationError):
            _VisionNote(note_num=0, title="Zeroth note",
                        first_page=1, last_page=1)


# ---------------------------------------------------------------------------
# Item 5 — explicit scanned-PDF signal
# ---------------------------------------------------------------------------

class TestScannedPdfSignal:
    def test_empty_text_layer_returns_scanned_hint(self, tmp_path):
        pdf = _make_pdf(tmp_path, text=None)
        deps = _deps(tmp_path, pdf_length=1)
        result = _read_face_structure_impl(deps, "SOFP", 1)
        assert isinstance(result, dict)
        assert result["scanned_hint"] is True
        assert "populate face_line_refs" in result["message"]
        assert result["face_line_refs"] == []

    def test_text_page_with_no_refs_still_signals(self, tmp_path):
        pdf = _make_pdf(tmp_path, text="Just an ordinary narrative page.")
        deps = _deps(tmp_path, pdf_length=1)
        result = _read_face_structure_impl(deps, "SOFP", 1)
        assert isinstance(result, dict)
        assert result["scanned_hint"] is True
        assert "text layer" in result["message"]

    def test_save_warns_when_both_sources_empty(self, tmp_path, caplog):
        """Regex ran-and-empty + LLM didn't populate → loud warning naming
        the statement, plus a line in the save summary."""
        deps = _deps(tmp_path)
        deps.face_line_refs_by_statement[StatementType.SOFP] = []
        with caplog.at_level(logging.WARNING, logger="scout.agent"):
            msg = _save_infopack_impl(deps, json.dumps({
                "toc_page": 1, "page_offset": 0,
                "statements": {
                    "SOFP": {
                        "variant_suggestion": "CuNonCu",
                        "face_page": 2,
                        "face_line_refs": [],
                        "face_read_in_detail": True,
                    },
                },
            }))
        assert any(
            "face refs unavailable for SOFP" in r.getMessage()
            for r in caplog.records
        )
        assert "Face refs unavailable for: SOFP" in msg

    def test_no_warning_when_regex_never_ran(self, tmp_path, caplog):
        """A statement the regex never parsed (tool not called) is not a
        scanned-page signal — no spurious warning."""
        deps = _deps(tmp_path)
        with caplog.at_level(logging.WARNING, logger="scout.agent"):
            msg = _save_infopack_impl(deps, json.dumps({
                "toc_page": 1, "page_offset": 0,
                "statements": {
                    "SOFP": {
                        "variant_suggestion": "CuNonCu",
                        "face_page": 2,
                    },
                },
            }))
        assert "Face refs unavailable" not in msg


# ---------------------------------------------------------------------------
# Item 3 — from_json drop summary
# ---------------------------------------------------------------------------

def test_from_json_emits_drop_summary(caplog):
    raw = json.dumps({
        "toc_page": 1, "page_offset": 0,
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu",
                "face_page": 2,
                "face_line_refs": [
                    {"label": ""},               # dropped: empty label
                    "not-a-dict",                # dropped: not a dict
                    {"label": "Cash", "note_num": 5},  # kept
                ],
            },
        },
        "notes_inventory": [
            {"note_num": 1, "title": "x", "page_range": [1]},  # malformed
        ],
    })
    with caplog.at_level(logging.WARNING, logger="scout.infopack"):
        ip = Infopack.from_json(raw)
    assert len(ip.statements[StatementType.SOFP].face_line_refs) == 1
    summary = [
        r for r in caplog.records
        if "Infopack load degraded" in r.getMessage()
    ]
    assert summary, "degraded load must emit one summary warning"
    assert "2 face ref(s) dropped" in summary[0].getMessage()
    assert "1 inventory entr" in summary[0].getMessage()
