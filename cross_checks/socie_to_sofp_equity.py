"""P0 Check 4: SOCIE closing equity = SOFP equity across dimensions."""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult, Comparand
from cross_checks.periods import PeriodEvaluation, combine_period_evaluations, period_scopes
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label, find_value_in_block,
    find_row_sum_in_block, socie_component_columns, socie_period_block,
    socie_period_column, socie_total_column, is_sore_run,
)
from cross_checks._format import fmt_amount, fmt_diff


def _display(value):
    if value is None:
        return "not found"
    number = float(value)
    return f"{int(number):,}" if number.is_integer() else f"{number:,.2f}"


def _missing_clause(socie, sofp):
    left = (
        f"SOCIE closing equity is {_display(socie)}" if socie is not None
        else "the SOCIE closing equity total ('Equity at end of period') was not filled in"
    )
    right = (
        f"SOFP total equity is {_display(sofp)}" if sofp is not None
        else "the SOFP 'Total equity' figure was not filled in"
    )
    return f"{left}; {right}"


def _message(spec, socie, sofp):
    if socie is None and sofp is None and spec.period == "PY":
        return f"{spec.label}: not checked (both equity values absent)"
    if socie is None or sofp is None:
        return f"{spec.label}: couldn't compare equity totals — {_missing_clause(socie, sofp)}"
    return (f"{spec.label}: SOCIE ({fmt_amount(socie)}) vs SOFP ({fmt_amount(sofp)}), "
            f"diff={fmt_diff(abs(socie - sofp))}")


def _evaluation(spec, socie, sofp, socie_sheet, sofp_sheet, filing_level):
    suffix = " [company]" if filing_level == "group" and spec.entity_scope == "Company" else ""
    return PeriodEvaluation(
        spec=spec, lhs=socie.value, rhs=sofp.value,
        message=_message(spec, socie.value, sofp.value),
        comparands=[
            Comparand(label=f"Equity at end of period{suffix}", sheet=socie_sheet,
                      value=socie.value, role="lhs", statement=StatementType.SOCIE.value,
                      period=spec.period),
            Comparand(label=f"Total equity{suffix}", sheet=sofp_sheet,
                      value=sofp.value, role="rhs", statement=StatementType.SOFP.value,
                      period=spec.period),
        ],
    )


class _Value:
    def __init__(self, value, sheet, row=None):
        self.value, self.sheet, self.row = value, sheet, row


class SOCIEToSOFPEquityCheck:
    name = "socie_to_sofp_equity"
    required_statements = {StatementType.SOCIE, StatementType.SOFP}

    def applies_to(self, run_config: dict) -> bool:
        return not is_sore_run(run_config)

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float,
            filing_level: str = "company", filing_standard: str = "mfrs") -> CrossCheckResult:
        socie_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        socie_ws = find_sheet(socie_wb, "SOCIE")
        sofp_wb = open_workbook(workbook_paths[StatementType.SOFP])
        sofp_ws = find_sheet(sofp_wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        if socie_ws is None or sofp_ws is None:
            socie_wb.close(); sofp_wb.close()
            return CrossCheckResult(name=self.name, status="failed",
                                    message="Could not find SOCIE or SOFP main sheet")

        evaluations = []
        for spec in period_scopes(filing_level):
            block = socie_period_block(
                filing_standard, filing_level, spec.entity_scope, spec.period)
            col = socie_period_column(
                filing_standard, filing_level, spec.period,
                socie_total_column(filing_standard))
            component_cols = (
                [col] if filing_standard == "mpers"
                else socie_component_columns(filing_standard)
            )
            if block is None:
                socie_value = find_value_by_label(
                    socie_ws, "equity at end of period", col=col, wb=socie_wb,
                    blank_formula_as_none=spec.period == "PY")
                if socie_value is None:
                    socie_value = find_row_sum_in_block(
                        socie_ws, "equity at end of period", component_cols,
                        1, socie_ws.max_row, wb=socie_wb,
                        blank_formula_as_none=spec.period == "PY")
            else:
                socie_value = find_value_in_block(
                    socie_ws, "equity at end of period", col,
                    block[0], block[1], wb=socie_wb,
                    blank_formula_as_none=spec.period == "PY")
                if socie_value is None:
                    socie_value = find_row_sum_in_block(
                        socie_ws, "equity at end of period", component_cols,
                        block[0], block[1], wb=socie_wb,
                        blank_formula_as_none=spec.period == "PY")
                if socie_value is None and filing_level == "company" and spec.period == "CY":
                    socie_value = find_value_by_label(
                        socie_ws, "equity at end of period", col=col, wb=socie_wb)
                    if socie_value is None:
                        socie_value = find_row_sum_in_block(
                            socie_ws, "equity at end of period", component_cols,
                            1, socie_ws.max_row, wb=socie_wb)
            socie = _Value(socie_value, socie_ws.title)
            sofp = _Value(find_value_by_label(
                sofp_ws, "total equity", col=spec.column, wb=sofp_wb,
                blank_formula_as_none=spec.period == "PY"), sofp_ws.title)
            evaluations.append(_evaluation(
                spec, socie, sofp, socie_ws.title, sofp_ws.title, filing_level))
        socie_wb.close(); sofp_wb.close()
        return combine_period_evaluations(self.name, evaluations, tolerance)

    def run_facts(self, ctx, tolerance: float) -> CrossCheckResult:
        from cross_checks.facts_util import (
            read_labelled_value, read_matrix_value, read_matrix_row_sum,
            socie_component_cols, socie_period_col, socie_total_col,
        )
        evaluations = []
        for spec in period_scopes(ctx.filing_level):
            col = socie_period_col(
                ctx.filing_standard, ctx.filing_level, spec.period,
                socie_total_col(ctx.filing_standard))
            socie = read_matrix_value(
                ctx, StatementType.SOCIE, "equity at end of period", col,
                spec.period, spec.entity_scope)
            if socie.value is None:
                component_cols = (
                    [col] if ctx.filing_standard == "mpers"
                    else socie_component_cols(ctx.filing_standard)
                )
                socie = read_matrix_row_sum(
                    ctx, StatementType.SOCIE, "equity at end of period",
                    component_cols, spec.period, spec.entity_scope)
            sofp = read_labelled_value(
                ctx, StatementType.SOFP, "total equity",
                spec.period, spec.entity_scope)
            evaluations.append(_evaluation(
                spec, socie, sofp, socie.sheet or "SOCIE", sofp.sheet or "SOFP",
                ctx.filing_level))
        return combine_period_evaluations(self.name, evaluations, tolerance)
