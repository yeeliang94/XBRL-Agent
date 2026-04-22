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
_MPERS_TEMPLATE_DIR = Path(__file__).resolve().parent / "XBRL-template-MPERS"

# Keyed by filing standard. MFRS retains the pre-existing 10..14 numbering;
# MPERS shifts one slot up to 11..15 because slot 10 in the MPERS bundle is
# the MPERS-only SoRE face-statement template.
_TEMPLATE_DIRS_BY_STANDARD: dict[str, Path] = {
    "mfrs": _TEMPLATE_DIR,
    "mpers": _MPERS_TEMPLATE_DIR,
}


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


# Per-standard filename map — only MPERS deviates from NOTES_REGISTRY's
# MFRS defaults (shifted +1 slot across all 5 templates). The sheet names
# stay identical across standards so downstream code doesn't branch.
_NOTES_FILENAMES_BY_STANDARD: dict[tuple[NotesTemplateType, str], str] = {
    (NotesTemplateType.CORP_INFO, "mpers"): "11-Notes-CorporateInfo.xlsx",
    (NotesTemplateType.ACC_POLICIES, "mpers"): "12-Notes-AccountingPolicies.xlsx",
    (NotesTemplateType.LIST_OF_NOTES, "mpers"): "13-Notes-ListOfNotes.xlsx",
    (NotesTemplateType.ISSUED_CAPITAL, "mpers"): "14-Notes-IssuedCapital.xlsx",
    (NotesTemplateType.RELATED_PARTY, "mpers"): "15-Notes-RelatedParty.xlsx",
}


_VALID_LEVELS = ("company", "group")


def notes_template_path(
    template_type: NotesTemplateType,
    level: str = "company",
    standard: str = "mfrs",
) -> Path:
    """Absolute filesystem path to the notes template for (type, level, standard).

    Templates live under XBRL-template-MFRS/{Company,Group}/ (slots 10..14) or
    XBRL-template-MPERS/{Company,Group}/ (slots 11..15).
    """
    if level not in _VALID_LEVELS:
        raise ValueError(
            f"Invalid filing level {level!r} — must be one of {_VALID_LEVELS}"
        )
    if standard not in _TEMPLATE_DIRS_BY_STANDARD:
        raise ValueError(
            f"Invalid filing standard {standard!r} — "
            f"must be one of {tuple(_TEMPLATE_DIRS_BY_STANDARD)}"
        )
    entry = NOTES_REGISTRY[template_type]
    # MFRS keeps the filename pinned on the registry entry so any caller that
    # reads entry.template_filename directly still gets the 10..14 file. MPERS
    # routes through the override map.
    filename = _NOTES_FILENAMES_BY_STANDARD.get(
        (template_type, standard), entry.template_filename,
    )
    return _TEMPLATE_DIRS_BY_STANDARD[standard] / level.capitalize() / filename
