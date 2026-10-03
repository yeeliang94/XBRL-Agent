"""CLBG fund and income tie-outs using the exact CLBG filing vocabulary."""
from dataclasses import dataclass

from statement_types import StatementType
from cross_checks.framework import Comparand
from cross_checks.periods import PeriodEvaluation, combine_period_evaluations, period_scopes


@dataclass(frozen=True)
class CLBGTieOutCheck:
    name: str
    left_statement: StatementType
    left_label: str
    right_statement: StatementType
    right_label: str
    left_matrix: bool = False
    right_matrix: bool = False
    applies_to_standard = frozenset({"clbg"})

    @property
    def required_statements(self):
        return {self.left_statement, self.right_statement}

    def applies_to(self, run_config):
        return run_config.get("filing_standard") == "clbg"

    def _combine(self, readings, tolerance):
        evaluations = []
        for spec, left, right in readings:
            evaluations.append(PeriodEvaluation(
                spec=spec, lhs=left.value, rhs=right.value,
                message=f"{spec.label}: {self.left_label} ({left.value}) vs {self.right_label} ({right.value})",
                comparands=[
                    Comparand(label=self.left_label, sheet=left.sheet, value=left.value,
                              role="lhs", statement=self.left_statement.value, period=spec.period, row=left.row),
                    Comparand(label=self.right_label, sheet=right.sheet, value=right.value,
                              role="rhs", statement=self.right_statement.value, period=spec.period, row=right.row),
                ],
            ))
        return combine_period_evaluations(self.name, evaluations, tolerance)

    def run_facts(self, ctx, tolerance):
        from cross_checks.facts_util import read_labelled_value, read_matrix_value
        def read(statement, label, matrix, spec):
            if matrix:
                return read_matrix_value(ctx, statement, label, "N", spec.period, spec.entity_scope)
            return read_labelled_value(ctx, statement, label, spec.period, spec.entity_scope)
        return self._combine([
            (spec, read(self.left_statement, self.left_label, self.left_matrix, spec),
             read(self.right_statement, self.right_label, self.right_matrix, spec))
            for spec in period_scopes(ctx.filing_level)
        ], tolerance)

    def run(self, workbook_paths, tolerance, filing_level="company", filing_standard="clbg"):
        from types import SimpleNamespace
        from cross_checks.util import open_workbook, find_value_by_label, find_value_in_block
        from scripts.generate_clbg_templates import fund_line_items
        workbooks = {statement: open_workbook(workbook_paths[statement]) for statement in self.required_statements}
        def read(statement, label, matrix, spec):
            wb = workbooks[statement]
            ws = wb["SOCIE"] if matrix else wb[wb.sheetnames[0]]
            if matrix:
                start = 6 + (0 if spec.period == "CY" else len(fund_line_items())+3)
                value = find_value_in_block(ws, label, 14, start, start+len(fund_line_items())-1,
                                            wb=wb, blank_formula_as_none=True)
            else:
                value = find_value_by_label(ws, label, col=spec.column, wb=wb, blank_formula_as_none=True)
            return SimpleNamespace(value=value, sheet=ws.title, row=None)
        try:
            return self._combine([
                (spec, read(self.left_statement, self.left_label, self.left_matrix, spec),
                 read(self.right_statement, self.right_label, self.right_matrix, spec))
                for spec in period_scopes(filing_level)
            ], tolerance)
        finally:
            for wb in workbooks.values():
                wb.close()


def clbg_checks():
    return [
        CLBGTieOutCheck("clbg_sofp_balance", StatementType.SOFP, "total assets",
                       StatementType.SOFP, "total fund/equity and liabilities"),
        CLBGTieOutCheck("clbg_income_to_fund", StatementType.SOPL, "total surplus (deficit)",
                       StatementType.SOCIE, "total surplus (deficit)", right_matrix=True),
        CLBGTieOutCheck("clbg_fund_to_sofp", StatementType.SOCIE, "balance at end of period",
                       StatementType.SOFP, "total fund/equity and reserve", left_matrix=True),
    ]
