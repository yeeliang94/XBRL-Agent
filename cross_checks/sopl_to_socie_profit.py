"""P0 Check 2: SOPL profit = SOCIE profit row.

The profit/(loss) reported on SOPL must appear identically in the SOCIE
matrix under the "Retained earnings" column (C) on the profit row.
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult
from cross_checks.util import open_workbook, find_sheet, find_value_by_label, socie_column


class SOPLToSOCIEProfitCheck:
    name = "sopl_to_socie_profit"
    required_statements = {StatementType.SOPL, StatementType.SOCIE}

    def applies_to(self, run_config: dict) -> bool:
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float) -> CrossCheckResult:
        # Read SOPL profit (CY = col B) — label is "*Profit (loss)" (not "for the period")
        sopl_wb = open_workbook(workbook_paths[StatementType.SOPL])
        sopl_ws = find_sheet(sopl_wb, "SOPL-Function", "SOPL-Nature")
        sopl_profit = None
        if sopl_ws is not None:
            sopl_profit = find_value_by_label(sopl_ws, "profit (loss)", col=2, wb=sopl_wb)
        sopl_wb.close()

        # Read SOCIE profit row — Retained earnings column (C=3) captures
        # the owners-attributable profit. When NCI exists, use Total column
        # (X=24) for the group figure that should match SOPL.
        socie_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        socie_ws = find_sheet(socie_wb, "SOCIE")
        socie_profit = None
        if socie_ws is not None:
            col = socie_column(socie_ws)
            socie_profit = find_value_by_label(socie_ws, "profit (loss)", col=col, wb=socie_wb)
        socie_wb.close()

        if sopl_profit is None or socie_profit is None:
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message=f"Could not find profit values: SOPL={sopl_profit}, SOCIE={socie_profit}",
            )

        diff = abs(sopl_profit - socie_profit)
        passed = diff <= tolerance

        return CrossCheckResult(
            name=self.name,
            status="passed" if passed else "failed",
            expected=sopl_profit,
            actual=socie_profit,
            diff=diff,
            tolerance=tolerance,
            message=(
                f"SOPL profit ({sopl_profit}) vs SOCIE profit row ({socie_profit}), "
                f"diff={diff:.2f}"
            ),
        )
