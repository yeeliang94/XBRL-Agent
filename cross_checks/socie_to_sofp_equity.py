"""P0 Check 4: SOCIE closing equity = SOFP total equity.

The closing balance (last row of CY block) in SOCIE column X (Total, col 24)
must match SOFP "Total equity" on the main sheet (col B).
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult, Comparand
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label,
    find_value_in_block, find_row_sum_in_block, socie_component_columns,
    SOCIE_GROUP_BLOCKS, is_sore_run,
    socie_total_column, filing_level_prefix,
)


def _fmt_amount(value) -> str:
    """Human formatting for check messages: thousands separators, no
    trailing ``.0``. Run-168 QA finding: the raw f-string interpolation
    leaked ``SOCIE=None, SOFP=963391.0`` straight into the Needs-attention
    panel (which renders check messages verbatim)."""
    if value is None:
        return "not found"
    v = float(value)
    if v.is_integer():
        return f"{int(v):,}"
    return f"{v:,.2f}"


def _equity_not_found_clause(socie_equity, sofp_equity) -> str:
    """Plain-English body for the one-side-missing failure, shared by the
    xlsx and fact-based paths and by the Group/Company sub-clauses."""
    socie_part = (
        f"SOCIE closing equity is {_fmt_amount(socie_equity)}"
        if socie_equity is not None
        else "the SOCIE closing equity total ('Equity at end of period') was not filled in"
    )
    sofp_part = (
        f"SOFP total equity is {_fmt_amount(sofp_equity)}"
        if sofp_equity is not None
        else "the SOFP 'Total equity' figure was not filled in"
    )
    return f"{socie_part}; {sofp_part}"


class SOCIEToSOFPEquityCheck:
    name = "socie_to_sofp_equity"
    required_statements = {StatementType.SOCIE, StatementType.SOFP}

    def applies_to(self, run_config: dict) -> bool:
        # SoRE tracks retained earnings only, not total equity.
        # SoREToSOFPRetainedEarningsCheck carries the SoRE-era reconciliation.
        return not is_sore_run(run_config)

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float, filing_level: str = "company", filing_standard: str = "mfrs") -> CrossCheckResult:
        socie_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        socie_ws = find_sheet(socie_wb, "SOCIE")
        socie_sheet = socie_ws.title if socie_ws is not None else "SOCIE"
        socie_equity = None
        co_socie_equity = None
        if socie_ws is not None:
            # Phase 5: pick the read column per standard. Equity at end
            # of period is always a total across the dimensional axes
            # — on MFRS that's col X (24) unconditionally; on MPERS the
            # flat layout puts it in col B (2). `socie_total_column`
            # encapsulates the branch. NCI presence doesn't change the
            # column here (unlike the profit check, where retained-
            # earnings-only filings read col C).
            col = socie_total_column(filing_standard)
            comp_cols = socie_component_columns(filing_standard)
            if filing_level == "group":
                blk = SOCIE_GROUP_BLOCKS["group_cy"]
                socie_equity = find_value_in_block(
                    socie_ws, "equity at end of period", col=col,
                    start_row=blk[0], end_row=blk[1], wb=socie_wb,
                )
                if socie_equity is None:
                    # Apex Total blank — reconstruct from component columns
                    # (see run_facts for the full rationale).
                    socie_equity = find_row_sum_in_block(
                        socie_ws, "equity at end of period", comp_cols,
                        start_row=blk[0], end_row=blk[1], wb=socie_wb,
                    )
                co_blk = SOCIE_GROUP_BLOCKS["company_cy"]
                co_socie_equity = find_value_in_block(
                    socie_ws, "equity at end of period", col=col,
                    start_row=co_blk[0], end_row=co_blk[1], wb=socie_wb,
                )
                if co_socie_equity is None:
                    co_socie_equity = find_row_sum_in_block(
                        socie_ws, "equity at end of period", comp_cols,
                        start_row=co_blk[0], end_row=co_blk[1], wb=socie_wb,
                    )
            else:
                socie_equity = find_value_by_label(
                    socie_ws, "equity at end of period", col=col, wb=socie_wb,
                )
                if socie_equity is None:
                    socie_equity = find_row_sum_in_block(
                        socie_ws, "equity at end of period", comp_cols,
                        start_row=1, end_row=socie_ws.max_row, wb=socie_wb,
                    )
        socie_wb.close()

        sofp_wb = open_workbook(workbook_paths[StatementType.SOFP])
        sofp_ws = find_sheet(sofp_wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        sofp_sheet = sofp_ws.title if sofp_ws is not None else "SOFP"
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
                message="Couldn't compare equity totals: "
                        f"{_equity_not_found_clause(socie_equity, sofp_equity)}.",
            )

        diff = abs(socie_equity - sofp_equity)
        group_passed = diff <= tolerance
        # Reconciliation check — see cross_checks.util.filing_level_prefix.
        primary_label = filing_level_prefix(filing_level, with_period=False)
        parts = [f"{primary_label}: SOCIE ({socie_equity}) vs SOFP ({sofp_equity}), diff={diff:.2f}"]

        # Group filings must carry Company totals — see sofp_balance.py for
        # the peer-review background on the old silent-pass default.
        co_passed = True
        if filing_level == "group":
            if co_socie_equity is None or co_sofp_equity is None:
                co_passed = False
                parts.append(
                    "Company: couldn't compare equity totals — "
                    f"{_equity_not_found_clause(co_socie_equity, co_sofp_equity)}"
                )
            else:
                co_diff = abs(co_socie_equity - co_sofp_equity)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company: SOCIE ({co_socie_equity}) vs SOFP ({co_sofp_equity}), diff={co_diff:.2f}"
                )

        comparands = [
            Comparand(label="Equity at end of period", sheet=socie_sheet,
                      value=socie_equity, role="lhs",
                      statement=StatementType.SOCIE.value),
            Comparand(label="Total equity", sheet=sofp_sheet, value=sofp_equity,
                      role="rhs", statement=StatementType.SOFP.value),
        ]
        if filing_level == "group":
            comparands += [
                Comparand(label="Equity at end of period [company]",
                          sheet=socie_sheet, value=co_socie_equity, role="lhs",
                          statement=StatementType.SOCIE.value),
                Comparand(label="Total equity [company]", sheet=sofp_sheet,
                          value=co_sofp_equity, role="rhs",
                          statement=StatementType.SOFP.value),
            ]

        return CrossCheckResult(
            name=self.name,
            status="passed" if group_passed and co_passed else "failed",
            expected=socie_equity, actual=sofp_equity, diff=diff, tolerance=tolerance,
            message="; ".join(parts),
            comparands=comparands,
        )

    def run_facts(self, ctx, tolerance: float) -> CrossCheckResult:
        """Fact-based twin of :meth:`run` (item 32). SOCIE equity-at-end is the
        matrix Total column (MFRS X / MPERS B); SOFP total equity is linear."""
        from cross_checks.facts_util import (
            primary_scope, read_labelled_value, read_matrix_value,
            read_matrix_row_sum, socie_component_cols, socie_total_col,
        )
        from cross_checks.util import filing_level_prefix

        scope = primary_scope(ctx.filing_level)
        col = socie_total_col(ctx.filing_standard)
        socie = read_matrix_value(
            ctx, StatementType.SOCIE, "equity at end of period", col, "CY", scope)
        if socie.value is None:
            # Apex Total (col X) not materialised by the cascade — reconstruct
            # closing equity from the per-component columns, which resolve one
            # hop above the agent's leaves (run-168: empty reserve columns left
            # X blank though every real component closing was present).
            socie = read_matrix_row_sum(
                ctx, StatementType.SOCIE, "equity at end of period",
                socie_component_cols(ctx.filing_standard), "CY", scope)
        sofp = read_labelled_value(ctx, StatementType.SOFP, "total equity", "CY", scope)
        socie_equity, sofp_equity = socie.value, sofp.value
        socie_sheet = socie.sheet or "SOCIE"
        sofp_sheet = sofp.sheet or "SOFP"

        if socie_equity is None or sofp_equity is None:
            return CrossCheckResult(
                name=self.name, status="failed",
                message="Couldn't compare equity totals: "
                        f"{_equity_not_found_clause(socie_equity, sofp_equity)}.",
            )

        diff = abs(socie_equity - sofp_equity)
        group_passed = diff <= tolerance
        primary_label = filing_level_prefix(ctx.filing_level, with_period=False)
        parts = [f"{primary_label}: SOCIE ({socie_equity}) vs SOFP ({sofp_equity}), diff={diff:.2f}"]

        co_socie_equity = None
        co_sofp_equity = None
        co_passed = True
        if ctx.filing_level == "group":
            co_socie = read_matrix_value(
                ctx, StatementType.SOCIE, "equity at end of period", col,
                "CY", "Company")
            if co_socie.value is None:
                co_socie = read_matrix_row_sum(
                    ctx, StatementType.SOCIE, "equity at end of period",
                    socie_component_cols(ctx.filing_standard), "CY", "Company")
            co_socie_equity = co_socie.value
            co_sofp_equity = read_labelled_value(
                ctx, StatementType.SOFP, "total equity", "CY", "Company").value
            if co_socie_equity is None or co_sofp_equity is None:
                co_passed = False
                parts.append(
                    "Company: couldn't compare equity totals — "
                    f"{_equity_not_found_clause(co_socie_equity, co_sofp_equity)}"
                )
            else:
                co_diff = abs(co_socie_equity - co_sofp_equity)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company: SOCIE ({co_socie_equity}) vs SOFP ({co_sofp_equity}), diff={co_diff:.2f}"
                )

        comparands = [
            Comparand(label="Equity at end of period", sheet=socie_sheet,
                      value=socie_equity, role="lhs",
                      statement=StatementType.SOCIE.value),
            Comparand(label="Total equity", sheet=sofp_sheet, value=sofp_equity,
                      role="rhs", statement=StatementType.SOFP.value),
        ]
        if ctx.filing_level == "group":
            comparands += [
                Comparand(label="Equity at end of period [company]",
                          sheet=socie_sheet, value=co_socie_equity, role="lhs",
                          statement=StatementType.SOCIE.value),
                Comparand(label="Total equity [company]", sheet=sofp_sheet,
                          value=co_sofp_equity, role="rhs",
                          statement=StatementType.SOFP.value),
            ]

        return CrossCheckResult(
            name=self.name,
            status="passed" if group_passed and co_passed else "failed",
            expected=socie_equity, actual=sofp_equity, diff=diff, tolerance=tolerance,
            message="; ".join(parts),
            comparands=comparands,
        )
