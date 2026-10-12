"""Issued Capital finishes only after every disclosed share class is listed.

Runs 183-185: Nielsen wrote ordinary shares only; a separate preference-share
note (RM5,000,000) reached the prose but never the numeric capital rows, and
the gap surfaced only as a post-run notes-to-face tie-out.
"""
import pytest

from notes.agent import (
    MAX_SHARE_CLASS_REFUSALS, NotesDeps, _share_class_save_gate,
    _submit_share_classes_impl,
)
from notes_types import NotesTemplateType
from token_tracker import TokenReport

AXIS = "ifrs-full_ClassesOfShareCapitalAxis"
ORDINARY = "ifrs-full_OrdinarySharesMember"
PREFERENCE = "ssmt-mfrs_NonredeemablePreferenceSharesMember"


def _deps(*members, standard="mfrs"):
    deps = NotesDeps(
        pdf_path="x.pdf", template_path="t.xlsx", model=None, output_dir=".",
        token_report=TokenReport(model="test"),
        template_type=NotesTemplateType.ISSUED_CAPITAL,
        sheet_name="Notes-Issuedcapital", filing_level="company",
        filing_standard=standard,
    )
    deps.viewed_pdf_pages = {32, 33}
    deps.numeric_cells = [
        {"sheet": "Notes-Issuedcapital", "row": 11, "col": 2, "value": 1,
         "dimensions": {AXIS: m} if m else {}}
        for m in members
    ]
    return deps


def _entry(name, member, page, action="written", **extra):
    return {"class_name": name, "note": "16", "source_pages": [page],
            "action": action, "dimension_member": member, **extra}


def test_save_is_refused_until_every_class_is_listed():
    deps = _deps(ORDINARY, PREFERENCE)
    assert "submit_share_classes" in _share_class_save_gate(deps)

    partial = _submit_share_classes_impl(deps, [_entry("Ordinary shares", ORDINARY, 32)])
    assert "refused" in partial and "no written entry names" in partial

    accepted = _submit_share_classes_impl(deps, [
        _entry("Ordinary shares", ORDINARY, 32),
        {**_entry("Preference shares", PREFERENCE, 33), "note": "17"},
    ])
    assert "accepted: 2 written" in accepted
    assert _share_class_save_gate(deps) is None


def test_claimed_class_without_its_own_payloads_is_refused():
    deps = _deps(ORDINARY)
    message = _submit_share_classes_impl(deps, [
        _entry("Ordinary shares", ORDINARY, 32),
        _entry("Preference shares", PREFERENCE, 33),
    ])
    assert "Preference shares" in message and "write its rows first" in message


def test_two_unresolved_classes_cannot_share_one_fact_identity():
    deps = _deps("")
    message = _submit_share_classes_impl(deps, [
        _entry("Ordinary shares", "", 32), _entry("Preference shares", "", 33),
    ])
    assert "overwrite each other" in message


def test_not_written_class_needs_a_reason_and_stays_visible():
    deps = _deps(ORDINARY)
    assert "requires a reason" in _submit_share_classes_impl(deps, [
        _entry("Ordinary shares", ORDINARY, 32),
        _entry("Preference shares", "", 33, action="not_written"),
    ])
    _submit_share_classes_impl(deps, [
        _entry("Ordinary shares", ORDINARY, 32),
        _entry("Preference shares", "", 33, action="not_written",
               reason="Redeemed in full before the opening date; no balance shown."),
    ])
    assert any("Preference shares" in w for w in deps.write_skip_errors)


def test_unviewed_citation_and_stale_receipt_are_refused():
    deps = _deps(ORDINARY)
    assert "view pages [40]" in _submit_share_classes_impl(
        deps, [_entry("Ordinary shares", ORDINARY, 40)])
    _submit_share_classes_impl(deps, [_entry("Ordinary shares", ORDINARY, 32)])
    deps.numeric_cells.append({"sheet": "Notes-Issuedcapital", "row": 11, "col": 2,
                               "value": 5, "dimensions": {AXIS: PREFERENCE}})
    assert "changed after the share-class receipt" in _share_class_save_gate(deps)


def test_mpers_uses_its_own_share_class_axis():
    deps = _deps(standard="mpers")
    deps.numeric_cells = [{"sheet": "Notes-Issuedcapital", "row": 11, "col": 2, "value": 1,
                           "dimensions": {"ifrs-smes_ClassesOfShareCapitalAxis":
                                          "ssmt-mpers_OrdinarySharesMember"}}]
    assert "accepted" in _submit_share_classes_impl(
        deps, [_entry("Ordinary shares", "ssmt-mpers_OrdinarySharesMember", 32)])


def test_missing_receipt_finishes_with_a_warning_after_bounded_refusals():
    deps = _deps(ORDINARY)
    for _ in range(MAX_SHARE_CLASS_REFUSALS):
        assert _share_class_save_gate(deps) is not None
    assert _share_class_save_gate(deps) is None
    assert any("Share-class coverage was not confirmed" in w for w in deps.write_skip_errors)


def test_other_notes_templates_are_not_gated():
    deps = _deps(ORDINARY)
    deps.template_type = NotesTemplateType.RELATED_PARTY
    assert _share_class_save_gate(deps) is None


def test_corrected_receipt_replaces_its_earlier_warnings():
    """Review finding: after writing the skipped preference shares, the old
    'not written' warning survived the corrected receipt."""
    deps = _deps(ORDINARY)
    deps.write_skip_errors.append("Unrelated writer warning")
    _submit_share_classes_impl(deps, [
        _entry("Ordinary shares", ORDINARY, 32),
        _entry("Preference shares", "", 33, action="not_written", reason="Not found at first."),
    ])
    assert any("Preference shares" in w for w in deps.write_skip_errors)
    deps.numeric_cells.append({"sheet": "Notes-Issuedcapital", "row": 11, "col": 2,
                               "value": 5, "dimensions": {AXIS: PREFERENCE}})
    assert "accepted" in _submit_share_classes_impl(deps, [
        _entry("Ordinary shares", ORDINARY, 32), _entry("Preference shares", PREFERENCE, 33),
    ])
    assert deps.write_skip_errors == ["Unrelated writer warning"]


def test_receipt_accepted_after_an_unconfirmed_exit_clears_that_warning():
    from notes.agent import SHARE_CLASS_UNCONFIRMED, share_class_exit_check
    deps = _deps(ORDINARY)
    share_class_exit_check(deps)
    assert SHARE_CLASS_UNCONFIRMED in deps.write_skip_errors
    _submit_share_classes_impl(deps, [_entry("Ordinary shares", ORDINARY, 32)])
    share_class_exit_check(deps)
    assert SHARE_CLASS_UNCONFIRMED not in deps.write_skip_errors
