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

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float, filing_level: str = "company") -> CrossCheckResult:
        path = workbook_paths[StatementType.SOFP]
        wb = open_workbook(path)

        ws = find_sheet(wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        if ws is None:
            wb.close()
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message="No SOFP main sheet found in workbook",
            )

        assets_cy = find_value_by_label(ws, "total assets", col=2, wb=wb)
        eq_liab_cy = find_value_by_label(ws, "total equity and liabilities", col=2, wb=wb)

        # Group filing: also read Company columns (D=4)
        co_assets_cy = None
        co_eq_liab_cy = None
        if filing_level == "group":
            co_assets_cy = find_value_by_label(ws, "total assets", col=4, wb=wb)
            co_eq_liab_cy = find_value_by_label(ws, "total equity and liabilities", col=4, wb=wb)

        wb.close()

        if assets_cy is None or eq_liab_cy is None:
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message=f"Could not find total rows: assets={assets_cy}, equity+liab={eq_liab_cy}",
            )

        diff = abs(assets_cy - eq_liab_cy)
        group_passed = diff <= tolerance
        parts = [f"Group CY: assets ({assets_cy}) vs equity+liab ({eq_liab_cy}), diff={diff:.2f}"]

        co_passed = True
        if filing_level == "group" and co_assets_cy is not None and co_eq_liab_cy is not None:
            co_diff = abs(co_assets_cy - co_eq_liab_cy)
            co_passed = co_diff <= tolerance
            parts.append(f"Company CY: assets ({co_assets_cy}) vs equity+liab ({co_eq_liab_cy}), diff={co_diff:.2f}")

        passed = group_passed and co_passed

        return CrossCheckResult(
            name=self.name,
            status="passed" if passed else "failed",
            expected=assets_cy,
            actual=eq_liab_cy,
            diff=diff,
            tolerance=tolerance,
            message="; ".join(parts),
        )
