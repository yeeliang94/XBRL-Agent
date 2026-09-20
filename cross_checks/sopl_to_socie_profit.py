"""P0 Check 2: SOPL profit = SOCIE profit across populated dimensions."""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult, Comparand
from cross_checks.periods import PeriodEvaluation, combine_period_evaluations, period_scopes
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label, find_value_in_block,
    socie_column, socie_period_block, socie_period_column, is_sore_run,
)
from cross_checks._format import fmt_amount, fmt_diff


def _message(spec, sopl, socie):
    if sopl is None and socie is None and spec.period == "PY":
        return f"{spec.label}: not checked (both profit values absent)"
    if sopl is None or socie is None:
        return f"{spec.label}: missing profit values (SOPL={sopl}, SOCIE={socie})"
    return (f"{spec.label}: SOPL ({fmt_amount(sopl)}) vs SOCIE ({fmt_amount(socie)}), "
            f"diff={fmt_diff(abs(sopl - socie))}")


def _evaluation(spec, sopl, socie, sopl_sheet, socie_sheet, filing_level):
    suffix = " [company]" if filing_level == "group" and spec.entity_scope == "Company" else ""
    return PeriodEvaluation(
        spec=spec, lhs=sopl.value, rhs=socie.value,
        message=_message(spec, sopl.value, socie.value),
        comparands=[
            Comparand(label=f"Profit (loss){suffix}", sheet=sopl_sheet,
                      value=sopl.value, role="lhs", statement=StatementType.SOPL.value,
                      period=spec.period),
            Comparand(label=f"Profit (loss){suffix}", sheet=socie_sheet,
                      value=socie.value, role="rhs", statement=StatementType.SOCIE.value,
                      period=spec.period),
        ],
    )


class _Value:
    def __init__(self, value, sheet):
        self.value, self.sheet, self.row = value, sheet, None


class SOPLToSOCIEProfitCheck:
    name = "sopl_to_socie_profit"
    required_statements = {StatementType.SOPL, StatementType.SOCIE}

    def applies_to(self, run_config: dict) -> bool:
        return not is_sore_run(run_config)

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float,
            filing_level: str = "company", filing_standard: str = "mfrs") -> CrossCheckResult:
        sopl_wb = open_workbook(workbook_paths[StatementType.SOPL])
        sopl_ws = find_sheet(sopl_wb, "SOPL-Function", "SOPL-Nature")
        socie_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        socie_ws = find_sheet(socie_wb, "SOCIE")
        if sopl_ws is None or socie_ws is None:
            sopl_wb.close(); socie_wb.close()
            return CrossCheckResult(name=self.name, status="failed",
                                    message="Could not find SOPL or SOCIE main sheet")
        evaluations = []
        for spec in period_scopes(filing_level):
            sopl = _Value(find_value_by_label(
                sopl_ws, "profit (loss)", col=spec.column, wb=sopl_wb,
                blank_formula_as_none=spec.period == "PY"), sopl_ws.title)
            block = socie_period_block(
                filing_standard, filing_level, spec.entity_scope, spec.period)
            if filing_standard == "mpers":
                base_col = 2
            elif block is None:
                base_col = socie_column(socie_ws, filing_standard=filing_standard)
            else:
                base_col = socie_column(
                    socie_ws, start_row=block[0], end_row=block[1],
                    filing_standard=filing_standard)
            col = socie_period_column(
                filing_standard, filing_level, spec.period, base_col)
            if block is None:
                value = find_value_by_label(
                    socie_ws, "profit (loss)", col=col, wb=socie_wb,
                    blank_formula_as_none=spec.period == "PY")
            else:
                value = find_value_in_block(
                    socie_ws, "profit (loss)", col,
                    block[0], block[1], wb=socie_wb,
                    blank_formula_as_none=spec.period == "PY")
                # Small synthetic/legacy Company fixtures may omit the
                # presentation padding before the CY block.  Preserve the
                # former whole-sheet CY lookup without ever using it for PY.
                if value is None and filing_level == "company" and spec.period == "CY":
                    value = find_value_by_label(
                        socie_ws, "profit (loss)", col=col, wb=socie_wb)
            socie = _Value(value, socie_ws.title)
            evaluations.append(_evaluation(
                spec, sopl, socie, sopl_ws.title, socie_ws.title, filing_level))
        sopl_wb.close(); socie_wb.close()
        return combine_period_evaluations(self.name, evaluations, tolerance)

    def run_facts(self, ctx, tolerance: float) -> CrossCheckResult:
        from cross_checks.facts_util import (
            read_labelled_value, read_matrix_value, socie_has_nci,
            socie_period_col, socie_retained_col, socie_total_col,
        )
        evaluations = []
        for spec in period_scopes(ctx.filing_level):
            sopl = read_labelled_value(
                ctx, StatementType.SOPL, "profit (loss)",
                spec.period, spec.entity_scope)
            base_col = (
                socie_total_col(ctx.filing_standard)
                if socie_has_nci(
                    ctx, StatementType.SOCIE, spec.period, spec.entity_scope)
                else socie_retained_col(ctx.filing_standard)
            )
            col = socie_period_col(
                ctx.filing_standard, ctx.filing_level, spec.period, base_col)
            socie = read_matrix_value(
                ctx, StatementType.SOCIE, "profit (loss)", col,
                spec.period, spec.entity_scope)
            evaluations.append(_evaluation(
                spec, sopl, socie, sopl.sheet or "SOPL", socie.sheet or "SOCIE",
                ctx.filing_level))
        return combine_period_evaluations(self.name, evaluations, tolerance)
