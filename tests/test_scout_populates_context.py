"""Phase 2 Step 17 — scout's save_infopack accepts context fields.

Asserts:
- LLM-supplied entity_name / reporting periods / scale_unit etc. land on
  the saved Infopack
- Bad scale_unit values are coerced to "unknown" at the save surface
  (not just at the from_json surface — defence in depth)
- Empty / whitespace strings coerce to None (the renderer omits them)
"""
from __future__ import annotations

import json

from scout.agent import ScoutDeps, _save_infopack_impl


def _make_deps() -> ScoutDeps:
    return ScoutDeps(
        pdf_path="/dev/null",
        pdf_length=100,
        statements_to_find=None,
        on_progress=None,
    )


def test_full_context_lands_on_infopack():
    deps = _make_deps()
    payload = {
        "toc_page": 2,
        "page_offset": 0,
        "entity_name": "FINCO Berhad",
        "reporting_period_cy": "01/01/2022 - 31/12/2022",
        "reporting_period_py": "01/01/2021 - 31/12/2021",
        "currency": "RM",
        "scale_unit": "thousands",
        "consolidation_level": "company",
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu",
                "face_page": 5,
                "note_pages": [],
                "confidence": "HIGH",
            },
        },
        "notes_inventory": [],
    }
    msg = _save_infopack_impl(deps, json.dumps(payload))
    assert "saved successfully" in msg.lower()
    p = deps.infopack
    assert p.entity_name == "FINCO Berhad"
    assert p.reporting_period_cy == "01/01/2022 - 31/12/2022"
    assert p.reporting_period_py == "01/01/2021 - 31/12/2021"
    assert p.currency == "RM"
    assert p.scale_unit == "thousands"
    assert p.consolidation_level == "company"


def test_bad_scale_unit_coerced_at_save_surface():
    deps = _make_deps()
    payload = {
        "toc_page": 2, "page_offset": 0,
        "scale_unit": "thousands-of-millions",  # nonsense
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu", "face_page": 5,
                "note_pages": [], "confidence": "HIGH",
            },
        },
        "notes_inventory": [],
    }
    _save_infopack_impl(deps, json.dumps(payload))
    # Bad scale_unit coerces to "unknown" — never to a value the
    # renderer might treat as trustworthy.
    assert deps.infopack.scale_unit == "unknown"


def test_missing_context_fields_use_safe_defaults():
    deps = _make_deps()
    payload = {
        "toc_page": 2, "page_offset": 0,
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu", "face_page": 5,
                "note_pages": [], "confidence": "HIGH",
            },
        },
        "notes_inventory": [],
    }
    _save_infopack_impl(deps, json.dumps(payload))
    p = deps.infopack
    assert p.entity_name is None
    assert p.scale_unit == "unknown"
    assert p.consolidation_level == "unknown"


def test_whitespace_entity_name_becomes_none():
    deps = _make_deps()
    payload = {
        "toc_page": 2, "page_offset": 0,
        "entity_name": "  ",
        "reporting_period_cy": "",
        "statements": {
            "SOFP": {
                "variant_suggestion": "CuNonCu", "face_page": 5,
                "note_pages": [], "confidence": "HIGH",
            },
        },
        "notes_inventory": [],
    }
    _save_infopack_impl(deps, json.dumps(payload))
    assert deps.infopack.entity_name is None
    assert deps.infopack.reporting_period_cy is None
