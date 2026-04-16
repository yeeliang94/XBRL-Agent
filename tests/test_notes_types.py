"""Unit tests for notes_types.py — the notes-template registry."""
from __future__ import annotations

from pathlib import Path

import pytest

from notes_types import (
    NOTES_REGISTRY,
    NotesTemplateEntry,
    NotesTemplateType,
    notes_template_path,
)


def test_enum_has_all_five_notes_templates():
    # Order doesn't matter; membership does.
    assert {t.value for t in NotesTemplateType} == {
        "CORP_INFO",
        "ACC_POLICIES",
        "LIST_OF_NOTES",
        "ISSUED_CAPITAL",
        "RELATED_PARTY",
    }


@pytest.mark.parametrize(
    "template_type,expected_filename,expected_sheet",
    [
        (NotesTemplateType.CORP_INFO, "10-Notes-CorporateInfo.xlsx", "Notes-CI"),
        (NotesTemplateType.ACC_POLICIES, "11-Notes-AccountingPolicies.xlsx", "Notes-SummaryofAccPol"),
        (NotesTemplateType.LIST_OF_NOTES, "12-Notes-ListOfNotes.xlsx", "Notes-Listofnotes"),
        (NotesTemplateType.ISSUED_CAPITAL, "13-Notes-IssuedCapital.xlsx", "Notes-Issuedcapital"),
        (NotesTemplateType.RELATED_PARTY, "14-Notes-RelatedParty.xlsx", "Notes-RelatedPartytran"),
    ],
)
def test_registry_maps_types_to_filenames_and_sheets(
    template_type, expected_filename, expected_sheet
):
    entry = NOTES_REGISTRY[template_type]
    assert isinstance(entry, NotesTemplateEntry)
    assert entry.template_filename == expected_filename
    assert entry.sheet_name == expected_sheet


def test_notes_template_path_company_level_returns_company_file():
    p = notes_template_path(NotesTemplateType.CORP_INFO, level="company")
    assert p.exists(), f"expected Company template to exist: {p}"
    assert p.parent.name == "Company"
    assert p.name == "10-Notes-CorporateInfo.xlsx"


def test_notes_template_path_group_level_returns_group_file():
    p = notes_template_path(NotesTemplateType.LIST_OF_NOTES, level="group")
    assert p.exists()
    assert p.parent.name == "Group"
    assert p.name == "12-Notes-ListOfNotes.xlsx"


def test_notes_template_path_rejects_bad_level():
    with pytest.raises(ValueError, match="filing level"):
        notes_template_path(NotesTemplateType.CORP_INFO, level="consolidated")
