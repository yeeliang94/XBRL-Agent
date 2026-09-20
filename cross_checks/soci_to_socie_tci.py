"""P0 Check 3: SOCI total comprehensive income = SOCIE across dimensions."""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult, Comparand
from cross_checks.periods import PeriodEvaluation, combine_period_evaluations, period_scopes
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label, find_value_in_block,
    socie_period_block, socie_period_column, socie_total_column, is_sore_run,
)
from cross_checks._format import fmt_amount, fmt_diff


def _message(spec, soci, socie):
    if soci is None and socie is None and spec.period == "PY":
        return f"{spec.label}: not checked (both TCI values absent)"
    if soci is None or socie is None:
        return f"{spec.label}: missing TCI values (SOCI={soci}, SOCIE={socie})"
    return (f"{spec.label}: SOCI ({fmt_amount(soci)}) vs SOCIE ({fmt_amount(socie)}), "
            f"diff={fmt_diff(abs(soci - socie))}")


def _evaluation(spec, soci, socie, soci_sheet, socie_sheet, filing_level):
    suffix = " [company]" if filing_level == "group" and spec.entity_scope == "Company" else ""
    return PeriodEvaluation(
        spec=spec, lhs=soci.value, rhs=socie.value,
        message=_message(spec, soci.value, socie.value),
        comparands=[
            Comparand(label=f"Total comprehensive income{suffix}", sheet=soci_sheet,
                      value=soci.value, role="lhs", statement=StatementType.SOCI.value,
                      period=spec.period),
            Comparand(label=f"Total comprehensive income{suffix}", sheet=socie_sheet,
                      value=socie.value, role="rhs", statement=StatementType.SOCIE.value,
                      period=spec.period),
        ],
    )


class _Value:
    def __init__(self, value, sheet):
        self.value, self.sheet, self.row = value, sheet, None


class SOCIToSOCIETCICheck:
    name = "soci_to_socie_tci"
    required_statements = {StatementType.SOCI, StatementType.SOCIE}

    def applies_to(self, run_config: dict) -> bool:
        return not is_sore_run(run_config)

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float,
            filing_level: str = "company", filing_standard: str = "mfrs") -> CrossCheckResult:
        soci_wb = open_workbook(workbook_paths[StatementType.SOCI])
        soci_ws = find_sheet(soci_wb, "SOCI-BeforeOfTax", "SOCI-BeforeTax", "SOCI-NetOfTax")
        socie_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        socie_ws = find_sheet(socie_wb, "SOCIE")
        if soci_ws is None or socie_ws is None:
            soci_wb.close(); socie_wb.close()
            return CrossCheckResult(name=self.name, status="failed",
                                    message="Could not find SOCI or SOCIE main sheet")
        evaluations = []
        for spec in period_scopes(filing_level):
            soci = _Value(find_value_by_label(
                soci_ws, "total comprehensive income", col=spec.column, wb=soci_wb,
                blank_formula_as_none=spec.period == "PY"),
                soci_ws.title)
            block = socie_period_block(
                filing_standard, filing_level, spec.entity_scope, spec.period)
            col = socie_period_column(
                filing_standard, filing_level, spec.period,
                socie_total_column(filing_standard))
            if block is None:
                value = find_value_by_label(
                    socie_ws, "total comprehensive income", col=col, wb=socie_wb,
                    blank_formula_as_none=spec.period == "PY")
            else:
                value = find_value_in_block(
                    socie_ws, "total comprehensive income", col,
                    block[0], block[1], wb=socie_wb,
                    blank_formula_as_none=spec.period == "PY")
                if value is None and filing_level == "company" and spec.period == "CY":
                    value = find_value_by_label(
                        socie_ws, "total comprehensive income", col=col, wb=socie_wb)
            socie = _Value(value, socie_ws.title)
            evaluations.append(_evaluation(
                spec, soci, socie, soci_ws.title, socie_ws.title, filing_level))
        soci_wb.close(); socie_wb.close()
        return combine_period_evaluations(self.name, evaluations, tolerance)

    def run_facts(self, ctx, tolerance: float) -> CrossCheckResult:
        from cross_checks.facts_util import (
            read_labelled_value, read_matrix_value, socie_period_col, socie_total_col,
        )
        evaluations = []
        for spec in period_scopes(ctx.filing_level):
            soci = read_labelled_value(
                ctx, StatementType.SOCI, "total comprehensive income",
                spec.period, spec.entity_scope)
            col = socie_period_col(
                ctx.filing_standard, ctx.filing_level, spec.period,
                socie_total_col(ctx.filing_standard))
            socie = read_matrix_value(
                ctx, StatementType.SOCIE, "total comprehensive income", col,
                spec.period, spec.entity_scope)
            evaluations.append(_evaluation(
                spec, soci, socie, soci.sheet or "SOCI", socie.sheet or "SOCIE",
                ctx.filing_level))
        return combine_period_evaluations(self.name, evaluations, tolerance)
