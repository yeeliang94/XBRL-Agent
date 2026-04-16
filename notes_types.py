"""Central registry of the 5 MBRS notes-template types.

Parallel to `statement_types.py` but for the notes workbook side of the
pipeline. Adding or renaming a notes template only touches this file.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class NotesTemplateType(str, Enum):
    """The 5 notes templates we fill from PDF disclosures."""
    CORP_INFO = "CORP_INFO"            # Sheet 10 — Corporate information
    ACC_POLICIES = "ACC_POLICIES"      # Sheet 11 — Summary of material accounting policies
    LIST_OF_NOTES = "LIST_OF_NOTES"    # Sheet 12 — List of notes (138 rows; sub-agent fan-out)
    ISSUED_CAPITAL = "ISSUED_CAPITAL"  # Sheet 13 — Issued capital (numeric movement table)
    RELATED_PARTY = "RELATED_PARTY"    # Sheet 14 — Related-party transactions (numeric)


@dataclass(frozen=True)
class NotesTemplateEntry:
    template_filename: str
    sheet_name: str
    # Whether this template expects structured numeric values (True) vs.
    # free-form prose (False). Writer uses this to decide column placement.
    is_numeric: bool = False


_TEMPLATE_DIR = Path(__file__).resolve().parent / "XBRL-template-MFRS"


NOTES_REGISTRY: dict[NotesTemplateType, NotesTemplateEntry] = {
    NotesTemplateType.CORP_INFO: NotesTemplateEntry(
        template_filename="10-Notes-CorporateInfo.xlsx",
        sheet_name="Notes-CI",
    ),
    NotesTemplateType.ACC_POLICIES: NotesTemplateEntry(
        template_filename="11-Notes-AccountingPolicies.xlsx",
        sheet_name="Notes-SummaryofAccPol",
    ),
    NotesTemplateType.LIST_OF_NOTES: NotesTemplateEntry(
        template_filename="12-Notes-ListOfNotes.xlsx",
        sheet_name="Notes-Listofnotes",
    ),
    NotesTemplateType.ISSUED_CAPITAL: NotesTemplateEntry(
        template_filename="13-Notes-IssuedCapital.xlsx",
        sheet_name="Notes-Issuedcapital",
        is_numeric=True,
    ),
    NotesTemplateType.RELATED_PARTY: NotesTemplateEntry(
        template_filename="14-Notes-RelatedParty.xlsx",
        sheet_name="Notes-RelatedPartytran",
        is_numeric=True,
    ),
}


_VALID_LEVELS = ("company", "group")


def notes_template_path(template_type: NotesTemplateType, level: str = "company") -> Path:
    """Absolute filesystem path to the notes template for (type, level).

    Templates live under XBRL-template-MFRS/Company/ or XBRL-template-MFRS/Group/.
    """
    if level not in _VALID_LEVELS:
        raise ValueError(
            f"Invalid filing level {level!r} — must be one of {_VALID_LEVELS}"
        )
    entry = NOTES_REGISTRY[template_type]
    return _TEMPLATE_DIR / level.capitalize() / entry.template_filename
