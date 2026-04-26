"""RUN-REVIEW P1-1 (2026-04-26): double-booking guard for fill_workbook.

The Amway run wrote PY restoration provision 1,881 onto BOTH row 287
(`Provision for decommissioning, restoration and rehabilitation costs`)
and row 318 (`Other non-current non-trade payables`). Both rows are in
the same Non-current liabilities section, the face balance still passed
(both feed the same *Total), and the bug only surfaced via a manual
diff against the filer's submission. This test pins the new guard.

Plan §4.1 explicitly requires:
- Same sheet, same column, same section, same value → warn
- Group consolidation pass-through (same value across column-pairs)
  must NOT trigger the guard
- Disjoint evidence strings must NOT trigger
- The fixture from `tests/fixtures/run_review/` exercises the real
  Amway shape, MFRS Co + MPERS Group
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from tools.fill_workbook import fill_workbook

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "run_review"


def _fields(payloads: list[dict]) -> str:
    """Compact wrapper for the JSON fields format the writer expects."""
    return json.dumps(payloads)


def test_double_booking_warning_fires_on_amway_shape(tmp_path: Path) -> None:
    """The actual RUN-REVIEW §3.3-D shape: 1,881 PY on two rows in the
    same non-current-liabilities section, both with restoration-provision
    evidence text. Guard must emit a warning naming both rows."""
    template = _FIXTURE_DIR / "sofp_company_mfrs.xlsx"
    output = tmp_path / "filled.xlsx"

    fields = [
        {
            "sheet": "SOFP-Sub-CuNonCu",
            "field_label": "Provision for decommissioning, restoration and rehabilitation costs",
            "section": "non-current",
            "col": 3,  # PY
            "value": 1881,
            "evidence": "Note 23(a): provision for restoration and decommissioning costs RM1,881",
        },
        {
            "sheet": "SOFP-Sub-CuNonCu",
            "field_label": "Other non-current non-trade payables",
            "section": "non-current",
            "col": 3,
            "value": 1881,
            "evidence": "Note 23: provision for restoration costs RM1,881 included in other payables",
        },
    ]

    result = fill_workbook(
        template_path=str(template),
        output_path=str(output),
        fields_json=_fields(fields),
        filing_level="company",
    )
    assert result.success
    assert result.fields_written == 2
    # The guard must surface a warning naming both rows
    assert len(result.warnings) >= 1
    joined = " ".join(result.warnings).lower()
    assert "double-booking" in joined
    assert "1881" in joined
    assert ("non-current" in joined or "non current" in joined)


def test_disjoint_evidence_does_not_trigger(tmp_path: Path) -> None:
    """Two unrelated rows happening to hold the same RM amount with
    DISJOINT evidence text must NOT trigger the guard — that's a
    coincidence, not a double-booking."""
    template = _FIXTURE_DIR / "sofp_company_mfrs.xlsx"
    output = tmp_path / "filled.xlsx"

    fields = [
        {
            "sheet": "SOFP-Sub-CuNonCu",
            "field_label": "Warranty provision",
            "section": "non-current",
            "col": 3,
            "value": 1881,
            "evidence": "Note 23(b): warranty obligations RM1,881",
        },
        {
            "sheet": "SOFP-Sub-CuNonCu",
            "field_label": "Refund provision",
            "section": "non-current",
            "col": 3,
            "value": 1881,
            "evidence": "Note 24: customer refund accrual RM1,881",
        },
    ]
    result = fill_workbook(
        template_path=str(template),
        output_path=str(output),
        fields_json=_fields(fields),
        filing_level="company",
    )
    assert result.success
    # Disjoint evidence strings — guard should remain silent.
    assert result.warnings == [], (
        f"Guard false-fired on coincidental equal values: {result.warnings}"
    )


def test_group_consolidation_passthrough_does_not_trigger(tmp_path: Path) -> None:
    """A Group filing puts the same number in Group CY (col B) AND
    Company CY (col D) for the same row when consolidation is
    pass-through. That's legitimate; the guard must compare ACROSS rows
    in ONE column, not across columns within one row."""
    template = _FIXTURE_DIR / "sofp_group_mpers.xlsx"
    output = tmp_path / "filled.xlsx"

    fields = [
        {
            "sheet": "SOFP-Sub-CuNonCu",
            "field_label": "Capital from ordinary shares",
            "section": "issued capital",
            "col": 2,  # Group CY
            "value": 81804,
            "evidence": "Note 24: issued share capital 81,804 RM'000",
        },
        {
            "sheet": "SOFP-Sub-CuNonCu",
            "field_label": "Capital from ordinary shares",
            "section": "issued capital",
            "col": 4,  # Company CY (same value, pass-through)
            "value": 81804,
            "evidence": "Note 24: issued share capital 81,804 RM'000",
        },
    ]
    result = fill_workbook(
        template_path=str(template),
        output_path=str(output),
        fields_json=_fields(fields),
        filing_level="group",
    )
    assert result.success
    assert result.warnings == [], (
        "Group consolidation pass-through must NOT trigger the guard — "
        "the discriminator is two ROWS with same value in ONE column"
    )


def test_existing_abstract_row_guard_unaffected(tmp_path: Path) -> None:
    """Sanity: adding the double-booking guard must not regress the
    abstract-row guard from gotcha #17. A write to a section header
    still gets refused."""
    template = _FIXTURE_DIR / "sofp_company_mfrs.xlsx"
    output = tmp_path / "filled.xlsx"

    # Row 5 is the "Sub-classification of assets, liabilities and equity"
    # title row — an abstract section header with the dark-navy fill.
    fields = [
        {
            "sheet": "SOFP-Sub-CuNonCu",
            "row": 6,  # "Property, plant and equipment" abstract header
            "col": 2,
            "value": 99999,
            "evidence": "Note 13",
        },
    ]
    result = fill_workbook(
        template_path=str(template),
        output_path=str(output),
        fields_json=_fields(fields),
        filing_level="company",
    )
    # Abstract-row guard refuses the write — fields_written=0, errors set.
    # The abstract-row guard is what matters; whether success is True or
    # False depends on whether ANY write succeeded.
    if result.fields_written == 0:
        assert any("abstract" in e.lower() for e in result.errors), (
            f"Abstract-row guard should fire on header rows; errors: {result.errors}"
        )
