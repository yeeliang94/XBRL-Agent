"""P0 Check 4: SOCIE closing equity = SOFP total equity.

The closing balance (last row of CY block) in SOCIE column X (Total, col 24)
must match SOFP "Total equity" on the main sheet (col B).
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult
from cross_checks.util import open_workbook, find_sheet, find_value_by_label


class SOCIEToSOFPEquityCheck:
    name = "socie_to_sofp_equity"
    required_statements = {StatementType.SOCIE, StatementType.SOFP}

    def applies_to(self, run_config: dict) -> bool:
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float) -> CrossCheckResult:
        # Read SOCIE closing equity — col X (24) on the closing balance row
        # Real label is "*Equity at end of period" (not "Balance at end of period")
        socie_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        socie_ws = find_sheet(socie_wb, "SOCIE")
        socie_equity = None
        if socie_ws is not None:
            socie_equity = find_value_by_label(
                socie_ws, "equity at end of period", col=24, wb=socie_wb,
            )
        socie_wb.close()

        # Read SOFP total equity (CY = col B)
        sofp_wb = open_workbook(workbook_paths[StatementType.SOFP])
        sofp_ws = find_sheet(sofp_wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        sofp_equity = None
        if sofp_ws is not None:
            sofp_equity = find_value_by_label(sofp_ws, "total equity", col=2, wb=sofp_wb)
        sofp_wb.close()

        if socie_equity is None or sofp_equity is None:
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message=f"Could not find equity values: SOCIE={socie_equity}, SOFP={sofp_equity}",
            )

        diff = abs(socie_equity - sofp_equity)
        passed = diff <= tolerance

        return CrossCheckResult(
            name=self.name,
            status="passed" if passed else "failed",
            expected=socie_equity,
            actual=sofp_equity,
            diff=diff,
            tolerance=tolerance,
            message=(
                f"SOCIE closing equity ({socie_equity}) vs SOFP total equity ({sofp_equity}), "
                f"diff={diff:.2f}"
            ),
        )
