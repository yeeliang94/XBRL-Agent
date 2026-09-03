"""PLAN-orchestration-hardening item 23 — face-agent coverage receipts.

Pins: the receipt model + warning derivation, conditional tool registration
(only when scout supplied face_line_refs), and source-completeness gating.
Scout hints remain advisory facts because an inspected line can be skipped
with a reason; a ``written`` claim must reconcile to a successful target.
"""
from __future__ import annotations

import pytest

from extraction.coverage import (
    FaceCoverageReceipt,
    FaceCoverageEntry,
    face_coverage_warnings,
    expected_ref_label,
    parse_face_coverage_entries,
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
    receipt, errors = parse_face_coverage_entries([
        {"ref": "Trade receivables", "action": "written"},
        {"ref": "Other investments", "action": "skipped",
         "reason": "not on the face statement"},
    ])
    assert errors == []
    assert {e.ref for e in receipt.entries} == {"Trade receivables", "Other investments"}


def test_malformed_receipt_entry_is_reported_without_rejecting_valid_sibling():
    receipt, errors = parse_face_coverage_entries([
        {"ref": "Trade receivables", "action": "written"},
        {"ref": "Other investments", "action": "skipped"},
    ])
    assert [entry.ref for entry in receipt.entries] == ["Trade receivables"]
    assert len(errors) == 1
    assert "reason" in errors[0].lower()


def test_skipped_entry_requires_reason():
    with pytest.raises(ValueError):
        FaceCoverageEntry(ref="X", action="skipped", reason="")


def test_unknown_action_rejected():
    with pytest.raises(ValueError):
        FaceCoverageEntry(ref="X", action="invented")


def test_validate_flags_unknown_ref():
    receipt, errors = parse_face_coverage_entries([
        {"ref": "Goodwill on the moon", "action": "written"},
    ])
    assert errors == []
    errors = receipt.validate(_REFS)
    assert len(errors) == 1 and "not one of the scout-observed" in errors[0]


def test_written_receipt_must_point_to_a_successful_fact_target():
    """A self-reported ``written`` action is not coverage unless the target
    corresponds to a write that actually landed in the canonical path.
    """
    receipt, parse_errors = parse_face_coverage_entries([
        {
            "ref": "Payment of lease liabilities",
            "action": "written",
            "target": "Payment of lease liabilities",
        },
    ])
    assert parse_errors == []

    errors = receipt.validate(
        [{"label": "Payment of lease liabilities", "section": "financing"}],
        written_targets={"Interest paid"},
    )

    assert any(
        "no workbook write or canonical fact" in error.lower()
        for error in errors
    )
    assert any("'Interest paid'" in error for error in errors)


def test_written_receipt_accepts_a_persisted_fact_target():
    receipt, parse_errors = parse_face_coverage_entries([{
        "ref": "Payment of lease liabilities",
        "action": "written",
        "target": "Cash used to repay lease liabilities",
    }])
    assert parse_errors == []

    errors = receipt.validate(
        [{"label": "Payment of lease liabilities", "section": "financing"}],
        written_targets={"Cash used to repay lease liabilities"},
    )

    assert errors == []


def test_written_receipt_accepts_workbook_only_target_with_warning():
    receipt, parse_errors = parse_face_coverage_entries([{
        "ref": "Statement date",
        "action": "written",
        "target": "SOFP-CuNonCu!D1",
    }])
    assert parse_errors == []

    errors = receipt.validate(
        [{"label": "Statement date", "section": "heading"}],
        written_targets={"Trade receivables"},
        workbook_only_targets={"SOFP-CuNonCu!D1", "Statement date"},
    )
    warnings = receipt.workbook_only_warnings(
        written_targets={"Trade receivables"},
        workbook_only_targets={"SOFP-CuNonCu!D1", "Statement date"},
    )

    assert errors == []
    assert len(warnings) == 1
    assert "landed in the workbook" in warnings[0]
    assert "did not map to a canonical concept" in warnings[0]


def test_validate_label_match_is_normalised():
    # '*Trade Receivables ' must match the scout's 'Trade receivables'.
    receipt, errors = parse_face_coverage_entries([
        {"ref": "*Trade Receivables ", "action": "written"},
    ])
    assert errors == []
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
# Decorated-ref matching (2026-08-05 SOFP run: 15/15 false warnings)
# --------------------------------------------------------------------------
# The agent never sees the bare label alone. The prompt renders each line as
# "Label → Note 2"; the old failure feedback echoed "Label (Note 2)". The
# agent submitted first one form, then the other; neither matched, so every
# line warned on a fully-filled statement and the retry could not converge.

@pytest.mark.parametrize("ref", [
    "Trade receivables → Note 18",     # the prompt's rendering
    "Trade receivables -> Note 18",    # ASCII-arrow variant
    "Trade receivables (Note 18)",     # the old feedback's rendering
    "*Trade Receivables (note 18)",    # decoration + the older loose forms
])
def test_decorated_refs_match_the_bare_label(ref):
    receipt, errors = parse_face_coverage_entries([
        {"ref": ref, "action": "written"},
    ])
    assert errors == []
    assert receipt.validate(_REFS) == []
    warns = face_coverage_warnings(_REFS, receipt)
    assert not any("Trade receivables" in w for w in warns)


def test_note_reference_inside_a_label_is_not_stripped():
    # Only a TRAILING decoration is stripped — a label that genuinely ends
    # differently, or carries the words mid-label, must stay distinct.
    from extraction.coverage import _normalize_ref

    assert _normalize_ref("Amount due (note 9 companies)") == \
        "amount due (note 9 companies)"
    assert _normalize_ref("Trade receivables (Note 18)") == "trade receivables"


def test_unaccounted_labels_returns_the_accepted_spellings():
    # The tool's failure reply must hand back the BARE labels — the exact
    # strings the matcher accepts — never the display sentence.
    from extraction.coverage import unaccounted_labels

    receipt = FaceCoverageReceipt(entries=[
        FaceCoverageEntry("Trade receivables", "written"),
    ])
    labels = unaccounted_labels(_REFS, receipt)
    assert labels == ["Other investments", "Cash and bank balances"]
    assert not any("(Note" in lbl for lbl in labels)
    assert unaccounted_labels(_REFS, None) == [
        "Trade receivables", "Other investments", "Cash and bank balances",
    ]


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
    schema = agent._function_toolset.tools[
        "submit_face_coverage"
    ].function_schema.json_schema
    assert "propertyNames" not in str(schema)
    assert "receipt_json" not in str(schema)


def test_verify_reply_nudges_coverage_before_terminal_save():
    from extraction.agent import _face_coverage_pre_save_nudge

    _agent, deps = _make_agent({"face_line_refs": _REFS})

    nudge = _face_coverage_pre_save_nudge(deps)

    assert "Before save_result" in nudge
    assert "submit_face_coverage" in nudge
    deps.face_coverage_submitted = True
    assert _face_coverage_pre_save_nudge(deps) == ""


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
