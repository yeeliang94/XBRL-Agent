"""P0 Check 2: SOPL profit = SOCIE profit row.

The profit/(loss) reported on SOPL must appear identically in the SOCIE
matrix under the "Retained earnings" column (C) on the profit row.
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult, Comparand
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label, socie_column,
    find_value_in_block, SOCIE_GROUP_BLOCKS, is_sore_run, filing_level_prefix,
)
from cross_checks._format import fmt_amount, fmt_diff


class SOPLToSOCIEProfitCheck:
    name = "sopl_to_socie_profit"
    required_statements = {StatementType.SOPL, StatementType.SOCIE}

    def applies_to(self, run_config: dict) -> bool:
        # MPERS SoRE has no per-component matrix — it's a single retained-
        # earnings schedule. The companion SoREToSOFPRetainedEarningsCheck
        # handles the one reconciliation that still makes sense.
        return not is_sore_run(run_config)

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float, filing_level: str = "company", filing_standard: str = "mfrs") -> CrossCheckResult:
        sopl_wb = open_workbook(workbook_paths[StatementType.SOPL])
        sopl_ws = find_sheet(sopl_wb, "SOPL-Function", "SOPL-Nature")
        sopl_sheet = sopl_ws.title if sopl_ws is not None else "SOPL"
        sopl_profit = None
        co_sopl_profit = None
        if sopl_ws is not None:
            sopl_profit = find_value_by_label(sopl_ws, "profit (loss)", col=2, wb=sopl_wb)
            if filing_level == "group":
                co_sopl_profit = find_value_by_label(sopl_ws, "profit (loss)", col=4, wb=sopl_wb)
        sopl_wb.close()

        socie_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        socie_ws = find_sheet(socie_wb, "SOCIE")
        socie_sheet = socie_ws.title if socie_ws is not None else "SOCIE"
        socie_profit = None
        co_socie_profit = None
        if socie_ws is not None:
            # Phase 5: `socie_column` now honours filing_standard — see
            # socie_to_sofp_equity.py for the rationale.
            if filing_level == "group":
                blk = SOCIE_GROUP_BLOCKS["group_cy"]
                socie_profit = find_value_in_block(
                    socie_ws, "profit (loss)",
                    col=socie_column(
                        socie_ws, start_row=blk[0], end_row=blk[1],
                        filing_standard=filing_standard,
                    ),
                    start_row=blk[0], end_row=blk[1], wb=socie_wb,
                )
                co_blk = SOCIE_GROUP_BLOCKS["company_cy"]
                co_socie_profit = find_value_in_block(
                    socie_ws, "profit (loss)",
                    col=socie_column(
                        socie_ws, start_row=co_blk[0], end_row=co_blk[1],
                        filing_standard=filing_standard,
                    ),
                    start_row=co_blk[0], end_row=co_blk[1], wb=socie_wb,
                )
            else:
                col = socie_column(socie_ws, filing_standard=filing_standard)
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
        # Reconciliation check — period is implicit from the profit row
        # label so with_period=False. See cross_checks.util for the
        # S-3/S-4 rationale behind centralising this prefix.
        primary_label = filing_level_prefix(filing_level, with_period=False)
        parts = [f"{primary_label}: SOPL ({fmt_amount(sopl_profit)}) vs SOCIE ({fmt_amount(socie_profit)}), diff={fmt_diff(diff)}"]

        # Group filings must carry Company totals — see sofp_balance.py for
        # the peer-review background on the old silent-pass default.
        co_passed = True
        if filing_level == "group":
            if co_sopl_profit is None or co_socie_profit is None:
                co_passed = False
                parts.append(
                    f"Company: missing profit values (SOPL={co_sopl_profit}, SOCIE={co_socie_profit})"
                )
            else:
                co_diff = abs(co_sopl_profit - co_socie_profit)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company: SOPL ({fmt_amount(co_sopl_profit)}) vs SOCIE ({fmt_amount(co_socie_profit)}), diff={fmt_diff(co_diff)}"
                )

        passed = group_passed and co_passed

        comparands = [
            Comparand(label="Profit (loss)", sheet=sopl_sheet, value=sopl_profit,
                      role="lhs", statement=StatementType.SOPL.value),
            Comparand(label="Profit (loss)", sheet=socie_sheet,
                      value=socie_profit, role="rhs",
                      statement=StatementType.SOCIE.value),
        ]
        if filing_level == "group":
            comparands += [
                Comparand(label="Profit (loss) [company]", sheet=sopl_sheet,
                          value=co_sopl_profit, role="lhs",
                          statement=StatementType.SOPL.value),
                Comparand(label="Profit (loss) [company]", sheet=socie_sheet,
                          value=co_socie_profit, role="rhs",
                          statement=StatementType.SOCIE.value),
            ]

        return CrossCheckResult(
            name=self.name,
            status="passed" if passed else "failed",
            expected=sopl_profit,
            actual=socie_profit,
            diff=diff,
            tolerance=tolerance,
            message="; ".join(parts),
            comparands=comparands,
        )

    def run_facts(self, ctx, tolerance: float) -> CrossCheckResult:
        """Fact-based twin of :meth:`run` (item 32). SOPL profit is linear; the
        SOCIE profit cell is read from the Total column when NCI data is present,
        otherwise the Retained-earnings column (MFRS); MPERS reads col B. NCI
        presence is scoped per entity (Group vs Company) just like the xlsx
        per-block scan."""
        from cross_checks.facts_util import (
            primary_scope, read_labelled_value, read_matrix_value,
            socie_has_nci, socie_retained_col, socie_total_col,
        )
        from cross_checks.util import filing_level_prefix

        def _profit_col(scope: str) -> str:
            if socie_has_nci(ctx, StatementType.SOCIE, "CY", scope):
                return socie_total_col(ctx.filing_standard)
            return socie_retained_col(ctx.filing_standard)

        scope = primary_scope(ctx.filing_level)
        sopl = read_labelled_value(ctx, StatementType.SOPL, "profit (loss)", "CY", scope)
        socie = read_matrix_value(
            ctx, StatementType.SOCIE, "profit (loss)", _profit_col(scope), "CY", scope)
        sopl_profit, socie_profit = sopl.value, socie.value
        sopl_sheet = sopl.sheet or "SOPL"
        socie_sheet = socie.sheet or "SOCIE"

        if sopl_profit is None or socie_profit is None:
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message=f"Could not find profit values: SOPL={sopl_profit}, SOCIE={socie_profit}",
            )

        diff = abs(sopl_profit - socie_profit)
        group_passed = diff <= tolerance
        primary_label = filing_level_prefix(ctx.filing_level, with_period=False)
        parts = [f"{primary_label}: SOPL ({fmt_amount(sopl_profit)}) vs SOCIE ({fmt_amount(socie_profit)}), diff={fmt_diff(diff)}"]

        co_sopl_profit = None
        co_socie_profit = None
        co_passed = True
        if ctx.filing_level == "group":
            co_sopl_profit = read_labelled_value(
                ctx, StatementType.SOPL, "profit (loss)", "CY", "Company").value
            co_socie_profit = read_matrix_value(
                ctx, StatementType.SOCIE, "profit (loss)", _profit_col("Company"),
                "CY", "Company").value
            if co_sopl_profit is None or co_socie_profit is None:
                co_passed = False
                parts.append(
                    f"Company: missing profit values (SOPL={co_sopl_profit}, SOCIE={co_socie_profit})"
                )
            else:
                co_diff = abs(co_sopl_profit - co_socie_profit)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company: SOPL ({fmt_amount(co_sopl_profit)}) vs SOCIE ({fmt_amount(co_socie_profit)}), diff={fmt_diff(co_diff)}"
                )

        passed = group_passed and co_passed

        comparands = [
            Comparand(label="Profit (loss)", sheet=sopl_sheet, value=sopl_profit,
                      role="lhs", statement=StatementType.SOPL.value),
            Comparand(label="Profit (loss)", sheet=socie_sheet,
                      value=socie_profit, role="rhs",
                      statement=StatementType.SOCIE.value),
        ]
        if ctx.filing_level == "group":
            comparands += [
                Comparand(label="Profit (loss) [company]", sheet=sopl_sheet,
                          value=co_sopl_profit, role="lhs",
                          statement=StatementType.SOPL.value),
                Comparand(label="Profit (loss) [company]", sheet=socie_sheet,
                          value=co_socie_profit, role="rhs",
                          statement=StatementType.SOCIE.value),
            ]

        return CrossCheckResult(
            name=self.name,
            status="passed" if passed else "failed",
            expected=sopl_profit,
            actual=socie_profit,
            diff=diff,
            tolerance=tolerance,
            message="; ".join(parts),
            comparands=comparands,
        )
