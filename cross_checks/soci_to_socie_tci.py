"""P0 Check 3: SOCI total comprehensive income = SOCIE TCI row.

The total comprehensive income reported on SOCI must appear identically
in the SOCIE matrix on the TCI row.
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult
from cross_checks.util import open_workbook, find_sheet, find_value_by_label


class SOCIToSOCIETCICheck:
    name = "soci_to_socie_tci"
    required_statements = {StatementType.SOCI, StatementType.SOCIE}

    def applies_to(self, run_config: dict) -> bool:
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float) -> CrossCheckResult:
        # Read SOCI TCI (CY = col B)
        # Real sheet name is "SOCI-BeforeOfTax" (not "SOCI-BeforeTax")
        soci_wb = open_workbook(workbook_paths[StatementType.SOCI])
        soci_ws = find_sheet(soci_wb, "SOCI-BeforeOfTax", "SOCI-BeforeTax", "SOCI-NetOfTax")
        soci_tci = None
        if soci_ws is not None:
            soci_tci = find_value_by_label(
                soci_ws, "total comprehensive income", col=2, wb=soci_wb,
            )
        soci_wb.close()

        # Read SOCIE TCI row — use Total column (X=24) when NCI present,
        # Retained earnings column (C=3) for single-entity companies.
        socie_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        socie_ws = find_sheet(socie_wb, "SOCIE")
        socie_tci = None
        if socie_ws is not None:
            # Always read from Total col (X=24) — the formula aggregates all
            # equity component columns, matching SOCI's group-level TCI.
            socie_tci = find_value_by_label(
                socie_ws, "total comprehensive income", col=24, wb=socie_wb,
            )
        socie_wb.close()

        if soci_tci is None or socie_tci is None:
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message=f"Could not find TCI values: SOCI={soci_tci}, SOCIE={socie_tci}",
            )

        diff = abs(soci_tci - socie_tci)
        passed = diff <= tolerance

        return CrossCheckResult(
            name=self.name,
            status="passed" if passed else "failed",
            expected=soci_tci,
            actual=socie_tci,
            diff=diff,
            tolerance=tolerance,
            message=(
                f"SOCI TCI ({soci_tci}) vs SOCIE TCI row ({socie_tci}), "
                f"diff={diff:.2f}"
            ),
        )
