"""MPERS SoRE closing retained earnings = SOFP, by populated period/scope."""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult, Comparand
from cross_checks.periods import PeriodEvaluation, combine_period_evaluations, period_scopes
from cross_checks.util import open_workbook, find_sheet, find_value_by_label, is_sore_run
from cross_checks._format import fmt_amount, fmt_diff


def _message(spec, sore, sofp):
    if sore is None and sofp is None and spec.period == "PY":
        return f"{spec.label}: not checked (both retained-earnings values absent)"
    if sore is None or sofp is None:
        return f"{spec.label}: missing retained earnings (SoRE={sore}, SOFP={sofp})"
    return (f"{spec.label}: SoRE ({fmt_amount(sore)}) vs SOFP ({fmt_amount(sofp)}), "
            f"diff={fmt_diff(abs(sore - sofp))}")


def _evaluation(spec, sore, sofp, sore_sheet, sofp_sheet, filing_level):
    suffix = " [company]" if filing_level == "group" and spec.entity_scope == "Company" else ""
    return PeriodEvaluation(
        spec=spec, lhs=sore.value, rhs=sofp.value,
        message=_message(spec, sore.value, sofp.value),
        comparands=[
            Comparand(label=f"Retained earnings at end of period{suffix}",
                      sheet=sore_sheet, value=sore.value, role="lhs",
                      statement=StatementType.SOCIE.value, row=sore.row,
                      period=spec.period),
            Comparand(label=f"Retained earnings{suffix}", sheet=sofp_sheet,
                      value=sofp.value, role="rhs", statement=StatementType.SOFP.value,
                      row=sofp.row, period=spec.period),
        ],
    )


class _Value:
    def __init__(self, value, sheet):
        self.value, self.sheet, self.row = value, sheet, None


class SoREToSOFPRetainedEarningsCheck:
    name = "sore_to_sofp_retained_earnings"
    required_statements = {StatementType.SOCIE, StatementType.SOFP}
    applies_to_standard = frozenset({"mpers"})

    def applies_to(self, run_config: dict) -> bool:
        return is_sore_run(run_config)

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float,
            filing_level: str = "company") -> CrossCheckResult:
        sore_wb = open_workbook(workbook_paths[StatementType.SOCIE])
        sore_ws = find_sheet(sore_wb, "SoRE")
        sofp_wb = open_workbook(workbook_paths[StatementType.SOFP])
        sofp_ws = find_sheet(sofp_wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        if sore_ws is None or sofp_ws is None:
            sore_wb.close(); sofp_wb.close()
            return CrossCheckResult(name=self.name, status="failed",
                                    message="Could not find SoRE or SOFP main sheet")
        evaluations = []
        for spec in period_scopes(filing_level):
            sore = _Value(find_value_by_label(
                sore_ws, "retained earnings at end of period",
                col=spec.column, wb=sore_wb,
                blank_formula_as_none=spec.period == "PY"), sore_ws.title)
            sofp = _Value(find_value_by_label(
                sofp_ws, ["Retained earnings"], col=spec.column, wb=sofp_wb,
                blank_formula_as_none=spec.period == "PY"),
                sofp_ws.title)
            evaluations.append(_evaluation(
                spec, sore, sofp, sore_ws.title, sofp_ws.title, filing_level))
        sore_wb.close(); sofp_wb.close()
        return combine_period_evaluations(self.name, evaluations, tolerance)

    def run_facts(self, ctx, tolerance: float) -> CrossCheckResult:
        from cross_checks.facts_util import read_labelled_value
        evaluations = []
        for spec in period_scopes(ctx.filing_level):
            sore = read_labelled_value(
                ctx, StatementType.SOCIE, "retained earnings at end of period",
                spec.period, spec.entity_scope)
            sofp = read_labelled_value(
                ctx, StatementType.SOFP, ["Retained earnings"],
                spec.period, spec.entity_scope)
            evaluations.append(_evaluation(
                spec, sore, sofp, sore.sheet or "SoRE", sofp.sheet or "SOFP",
                ctx.filing_level))
        return combine_period_evaluations(self.name, evaluations, tolerance)
