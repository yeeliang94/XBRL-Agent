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
from cross_checks.framework import CrossCheckResult, Comparand
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label, is_sore_run,
)
from cross_checks._format import fmt_amount, fmt_diff


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
        sore_sheet = sore_ws.title if sore_ws is not None else "SoRE"
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
        sofp_sheet = sofp_ws.title if sofp_ws is not None else "SOFP"
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
            f"Group: SoRE ({fmt_amount(sore_re)}) vs SOFP ({fmt_amount(sofp_re)}), diff={fmt_diff(diff)}"
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
                    f"Company: SoRE ({fmt_amount(co_sore_re)}) vs SOFP ({fmt_amount(co_sofp_re)}), diff={fmt_diff(co_diff)}"
                )

        comparands = [
            Comparand(label="Retained earnings at end of period",
                      sheet=sore_sheet, value=sore_re, role="lhs",
                      statement=StatementType.SOCIE.value),
            Comparand(label="Retained earnings", sheet=sofp_sheet,
                      value=sofp_re, role="rhs",
                      statement=StatementType.SOFP.value),
        ]
        if filing_level == "group":
            comparands += [
                Comparand(label="Retained earnings at end of period [company]",
                          sheet=sore_sheet, value=co_sore_re, role="lhs",
                          statement=StatementType.SOCIE.value),
                Comparand(label="Retained earnings [company]", sheet=sofp_sheet,
                          value=co_sofp_re, role="rhs",
                          statement=StatementType.SOFP.value),
            ]

        return CrossCheckResult(
            name=self.name,
            status="passed" if group_passed and co_passed else "failed",
            expected=sore_re,
            actual=sofp_re,
            diff=diff,
            tolerance=tolerance,
            message="; ".join(parts),
            comparands=comparands,
        )

    def run_facts(self, ctx, tolerance: float) -> CrossCheckResult:
        """Fact-based twin of :meth:`run` (item 32). MPERS-only. SoRE closing
        retained earnings and SOFP retained earnings are both linear concepts."""
        from cross_checks.facts_util import primary_scope, read_labelled_value

        scope = primary_scope(ctx.filing_level)
        # SoRE lives under the SOCIE statement slot (the coordinator writes the
        # variant that ran to that key); read its template's facts.
        sore = read_labelled_value(
            ctx, StatementType.SOCIE, "retained earnings at end of period",
            "CY", scope)
        # Single-element candidate list → exact-match-first, mirroring the xlsx
        # path's deliberate avoidance of substring drift (peer-review I3).
        sofp = read_labelled_value(
            ctx, StatementType.SOFP, ["Retained earnings"], "CY", scope)
        sore_re, sofp_re = sore.value, sofp.value
        sore_sheet = sore.sheet or "SoRE"
        sofp_sheet = sofp.sheet or "SOFP"

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
        # The xlsx path hardcodes the "Group:" prefix here (it does not route
        # through filing_level_prefix) — mirror that verbatim for parity.
        parts = [f"Group: SoRE ({fmt_amount(sore_re)}) vs SOFP ({fmt_amount(sofp_re)}), diff={fmt_diff(diff)}"]

        co_sore_re = None
        co_sofp_re = None
        co_passed = True
        if ctx.filing_level == "group":
            co_sore_re = read_labelled_value(
                ctx, StatementType.SOCIE,
                "retained earnings at end of period", "CY", "Company").value
            co_sofp_re = read_labelled_value(
                ctx, StatementType.SOFP, ["Retained earnings"], "CY", "Company").value
            if co_sore_re is None or co_sofp_re is None:
                co_passed = False
                parts.append(
                    f"Company: missing retained earnings (SoRE={co_sore_re}, SOFP={co_sofp_re})"
                )
            else:
                co_diff = abs(co_sore_re - co_sofp_re)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company: SoRE ({fmt_amount(co_sore_re)}) vs SOFP ({fmt_amount(co_sofp_re)}), diff={fmt_diff(co_diff)}"
                )

        comparands = [
            Comparand(label="Retained earnings at end of period",
                      sheet=sore_sheet, value=sore_re, role="lhs",
                      statement=StatementType.SOCIE.value),
            Comparand(label="Retained earnings", sheet=sofp_sheet,
                      value=sofp_re, role="rhs",
                      statement=StatementType.SOFP.value),
        ]
        if ctx.filing_level == "group":
            comparands += [
                Comparand(label="Retained earnings at end of period [company]",
                          sheet=sore_sheet, value=co_sore_re, role="lhs",
                          statement=StatementType.SOCIE.value),
                Comparand(label="Retained earnings [company]", sheet=sofp_sheet,
                          value=co_sofp_re, role="rhs",
                          statement=StatementType.SOFP.value),
            ]

        return CrossCheckResult(
            name=self.name,
            status="passed" if group_passed and co_passed else "failed",
            expected=sore_re,
            actual=sofp_re,
            diff=diff,
            tolerance=tolerance,
            message="; ".join(parts),
            comparands=comparands,
        )
