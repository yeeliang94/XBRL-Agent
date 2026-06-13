"""PLAN-orchestration-hardening item 23 — face-agent coverage receipts.

Pins: the receipt model + warning derivation, conditional tool registration
(only when scout supplied face_line_refs), and that coverage is advisory —
a partial receipt yields warnings, never a save block.
"""
from __future__ import annotations

import pytest

from extraction.coverage import (
    FaceCoverageReceipt,
    FaceCoverageEntry,
    face_coverage_warnings,
    expected_ref_label,
)


_REFS = [
    {"label": "Trade receivables", "note_num": 18, "section": "current assets"},
    {"label": "Other investments", "note_num": 12, "section": "non-current assets"},
    {"label": "Cash and bank balances", "note_num": 20, "section": "current assets"},
]


# --------------------------------------------------------------------------
# Receipt parsing + validation
# --------------------------------------------------------------------------

def test_receipt_parses_written_and_skipped():
    receipt = FaceCoverageReceipt.from_json(
        '[{"ref": "Trade receivables", "action": "written"},'
        ' {"ref": "Other investments", "action": "skipped",'
        '  "reason": "not on the face statement"}]'
    )
    assert {e.ref for e in receipt.entries} == {"Trade receivables", "Other investments"}


def test_skipped_entry_requires_reason():
    with pytest.raises(ValueError):
        FaceCoverageEntry(ref="X", action="skipped", reason="")


def test_unknown_action_rejected():
    with pytest.raises(ValueError):
        FaceCoverageEntry(ref="X", action="invented")


def test_validate_flags_unknown_ref():
    receipt = FaceCoverageReceipt.from_json(
        '[{"ref": "Goodwill on the moon", "action": "written"}]'
    )
    errors = receipt.validate(_REFS)
    assert len(errors) == 1 and "not one of the scout-observed" in errors[0]


def test_validate_label_match_is_normalised():
    # '*Trade Receivables ' must match the scout's 'Trade receivables'.
    receipt = FaceCoverageReceipt.from_json(
        '[{"ref": "*Trade Receivables ", "action": "written"}]'
    )
    assert receipt.validate(_REFS) == []


# --------------------------------------------------------------------------
# Warning derivation
# --------------------------------------------------------------------------

def test_full_receipt_yields_no_warnings():
    receipt = FaceCoverageReceipt(entries=[
        FaceCoverageEntry("Trade receivables", "written"),
        FaceCoverageEntry("Other investments", "skipped", "not disclosed on face"),
        FaceCoverageEntry("Cash and bank balances", "written"),
    ])
    assert face_coverage_warnings(_REFS, receipt) == []


def test_missing_entries_become_per_ref_warnings():
    receipt = FaceCoverageReceipt(entries=[
        FaceCoverageEntry("Trade receivables", "written"),
    ])
    warns = face_coverage_warnings(_REFS, receipt)
    assert len(warns) == 2
    assert any("Other investments (Note 12)" in w for w in warns)
    assert any("Cash and bank balances (Note 20)" in w for w in warns)


def test_no_receipt_warns_every_ref():
    warns = face_coverage_warnings(_REFS, None)
    assert len(warns) == 3


def test_no_refs_no_warnings():
    assert face_coverage_warnings([], None) == []
    assert face_coverage_warnings([], FaceCoverageReceipt()) == []


def test_expected_ref_label_formats_note():
    assert expected_ref_label(_REFS[0]) == "Trade receivables (Note 18)"
    assert expected_ref_label({"label": "Bare line"}) == "Bare line"


# --------------------------------------------------------------------------
# Conditional tool registration on the extraction agent
# --------------------------------------------------------------------------

def _tool_names(agent) -> set[str]:
    names: set[str] = set()
    for ts in getattr(agent, "toolsets", []) or []:
        tools = getattr(ts, "tools", {}) or {}
        if isinstance(tools, dict):
            names.update(tools.keys())
    return names


def _make_agent(page_hints):
    from pydantic_ai.models.test import TestModel
    from statement_types import StatementType
    from extraction.agent import create_extraction_agent
    return create_extraction_agent(
        statement_type=StatementType.SOFP, variant="CuNonCu",
        pdf_path="/tmp/test.pdf", template_path="/tmp/test.xlsx",
        model=TestModel(), output_dir="/tmp/output", page_hints=page_hints,
    )


def test_tool_registered_only_when_refs_present():
    agent, deps = _make_agent({"face_line_refs": _REFS})
    assert "submit_face_coverage" in _tool_names(agent)
    assert deps.face_line_refs == _REFS


def test_tool_absent_without_refs():
    agent, deps = _make_agent({"face_page": 5, "note_pages": [6, 7]})
    assert "submit_face_coverage" not in _tool_names(agent)
    assert deps.face_line_refs == []


def test_malformed_refs_drop_to_empty_expectation_list():
    # A ref dict with no label is skipped — falls through to bare-hint
    # behaviour (gotcha #13 graceful degradation), so no coverage tool.
    agent, deps = _make_agent({"face_line_refs": [{"note_num": 5, "label": ""}]})
    assert deps.face_line_refs == []
    assert "submit_face_coverage" not in _tool_names(agent)
