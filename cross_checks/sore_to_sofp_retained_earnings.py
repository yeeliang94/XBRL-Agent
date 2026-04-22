"""MPERS P0 Check: SoRE closing retained earnings = SOFP retained earnings.

SoRE (Statement of Retained Earnings) replaces the full SOCIE matrix on MPERS
filings. After the SoRE variant gate disables the three SOCIE-consuming checks
(sopl_to_socie_profit / soci_to_socie_tci / socie_to_sofp_equity), this is the
only reconciliation that ties the equity movement back to the balance sheet.

Only the single retained-earnings line is checked — SoRE is a single-column
schedule by design, so a per-component reconciliation isn't meaningful.
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label, is_sore_run,
)


class SoREToSOFPRetainedEarningsCheck:
    name = "sore_to_sofp_retained_earnings"
    required_statements = {StatementType.SOCIE, StatementType.SOFP}
    # Framework short-circuits this check to not_applicable on MFRS runs — SoRE
    # doesn't exist there and the SOCIE workbook will carry the regular matrix.
    applies_to_standard = frozenset({"mpers"})

    def applies_to(self, run_config: dict) -> bool:
        # Only fire when the SOCIE slot is actually the SoRE variant. Regular
        # MPERS SOCIE runs still use the matrix-based checks.
        return is_sore_run(run_config)

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float, filing_level: str = "company") -> CrossCheckResult:
        # SoRE workbook lives under the SOCIE slot — the coordinator writes
        # whichever variant ran to that key.
        sore_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        sore_ws = find_sheet(sore_wb, "SoRE")
        sore_re = None
        co_sore_re = None
        if sore_ws is not None:
            sore_re = find_value_by_label(
                sore_ws, "retained earnings at end of period", col=2, wb=sore_wb,
            )
            if filing_level == "group":
                co_sore_re = find_value_by_label(
                    sore_ws, "retained earnings at end of period", col=4, wb=sore_wb,
                )
        sore_wb.close()

        sofp_wb = open_workbook(workbook_paths[StatementType.SOFP])
        sofp_ws = find_sheet(sofp_wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        sofp_re = None
        co_sofp_re = None
        if sofp_ws is not None:
            # Pass as a single-element list so `find_value_by_label` uses its
            # exact-match-first branch (`cross_checks/util.py:161`) rather
            # than a substring scan. The MPERS template has exactly one row
            # literally labelled "Retained earnings"; substring matching
            # would silently pick up variants like "Cumulative retained
            # earnings reserve" if a future template gains one (peer-review
            # I3).
            sofp_re = find_value_by_label(sofp_ws, ["Retained earnings"], col=2, wb=sofp_wb)
            if filing_level == "group":
                co_sofp_re = find_value_by_label(sofp_ws, ["Retained earnings"], col=4, wb=sofp_wb)
        sofp_wb.close()

        if sore_re is None or sofp_re is None:
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message=(
                    f"Could not find retained earnings values: "
                    f"SoRE={sore_re}, SOFP={sofp_re}"
                ),
            )

        diff = abs(sore_re - sofp_re)
        group_passed = diff <= tolerance
        parts = [
            f"Group: SoRE ({sore_re}) vs SOFP ({sofp_re}), diff={diff:.2f}"
        ]

        # Group filings must also line up on Company CY — mirrors the dual-
        # column handling in sofp_balance.py / socie_to_sofp_equity.py.
        co_passed = True
        if filing_level == "group":
            if co_sore_re is None or co_sofp_re is None:
                co_passed = False
                parts.append(
                    f"Company: missing retained earnings (SoRE={co_sore_re}, SOFP={co_sofp_re})"
                )
            else:
                co_diff = abs(co_sore_re - co_sofp_re)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company: SoRE ({co_sore_re}) vs SOFP ({co_sofp_re}), diff={co_diff:.2f}"
                )

        return CrossCheckResult(
            name=self.name,
            status="passed" if group_passed and co_passed else "failed",
            expected=sore_re,
            actual=sofp_re,
            diff=diff,
            tolerance=tolerance,
            message="; ".join(parts),
        )
