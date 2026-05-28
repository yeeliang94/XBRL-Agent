"""Tests for monolith/coordinator.py — covers the bits that don't need
to drive a live LLM. The full mocked-agent run is delegated to a
manual e2e (split-pipeline parallel test pattern)."""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from monolith.coordinator import (
    MonolithRunConfig,
    _default_variant,
    _materialise_workbook,
    _snapshot_workbook,
    _statements_with_writes,
)
from statement_types import StatementType


_REPO = Path(__file__).resolve().parent.parent


def _config_company(tmp_path: Path) -> MonolithRunConfig:
    return MonolithRunConfig(
        pdf_path="",
        output_dir=str(tmp_path),
        model="stub",
        statements=set(StatementType),
        variants={},
        filing_level="company",
        filing_standard="mfrs",
    )


def test_materialise_workbook_concatenates_all_five_face_templates(tmp_path):
    config = _config_company(tmp_path)
    out = tmp_path / "monolith_filled.xlsx"
    _materialise_workbook(out, config)
    assert out.exists()
    wb = openpyxl.load_workbook(str(out), data_only=False)
    try:
        # SOCIE is the only sheet not prefix-matched — assert its presence.
        assert "SOCIE" in wb.sheetnames
        # At least one sheet per face statement type.
        for prefix in ("SOFP-", "SOPL-", "SOCI-", "SOCF-"):
            assert any(n.startswith(prefix) for n in wb.sheetnames), (
                f"expected at least one sheet matching prefix {prefix!r}"
            )
    finally:
        wb.close()


def test_materialise_workbook_is_idempotent(tmp_path):
    config = _config_company(tmp_path)
    out = tmp_path / "monolith_filled.xlsx"
    _materialise_workbook(out, config)
    mtime_first = out.stat().st_mtime_ns
    _materialise_workbook(out, config)  # second call should no-op
    assert out.stat().st_mtime_ns == mtime_first


def test_default_variant_picks_a_real_template(tmp_path):
    for stmt in (
        StatementType.SOFP,
        StatementType.SOPL,
        StatementType.SOCI,
        StatementType.SOCF,
        StatementType.SOCIE,
    ):
        variant = _default_variant(stmt, "mfrs")
        assert variant != "NotPrepared"
        assert variant


def test_statements_with_writes_empty_on_fresh_workbook(tmp_path):
    config = _config_company(tmp_path)
    out = tmp_path / "monolith_filled.xlsx"
    _materialise_workbook(out, config)
    assert _statements_with_writes(out, set(StatementType)) == []


def test_statements_with_writes_reports_after_value_lands(tmp_path):
    config = _config_company(tmp_path)
    out = tmp_path / "monolith_filled.xlsx"
    _materialise_workbook(out, config)
    # Plant a numeric value somewhere on SOFP-CuNonCu.
    wb = openpyxl.load_workbook(str(out), data_only=False)
    ws = wb["SOFP-CuNonCu"]
    # Find first data-entry row (col B empty, col A has a label).
    target_row = None
    for r in range(3, ws.max_row + 1):
        if ws.cell(row=r, column=1).value and ws.cell(row=r, column=2).value is None:
            target_row = r
            break
    assert target_row is not None
    ws.cell(row=target_row, column=2, value=100.0)
    wb.save(str(out))
    wb.close()
    assert StatementType.SOFP.value in _statements_with_writes(
        out, set(StatementType),
    )


def test_snapshot_workbook_does_not_raise_on_missing(tmp_path):
    # Should be a no-op when the file doesn't exist.
    _snapshot_workbook(tmp_path / "does-not-exist.xlsx")
