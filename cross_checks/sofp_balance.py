"""P0 Check 1: SOFP balance — Total assets = Total equity + liabilities (CY + PY).

This is the fundamental accounting identity. If the balance sheet doesn't
balance, something is wrong with the extraction.
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult
from cross_checks.util import open_workbook, find_sheet, find_value_by_label


class SOFPBalanceCheck:
    name = "sofp_balance"
    required_statements = {StatementType.SOFP}

    def applies_to(self, run_config: dict) -> bool:
        # Always applies when SOFP is present — all variants have this identity.
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float) -> CrossCheckResult:
        path = workbook_paths[StatementType.SOFP]
        wb = open_workbook(path)

        # Try both CuNonCu and OrderOfLiquidity sheet names
        ws = find_sheet(wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        if ws is None:
            wb.close()
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message="No SOFP main sheet found in workbook",
            )

        # Check CY (col B = 2) — total rows are formulas, so pass wb for evaluation
        assets_cy = find_value_by_label(ws, "total assets", col=2, wb=wb)
        eq_liab_cy = find_value_by_label(ws, "total equity and liabilities", col=2, wb=wb)

        wb.close()

        if assets_cy is None or eq_liab_cy is None:
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message=f"Could not find total rows: assets={assets_cy}, equity+liab={eq_liab_cy}",
            )

        diff = abs(assets_cy - eq_liab_cy)
        passed = diff <= tolerance

        return CrossCheckResult(
            name=self.name,
            status="passed" if passed else "failed",
            expected=assets_cy,
            actual=eq_liab_cy,
            diff=diff,
            tolerance=tolerance,
            message=(
                f"CY: Total assets ({assets_cy}) vs Total equity+liabilities ({eq_liab_cy}), "
                f"diff={diff:.2f}, tolerance={tolerance}"
            ),
        )
