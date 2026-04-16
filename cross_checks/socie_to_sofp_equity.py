"""P0 Check 4: SOCIE closing equity = SOFP total equity.

The closing balance (last row of CY block) in SOCIE column X (Total, col 24)
must match SOFP "Total equity" on the main sheet (col B).
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label,
    find_value_in_block, SOCIE_GROUP_BLOCKS,
)


class SOCIEToSOFPEquityCheck:
    name = "socie_to_sofp_equity"
    required_statements = {StatementType.SOCIE, StatementType.SOFP}

    def applies_to(self, run_config: dict) -> bool:
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float, filing_level: str = "company") -> CrossCheckResult:
        socie_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        socie_ws = find_sheet(socie_wb, "SOCIE")
        socie_equity = None
        co_socie_equity = None
        if socie_ws is not None:
            if filing_level == "group":
                blk = SOCIE_GROUP_BLOCKS["group_cy"]
                socie_equity = find_value_in_block(
                    socie_ws, "equity at end of period", col=24,
                    start_row=blk[0], end_row=blk[1], wb=socie_wb,
                )
                co_blk = SOCIE_GROUP_BLOCKS["company_cy"]
                co_socie_equity = find_value_in_block(
                    socie_ws, "equity at end of period", col=24,
                    start_row=co_blk[0], end_row=co_blk[1], wb=socie_wb,
                )
            else:
                socie_equity = find_value_by_label(socie_ws, "equity at end of period", col=24, wb=socie_wb)
        socie_wb.close()

        sofp_wb = open_workbook(workbook_paths[StatementType.SOFP])
        sofp_ws = find_sheet(sofp_wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        sofp_equity = None
        co_sofp_equity = None
        if sofp_ws is not None:
            sofp_equity = find_value_by_label(sofp_ws, "total equity", col=2, wb=sofp_wb)
            if filing_level == "group":
                co_sofp_equity = find_value_by_label(sofp_ws, "total equity", col=4, wb=sofp_wb)
        sofp_wb.close()

        if socie_equity is None or sofp_equity is None:
            return CrossCheckResult(
                name=self.name, status="failed",
                message=f"Could not find equity values: SOCIE={socie_equity}, SOFP={sofp_equity}",
            )

        diff = abs(socie_equity - sofp_equity)
        group_passed = diff <= tolerance
        parts = [f"Group: SOCIE ({socie_equity}) vs SOFP ({sofp_equity}), diff={diff:.2f}"]

        # Group filings must carry Company totals — see sofp_balance.py for
        # the peer-review background on the old silent-pass default.
        co_passed = True
        if filing_level == "group":
            if co_socie_equity is None or co_sofp_equity is None:
                co_passed = False
                parts.append(
                    f"Company: missing equity values (SOCIE={co_socie_equity}, SOFP={co_sofp_equity})"
                )
            else:
                co_diff = abs(co_socie_equity - co_sofp_equity)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company: SOCIE ({co_socie_equity}) vs SOFP ({co_sofp_equity}), diff={co_diff:.2f}"
                )

        return CrossCheckResult(
            name=self.name,
            status="passed" if group_passed and co_passed else "failed",
            expected=socie_equity, actual=sofp_equity, diff=diff, tolerance=tolerance,
            message="; ".join(parts),
        )
