"""Taxonomy identities for canonical template cells.

The canonical parser historically retained only the visible Excel label.  That
is insufficient for mTool filing: labels repeat, while a filing fact is
identified by its taxonomy concept plus any dimensions.  This module restores
that identity from the same presentation-linkbase roles that generated the
repository templates.

The public interface is deliberately small: :func:`semantic_addresses_for`
returns a coordinate-keyed mapping for one template.  Callers do not need to
know role numbers, taxonomy paths, SOCIE row blocks, or component-member ids.
Failure is fail-closed: an unrecognised or drifted template receives no
semantic address and remains usable by the extraction pipeline, but mTool
readiness will report the missing filing identity.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import openpyxl


TAXONOMY_VERSION = "SSMxT_2022v1.0"
ADDRESS_VERSION = "2022-v1"

# The presentation roles are the source used by the template generators.
_ROLES_BY_FILE: dict[str, tuple[str, ...]] = {
    "01-SOFP-CuNonCu.xlsx": ("210000", "210100"),
    "02-SOFP-OrderOfLiquidity.xlsx": ("220000", "220100"),
    "03-SOPL-Function.xlsx": ("310000", "310100"),
    "04-SOPL-Nature.xlsx": ("320000", "320100"),
    "05-SOCI-BeforeTax.xlsx": ("420000",),
    "06-SOCI-NetOfTax.xlsx": ("410000",),
    "07-SOCF-Indirect.xlsx": ("520000",),
    "08-SOCF-Direct.xlsx": ("510000",),
    "09-SOCIE.xlsx": ("610000",),
    "10-SoRE.xlsx": ("620000",),
    "13-Notes-IssuedCapital.xlsx": ("740000",),
    "14-Notes-RelatedParty.xlsx": ("750000",),
    "14-Notes-IssuedCapital.xlsx": ("740000",),
    "15-Notes-RelatedParty.xlsx": ("750000",),
}

# MFRS SOCIE's columns are explicit members of ComponentsOfEquityAxis.  The
# presentation linkbase contains the core members; the SSM extension members
# below are defined elsewhere in the same taxonomy package.
_MFRS_SOCIE_COMPONENTS: tuple[str, ...] = (
    "ifrs-full_IssuedCapitalMember",
    "ifrs-full_RetainedEarningsMember",
    "ifrs-full_TreasurySharesMember",
    "ifrs-full_CapitalReserveMember",
    "ifrs-full_ReserveOfGainsAndLossesOnHedgingInstrumentsThatHedgeInvestmentsInEquityInstrumentsMember",
    "ssmt-mfrs_ForeignCurrencyTranslationReserveMember",
    "ifrs-full_ReserveOfSharebasedPaymentsMember",
    "ifrs-full_RevaluationSurplusMember",
    "ifrs-full_StatutoryReserveMember",
    "ifrs-full_WarrantReserveMember",
    "ssmt-mfrs_OtherNondistributableReserveMember",
    "ssmt_NonDistributableReservesMember",
    "ssmt-mfrs_FairValueAdjustmentReserveMember",
    "ssmt_ReserveOfNoncurrentAssetsClassifiedAsHeldForSaleMember",
    "ssmt-mfrs_ConsolidatedReserveMember",
    "ssmt-mfrs_WarrantyReserveMember",
    "ssmt-mfrs_OtherDistributableReserveMember",
    "ssmt_DistributableReservesMember",
    "ssmt_ReservesMember",
    "ifrs-full_EquityAttributableToOwnersOfParentMember",
    "ssmt-mfrs_OtherComponentsOfEquityMember",
    "ifrs-full_NoncontrollingInterestsMember",
    "ifrs-full_EquityMember",
)

def _standard_and_level(path: Path) -> tuple[str, str] | None:
    lowered = [part.lower() for part in path.parts]
    standard = (
        "mpers" if any("xbrl-template-mpers" in part for part in lowered)
        else "mfrs" if any("xbrl-template-mfrs" in part for part in lowered)
        else None
    )
    level = "group" if "group" in lowered else "company" if "company" in lowered else None
    if standard is None or level is None:
        return None
    return standard, level


@lru_cache(maxsize=64)
def _role_rows(standard: str, role: str) -> tuple[tuple[int, str, str, bool], ...]:
    """Read one role through the generator's tested presentation walker.

    The generator's public adapter owns its cache/context isolation, so this
    consumer does not reach into generator-private globals.
    """
    from scripts import generate_mpers_templates as taxonomy

    root = Path(__file__).resolve().parent.parent
    tax_dir = root / "SSMxT_2022v1.0/rep/ssm/ca-2016/fs" / standard
    return tuple(taxonomy.walk_role_for_taxonomy(tax_dir, standard, role))


def _address(primary: str, dimensions: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "primary_concept": primary,
        "dimensions": dict(sorted((dimensions or {}).items())),
        "taxonomy_version": TAXONOMY_VERSION,
        "address_version": ADDRESS_VERSION,
    }


def _linear_addresses(path: Path, standard: str) -> dict[tuple[str, int, str | None], dict[str, Any]]:
    roles = _ROLES_BY_FILE.get(path.name)
    if not roles:
        return {}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        if len(wb.worksheets) != len(roles):
            return {}
        out: dict[tuple[str, int, str | None], dict[str, Any]] = {}
        for ws, role in zip(wb.worksheets, roles):
            sheet_rows = [
                row for row in range(1, ws.max_row + 1)
                if ws.cell(row, 1).value not in (None, "")
            ]
            taxonomy_rows = list(_role_rows(standard, role))
            # One MFRS SOCI template carries a display-only title above the exact
            # linkbase rows.  A larger discrepancy means the template drifted and
            # must not receive guessed identities.
            if len(sheet_rows) == len(taxonomy_rows) + 1:
                sheet_rows = sheet_rows[1:]
            if len(sheet_rows) != len(taxonomy_rows):
                continue
            for row, (_depth, concept_id, _label, _abstract) in zip(sheet_rows, taxonomy_rows):
                out[(ws.title, row, None)] = _address(concept_id)
        return out
    finally:
        wb.close()


def _matrix_addresses(path: Path, standard: str, level: str) -> dict[tuple[str, int, str | None], dict[str, Any]]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        ws = wb["SOCIE"] if "SOCIE" in wb.sheetnames else wb[wb.sheetnames[0]]
        taxonomy_rows = list(_role_rows(standard, "610000"))

        if standard == "mpers":
            # MPERS's single value column is not a component dimension.  The
            # canonical parser starts at Equity and excludes the two title rows.
            line_items = taxonomy_rows[2:]
            # Company begins at row 5; Group has one additional block heading.
            base_row = 6 if level == "group" else 5
            rows = list(range(base_row, base_row + len(line_items)))
            return {
                (ws.title, row, "B"): _address(concept_id)
                for row, (_depth, concept_id, _label, _abstract) in zip(rows, line_items)
            }

        marker = "ifrs-full_StatementOfChangesInEquityLineItems"
        try:
            start = next(i for i, item in enumerate(taxonomy_rows) if item[1] == marker) + 1
        except StopIteration:
            return {}
        line_items = taxonomy_rows[start:]
        # The MFRS canonical matrix uses its first CY block, rows 6..25, as the
        # concept home; later blocks are render targets for period/scope.
        rows = list(range(6, 6 + len(line_items)))
        if len(line_items) != 20 or ws.max_column < 24:
            return {}
        axis = "ifrs-full_ComponentsOfEquityAxis"
        out: dict[tuple[str, int, str | None], dict[str, Any]] = {}
        for row, (_depth, primary, _label, _abstract) in zip(rows, line_items):
            for offset, member in enumerate(_MFRS_SOCIE_COMPONENTS, start=2):
                col = openpyxl.utils.get_column_letter(offset)
                out[(ws.title, row, col)] = _address(primary, {axis: member})
        return out
    finally:
        wb.close()


@lru_cache(maxsize=64)
def semantic_addresses_for(path_value: str) -> dict[tuple[str, int, str | None], dict[str, Any]]:
    """Return semantic addresses keyed by ``(sheet, row, matrix_col)``.

    ``matrix_col`` is ``None`` for linear concepts.  The returned dictionaries
    are safe to serialize into the concept-tree JSON.
    """
    path = Path(path_value).resolve()
    family = _standard_and_level(path)
    if family is None or path.name not in _ROLES_BY_FILE:
        return {}
    standard, level = family
    if path.name == "09-SOCIE.xlsx":
        return _matrix_addresses(path, standard, level)
    return _linear_addresses(path, standard)


__all__ = [
    "ADDRESS_VERSION",
    "TAXONOMY_VERSION",
    "semantic_addresses_for",
]
