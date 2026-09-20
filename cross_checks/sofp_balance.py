"""P0 Check 1: SOFP balance for every populated period and entity scope."""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult, Comparand
from cross_checks.periods import PeriodEvaluation, combine_period_evaluations, period_scopes
from cross_checks.util import open_workbook, find_sheet, find_value_by_label, find_label_row
from cross_checks._format import fmt_amount, fmt_diff


def _message(spec, assets, equity_liabilities):
    if assets is None and equity_liabilities is None and spec.period == "PY":
        return f"{spec.label}: not checked (both totals absent)"
    if assets is None or equity_liabilities is None:
        return f"{spec.label}: missing totals (assets={assets}, equity+liab={equity_liabilities})"
    return (
        f"{spec.label}: assets ({fmt_amount(assets)}) vs equity+liab "
        f"({fmt_amount(equity_liabilities)}), "
        f"diff={fmt_diff(abs(assets - equity_liabilities))}"
    )


class SOFPBalanceCheck:
    name = "sofp_balance"
    required_statements = {StatementType.SOFP}

    def applies_to(self, run_config: dict) -> bool:
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float,
            filing_level: str = "company") -> CrossCheckResult:
        wb = open_workbook(workbook_paths[StatementType.SOFP])
        ws = find_sheet(wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        if ws is None:
            wb.close()
            return CrossCheckResult(name=self.name, status="failed",
                                    message="No SOFP main sheet found in workbook")

        sheet = ws.title
        assets_row = find_label_row(ws, "total assets")
        target_row = find_label_row(ws, "total equity and liabilities")
        evaluations = []
        for spec in period_scopes(filing_level):
            assets = find_value_by_label(
                ws, "total assets", col=spec.column, wb=wb,
                blank_formula_as_none=spec.period == "PY",
            )
            equity_liabilities = find_value_by_label(
                ws, "total equity and liabilities", col=spec.column, wb=wb,
                blank_formula_as_none=spec.period == "PY",
            )
            suffix = " [company]" if filing_level == "group" and spec.entity_scope == "Company" else ""
            evaluations.append(PeriodEvaluation(
                spec=spec, lhs=assets, rhs=equity_liabilities,
                message=_message(spec, assets, equity_liabilities),
                target_sheet=sheet, target_row=target_row,
                comparands=[
                    Comparand(label=f"Total assets{suffix}", sheet=sheet, value=assets,
                              role="lhs", statement=StatementType.SOFP.value,
                              row=assets_row, period=spec.period),
                    Comparand(label=f"Total equity and liabilities{suffix}", sheet=sheet,
                              value=equity_liabilities, role="rhs",
                              statement=StatementType.SOFP.value, row=target_row,
                              period=spec.period),
                ],
            ))
        wb.close()
        return combine_period_evaluations(self.name, evaluations, tolerance)

    def run_facts(self, ctx, tolerance: float) -> CrossCheckResult:
        """Fact-based twin of :meth:`run`, including comparative facts."""
        from cross_checks.facts_util import read_labelled_value

        evaluations = []
        for spec in period_scopes(ctx.filing_level):
            assets = read_labelled_value(ctx, StatementType.SOFP, "total assets",
                                         spec.period, spec.entity_scope)
            equity_liabilities = read_labelled_value(
                ctx, StatementType.SOFP, "total equity and liabilities",
                spec.period, spec.entity_scope)
            sheet = equity_liabilities.sheet or assets.sheet or "SOFP"
            suffix = " [company]" if ctx.filing_level == "group" and spec.entity_scope == "Company" else ""
            evaluations.append(PeriodEvaluation(
                spec=spec, lhs=assets.value, rhs=equity_liabilities.value,
                message=_message(spec, assets.value, equity_liabilities.value),
                target_sheet=sheet, target_row=equity_liabilities.row,
                comparands=[
                    Comparand(label=f"Total assets{suffix}", sheet=sheet,
                              value=assets.value, role="lhs",
                              statement=StatementType.SOFP.value, row=assets.row,
                              period=spec.period),
                    Comparand(label=f"Total equity and liabilities{suffix}", sheet=sheet,
                              value=equity_liabilities.value, role="rhs",
                              statement=StatementType.SOFP.value,
                              row=equity_liabilities.row, period=spec.period),
                ],
            ))
        return combine_period_evaluations(self.name, evaluations, tolerance)
