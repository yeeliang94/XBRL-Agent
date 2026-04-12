"""P0 Check 3: SOCI total comprehensive income = SOCIE TCI row.

The total comprehensive income reported on SOCI must appear identically
in the SOCIE matrix on the TCI row.
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label,
    find_value_in_block, SOCIE_GROUP_BLOCKS,
)


class SOCIToSOCIETCICheck:
    name = "soci_to_socie_tci"
    required_statements = {StatementType.SOCI, StatementType.SOCIE}

    def applies_to(self, run_config: dict) -> bool:
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float, filing_level: str = "company") -> CrossCheckResult:
        soci_wb = open_workbook(workbook_paths[StatementType.SOCI])
        soci_ws = find_sheet(soci_wb, "SOCI-BeforeOfTax", "SOCI-BeforeTax", "SOCI-NetOfTax")
        soci_tci = None
        co_soci_tci = None
        if soci_ws is not None:
            soci_tci = find_value_by_label(soci_ws, "total comprehensive income", col=2, wb=soci_wb)
            if filing_level == "group":
                co_soci_tci = find_value_by_label(soci_ws, "total comprehensive income", col=4, wb=soci_wb)
        soci_wb.close()

        socie_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        socie_ws = find_sheet(socie_wb, "SOCIE")
        socie_tci = None
        co_socie_tci = None
        if socie_ws is not None:
            if filing_level == "group":
                blk = SOCIE_GROUP_BLOCKS["group_cy"]
                socie_tci = find_value_in_block(
                    socie_ws, "total comprehensive income", col=24,
                    start_row=blk[0], end_row=blk[1], wb=socie_wb,
                )
                co_blk = SOCIE_GROUP_BLOCKS["company_cy"]
                co_socie_tci = find_value_in_block(
                    socie_ws, "total comprehensive income", col=24,
                    start_row=co_blk[0], end_row=co_blk[1], wb=socie_wb,
                )
            else:
                socie_tci = find_value_by_label(socie_ws, "total comprehensive income", col=24, wb=socie_wb)
        socie_wb.close()

        if soci_tci is None or socie_tci is None:
            return CrossCheckResult(
                name=self.name, status="failed",
                message=f"Could not find TCI values: SOCI={soci_tci}, SOCIE={socie_tci}",
            )

        diff = abs(soci_tci - socie_tci)
        group_passed = diff <= tolerance
        parts = [f"Group: SOCI ({soci_tci}) vs SOCIE ({socie_tci}), diff={diff:.2f}"]

        co_passed = True
        if filing_level == "group" and co_soci_tci is not None and co_socie_tci is not None:
            co_diff = abs(co_soci_tci - co_socie_tci)
            co_passed = co_diff <= tolerance
            parts.append(f"Company: SOCI ({co_soci_tci}) vs SOCIE ({co_socie_tci}), diff={co_diff:.2f}")

        return CrossCheckResult(
            name=self.name,
            status="passed" if group_passed and co_passed else "failed",
            expected=soci_tci, actual=socie_tci, diff=diff, tolerance=tolerance,
            message="; ".join(parts),
        )
