"""P0 Check 2: SOPL profit = SOCIE profit row.

The profit/(loss) reported on SOPL must appear identically in the SOCIE
matrix under the "Retained earnings" column (C) on the profit row.
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label, socie_column,
    find_value_in_block, SOCIE_GROUP_BLOCKS,
)


class SOPLToSOCIEProfitCheck:
    name = "sopl_to_socie_profit"
    required_statements = {StatementType.SOPL, StatementType.SOCIE}

    def applies_to(self, run_config: dict) -> bool:
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float, filing_level: str = "company") -> CrossCheckResult:
        sopl_wb = open_workbook(workbook_paths[StatementType.SOPL])
        sopl_ws = find_sheet(sopl_wb, "SOPL-Function", "SOPL-Nature")
        sopl_profit = None
        co_sopl_profit = None
        if sopl_ws is not None:
            sopl_profit = find_value_by_label(sopl_ws, "profit (loss)", col=2, wb=sopl_wb)
            if filing_level == "group":
                co_sopl_profit = find_value_by_label(sopl_ws, "profit (loss)", col=4, wb=sopl_wb)
        sopl_wb.close()

        socie_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        socie_ws = find_sheet(socie_wb, "SOCIE")
        socie_profit = None
        co_socie_profit = None
        if socie_ws is not None:
            if filing_level == "group":
                blk = SOCIE_GROUP_BLOCKS["group_cy"]
                socie_profit = find_value_in_block(
                    socie_ws, "profit (loss)", col=socie_column(socie_ws, start_row=blk[0], end_row=blk[1]),
                    start_row=blk[0], end_row=blk[1], wb=socie_wb,
                )
                co_blk = SOCIE_GROUP_BLOCKS["company_cy"]
                co_socie_profit = find_value_in_block(
                    socie_ws, "profit (loss)", col=socie_column(socie_ws, start_row=co_blk[0], end_row=co_blk[1]),
                    start_row=co_blk[0], end_row=co_blk[1], wb=socie_wb,
                )
            else:
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
        group_passed = diff <= tolerance
        parts = [f"Group: SOPL ({sopl_profit}) vs SOCIE ({socie_profit}), diff={diff:.2f}"]

        co_passed = True
        if filing_level == "group" and co_sopl_profit is not None and co_socie_profit is not None:
            co_diff = abs(co_sopl_profit - co_socie_profit)
            co_passed = co_diff <= tolerance
            parts.append(f"Company: SOPL ({co_sopl_profit}) vs SOCIE ({co_socie_profit}), diff={co_diff:.2f}")

        passed = group_passed and co_passed

        return CrossCheckResult(
            name=self.name,
            status="passed" if passed else "failed",
            expected=sopl_profit,
            actual=socie_profit,
            diff=diff,
            tolerance=tolerance,
            message="; ".join(parts),
        )
