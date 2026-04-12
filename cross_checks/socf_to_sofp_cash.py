"""P0 Check 5: SOCF closing cash = SOFP cash and cash equivalents.

The cash at end of period from SOCF must match the SOFP "Cash and cash
equivalents" line item. This is the tightest cross-statement reconciliation.
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult
from cross_checks.util import open_workbook, find_sheet, find_value_by_label


class SOCFToSOFPCashCheck:
    name = "socf_to_sofp_cash"
    required_statements = {StatementType.SOCF, StatementType.SOFP}

    def applies_to(self, run_config: dict) -> bool:
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float) -> CrossCheckResult:
        # Read SOCF closing cash (CY = col B)
        socf_wb = open_workbook(workbook_paths[StatementType.SOCF])
        socf_ws = find_sheet(socf_wb, "SOCF-Indirect", "SOCF-Direct")
        socf_cash = None
        if socf_ws is not None:
            socf_cash = find_value_by_label(
                socf_ws, "cash and cash equivalents at end of period", col=2, wb=socf_wb,
            )
        socf_wb.close()

        # Read SOFP cash (CY = col B). Label differs between variants:
        # CuNonCu uses "*Cash and cash equivalents"; OrdOfLiq uses
        # "Total Cash and bank balances" for the same semantic row.
        sofp_wb = open_workbook(workbook_paths[StatementType.SOFP])
        sofp_ws = find_sheet(sofp_wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        sofp_cash = None
        if sofp_ws is not None:
            sofp_cash = find_value_by_label(
                sofp_ws,
                ["cash and cash equivalents", "total cash and bank balances"],
                col=2,
                wb=sofp_wb,
            )
        sofp_wb.close()

        if socf_cash is None or sofp_cash is None:
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message=f"Could not find cash values: SOCF={socf_cash}, SOFP={sofp_cash}",
            )

        diff = abs(socf_cash - sofp_cash)
        passed = diff <= tolerance

        return CrossCheckResult(
            name=self.name,
            status="passed" if passed else "failed",
            expected=socf_cash,
            actual=sofp_cash,
            diff=diff,
            tolerance=tolerance,
            message=(
                f"SOCF closing cash ({socf_cash}) vs SOFP cash ({sofp_cash}), "
                f"diff={diff:.2f}"
            ),
        )
