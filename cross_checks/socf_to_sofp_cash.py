"""P0 Check 5: SOCF closing cash = SOFP cash for all populated dimensions."""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult, Comparand
from cross_checks.periods import PeriodEvaluation, combine_period_evaluations, period_scopes
from cross_checks.util import open_workbook, find_sheet, find_value_by_label
from cross_checks._format import fmt_amount, fmt_diff


def _read_sofp_cash_equivalents_fact(ctx, period: str, entity_scope: str):
    from cross_checks.facts_util import read_labelled_value
    return read_labelled_value(
        ctx, StatementType.SOFP,
        ["total cash and cash equivalents", "total cash and bank balances",
         "cash and cash equivalents"],
        period, entity_scope,
    )


def _message(spec, socf_cash, sofp_cash, tolerance):
    if socf_cash is None and sofp_cash is None and spec.period == "PY":
        return f"{spec.label}: not checked (both cash values absent)"
    if socf_cash is None or sofp_cash is None:
        return f"{spec.label}: missing cash values (SOCF={socf_cash}, SOFP={sofp_cash})"
    diff = abs(socf_cash - sofp_cash)
    msg = (f"{spec.label}: SOCF ({fmt_amount(socf_cash)}) vs SOFP "
           f"({fmt_amount(sofp_cash)}), diff={fmt_diff(diff)}")
    if diff > tolerance and sofp_cash == 0 and socf_cash != 0:
        msg += (
            "; SOFP cash is 0 but SOCF closing cash is non-zero — the SOFP "
            "face likely has a cash line with no separate note. Fill SOFP "
            "cash from the face statement; do not rework SOCF."
        )
    return msg


def _evaluation(spec, socf, sofp, socf_sheet, sofp_sheet, filing_level, tolerance):
    suffix = " [company]" if filing_level == "group" and spec.entity_scope == "Company" else ""
    return PeriodEvaluation(
        spec=spec, lhs=socf.value, rhs=sofp.value,
        message=_message(spec, socf.value, sofp.value, tolerance),
        comparands=[
            Comparand(label=f"Cash and cash equivalents at end of period{suffix}",
                      sheet=socf_sheet, value=socf.value, role="lhs",
                      statement=StatementType.SOCF.value, period=spec.period),
            Comparand(label=f"Cash and cash equivalents{suffix}",
                      sheet=sofp_sheet, value=sofp.value, role="rhs",
                      statement=StatementType.SOFP.value, period=spec.period),
        ],
    )


class _Value:
    def __init__(self, value, sheet, row=None):
        self.value, self.sheet, self.row = value, sheet, row


class SOCFToSOFPCashCheck:
    name = "socf_to_sofp_cash"
    required_statements = {StatementType.SOCF, StatementType.SOFP}

    def applies_to(self, run_config: dict) -> bool:
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float,
            filing_level: str = "company") -> CrossCheckResult:
        socf_wb = open_workbook(workbook_paths[StatementType.SOCF])
        socf_ws = find_sheet(socf_wb, "SOCF-Indirect", "SOCF-Direct")
        sofp_wb = open_workbook(workbook_paths[StatementType.SOFP])
        sofp_ws = find_sheet(sofp_wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        if socf_ws is None or sofp_ws is None:
            socf_wb.close(); sofp_wb.close()
            return CrossCheckResult(name=self.name, status="failed",
                                    message="Could not find SOCF or SOFP main sheet")
        evaluations = []
        for spec in period_scopes(filing_level):
            socf = _Value(find_value_by_label(
                socf_ws, "cash and cash equivalents at end of period",
                col=spec.column, wb=socf_wb,
                blank_formula_as_none=spec.period == "PY"), socf_ws.title)
            sofp = _Value(find_value_by_label(
                sofp_ws, ["cash and cash equivalents", "total cash and bank balances"],
                col=spec.column, wb=sofp_wb,
                blank_formula_as_none=spec.period == "PY"), sofp_ws.title)
            evaluations.append(_evaluation(
                spec, socf, sofp, socf_ws.title, sofp_ws.title,
                filing_level, tolerance))
        socf_wb.close(); sofp_wb.close()
        return combine_period_evaluations(self.name, evaluations, tolerance)

    def run_facts(self, ctx, tolerance: float) -> CrossCheckResult:
        from cross_checks.facts_util import read_labelled_value
        evaluations = []
        for spec in period_scopes(ctx.filing_level):
            socf = read_labelled_value(
                ctx, StatementType.SOCF,
                "cash and cash equivalents at end of period",
                spec.period, spec.entity_scope)
            sofp = _read_sofp_cash_equivalents_fact(
                ctx, spec.period, spec.entity_scope)
            evaluations.append(_evaluation(
                spec, socf, sofp, socf.sheet or "SOCF", sofp.sheet or "SOFP",
                ctx.filing_level, tolerance))
        return combine_period_evaluations(self.name, evaluations, tolerance)
