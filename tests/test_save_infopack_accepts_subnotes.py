"""Phase 1b Step 12 — _save_infopack_impl accepts LLM-submitted subnotes.

Two scenarios:
1. LLM submits a `notes_inventory` with nested subnotes → they reach the
   final Infopack.
2. Malformed subnote entries inside otherwise-valid notes are dropped;
   the parent note still loads.

The `_populate_inventory_via_vision` post-scout safety net already
forwards subnotes for free because it carries through whatever
`NoteInventoryEntry` objects the vision discoverer constructs (those
objects now have populated `.subnotes` per Step 11). That path is
exercised by tests/test_scout_subnotes_via_vision.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from statement_types import StatementType
from scout.agent import ScoutDeps, _save_infopack_impl
import json


def _make_deps() -> ScoutDeps:
    return ScoutDeps(
        pdf_path="/dev/null",
        pdf_length=100,
        statements_to_find=None,
        on_progress=None,
    )


def test_llm_submitted_subnotes_land_on_infopack():
    deps = _make_deps()
    payload = {
        "toc_page": 2,
        "page_offset": 0,
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu",
                "face_page": 5,
                "note_pages": [],
                "confidence": "HIGH",
            },
        },
        "notes_inventory": [
            {
                "note_num": 2,
                "title": "Significant accounting policies",
                "page_range": [15, 22],
                "subnotes": [
                    {"subnote_ref": "2.1", "title": "Basis of preparation",
                     "page_range": [15, 15]},
                    {"subnote_ref": "2.14", "title": "Employee benefits",
                     "page_range": [20, 20]},
                ],
            },
            {
                "note_num": 4,
                "title": "PPE",
                "page_range": [45, 47],
                # No subnotes — must load with empty list, not error.
            },
        ],
    }
    result = _save_infopack_impl(deps, json.dumps(payload))
    assert "saved successfully" in result.lower()
    inv = deps.infopack.notes_inventory
    assert len(inv) == 2

    note2 = inv[0]
    refs = [s.subnote_ref for s in note2.subnotes]
    assert refs == ["2.1", "2.14"]
    titles = [s.title for s in note2.subnotes]
    assert "Basis of preparation" in titles[0]

    note4 = inv[1]
    assert note4.subnotes == []


def test_malformed_subnote_entries_dropped_silently():
    deps = _make_deps()
    payload = {
        "toc_page": 2,
        "page_offset": 0,
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu",
                "face_page": 5,
                "note_pages": [],
                "confidence": "HIGH",
            },
        },
        "notes_inventory": [
            {
                "note_num": 2,
                "title": "Policies",
                "page_range": [15, 22],
                "subnotes": [
                    {"subnote_ref": "2.1", "title": "ok",
                     "page_range": [15, 15]},
                    {"subnote_ref": "", "title": "empty ref",
                     "page_range": [15, 15]},          # drop
                    "not a dict",                       # drop
                    {"subnote_ref": "2.2", "title": "bad page",
                     "page_range": "garbage"},          # drop
                    {"subnote_ref": "2.3", "title": "bad page2",
                     "page_range": [0, 1]},             # drop (validator)
                ],
            },
        ],
    }
    result = _save_infopack_impl(deps, json.dumps(payload))
    assert "saved successfully" in result.lower()
    refs = [s.subnote_ref for s in deps.infopack.notes_inventory[0].subnotes]
    # Only the OK entry survives — bad subnotes don't take down the
    # parent note or block the whole save.
    assert refs == ["2.1"]


# ---------------------------------------------------------------------------
# Phase 8.2 — save_infopack reports SURVIVING counts so the agent can
# self-correct in-run (e.g. notice a note count that looks too low).
# ---------------------------------------------------------------------------


def test_save_infopack_reports_survival_counts():
    deps = _make_deps()
    payload = {
        "toc_page": 2,
        "page_offset": 0,
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu",
                "face_page": 5,
                "note_pages": [],
                "confidence": "HIGH",
                "face_line_refs": [
                    {"label": "Property, plant and equipment", "note_num": 4,
                     "section": "non-current assets"},
                    {"label": "Trade receivables", "note_num": None,
                     "section": "current assets"},
                ],
            },
        },
        "notes_inventory": [
            {"note_num": 2, "title": "Policies", "page_range": [15, 22]},
            {"note_num": 4, "title": "PPE", "page_range": [45, 47]},
        ],
    }
    result = _save_infopack_impl(deps, json.dumps(payload))
    low = result.lower()
    assert "1 statement(s)" in low
    assert "2 note(s)" in low
    assert "2 face-ref(s)" in low
    # A null note_num ref still survives (confidence-gated, Phase 8.1).
    refs = deps.infopack.statements[StatementType.SOFP].face_line_refs
    assert [r.note_num for r in refs] == [4, None]


def test_save_infopack_flags_skipped_inventory_entries():
    deps = _make_deps()
    payload = {
        "toc_page": 2,
        "page_offset": 0,
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu",
                "face_page": 5,
                "note_pages": [],
                "confidence": "HIGH",
            },
        },
        "notes_inventory": [
            {"note_num": 2, "title": "ok", "page_range": [15, 22]},
            {"note_num": 4, "title": "bad page", "page_range": "garbage"},  # skipped
        ],
    }
    result = _save_infopack_impl(deps, json.dumps(payload))
    low = result.lower()
    assert "1 note(s)" in low  # only the valid entry survived
    assert "skipped" in low and "re-check" in low
