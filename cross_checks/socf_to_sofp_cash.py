"""P0 Check 5: SOCF closing cash = SOFP cash and cash equivalents.

The cash at end of period from SOCF must match the SOFP "Cash and cash
equivalents" line item. This is the tightest cross-statement reconciliation.
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult, Comparand
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label, filing_level_prefix,
)


class SOCFToSOFPCashCheck:
    name = "socf_to_sofp_cash"
    required_statements = {StatementType.SOCF, StatementType.SOFP}

    def applies_to(self, run_config: dict) -> bool:
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float, filing_level: str = "company") -> CrossCheckResult:
        socf_wb = open_workbook(workbook_paths[StatementType.SOCF])
        socf_ws = find_sheet(socf_wb, "SOCF-Indirect", "SOCF-Direct")
        socf_sheet = socf_ws.title if socf_ws is not None else "SOCF"
        socf_cash = None
        co_socf_cash = None
        if socf_ws is not None:
            socf_cash = find_value_by_label(socf_ws, "cash and cash equivalents at end of period", col=2, wb=socf_wb)
            if filing_level == "group":
                co_socf_cash = find_value_by_label(socf_ws, "cash and cash equivalents at end of period", col=4, wb=socf_wb)
        socf_wb.close()

        sofp_wb = open_workbook(workbook_paths[StatementType.SOFP])
        sofp_ws = find_sheet(sofp_wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        sofp_sheet = sofp_ws.title if sofp_ws is not None else "SOFP"
        sofp_cash = None
        co_sofp_cash = None
        if sofp_ws is not None:
            sofp_cash = find_value_by_label(
                sofp_ws, ["cash and cash equivalents", "total cash and bank balances"], col=2, wb=sofp_wb,
            )
            if filing_level == "group":
                co_sofp_cash = find_value_by_label(
                    sofp_ws, ["cash and cash equivalents", "total cash and bank balances"], col=4, wb=sofp_wb,
                )
        sofp_wb.close()

        if socf_cash is None or sofp_cash is None:
            return CrossCheckResult(
                name=self.name, status="failed",
                message=f"Could not find cash values: SOCF={socf_cash}, SOFP={sofp_cash}",
            )

        diff = abs(socf_cash - sofp_cash)
        group_passed = diff <= tolerance
        # Reconciliation check — see cross_checks.util.filing_level_prefix.
        primary_label = filing_level_prefix(filing_level, with_period=False)
        parts = [f"{primary_label}: SOCF ({socf_cash}) vs SOFP ({sofp_cash}), diff={diff:.2f}"]
        # Peer-review (Edge AFS, 2026-05-28): when one side is 0 and the
        # other is non-trivial, the failure is almost always a missing
        # SOFP fill (face line with no note) rather than a balance mismatch.
        # Surface that lineage so the correction agent treats it as a
        # targeted SOFP-cash fill, not a SOCF rework.
        if not group_passed and sofp_cash == 0 and socf_cash != 0:
            parts.append(
                "SOFP cash is 0 but SOCF closing cash is non-zero — the "
                "SOFP face likely has a cash line with no separate note. "
                "Fill SOFP cash from the face statement; do not rework SOCF."
            )

        # Group filings must carry Company totals — see sofp_balance.py for
        # the peer-review background on the old silent-pass default.
        co_passed = True
        if filing_level == "group":
            if co_socf_cash is None or co_sofp_cash is None:
                co_passed = False
                parts.append(
                    f"Company: missing cash values (SOCF={co_socf_cash}, SOFP={co_sofp_cash})"
                )
            else:
                co_diff = abs(co_socf_cash - co_sofp_cash)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company: SOCF ({co_socf_cash}) vs SOFP ({co_sofp_cash}), diff={co_diff:.2f}"
                )

        comparands = [
            Comparand(label="Cash and cash equivalents at end of period",
                      sheet=socf_sheet, value=socf_cash, role="lhs",
                      statement=StatementType.SOCF.value),
            Comparand(label="Cash and cash equivalents", sheet=sofp_sheet,
                      value=sofp_cash, role="rhs",
                      statement=StatementType.SOFP.value),
        ]
        if filing_level == "group":
            comparands += [
                Comparand(label="Cash and cash equivalents at end of period "
                          "[company]", sheet=socf_sheet, value=co_socf_cash,
                          role="lhs", statement=StatementType.SOCF.value),
                Comparand(label="Cash and cash equivalents [company]",
                          sheet=sofp_sheet, value=co_sofp_cash, role="rhs",
                          statement=StatementType.SOFP.value),
            ]

        return CrossCheckResult(
            name=self.name,
            status="passed" if group_passed and co_passed else "failed",
            expected=socf_cash, actual=sofp_cash, diff=diff, tolerance=tolerance,
            message="; ".join(parts),
            comparands=comparands,
        )

    def run_facts(self, ctx, tolerance: float) -> CrossCheckResult:
        """Fact-based twin of :meth:`run` (item 32). Reads SOCF closing cash and
        SOFP cash from ``run_concept_facts`` by uuid; both are linear (non-
        matrix) concepts."""
        from cross_checks.facts_util import primary_scope, read_labelled_value
        from cross_checks.util import filing_level_prefix

        scope = primary_scope(ctx.filing_level)
        socf = read_labelled_value(
            ctx, StatementType.SOCF, "cash and cash equivalents at end of period",
            "CY", scope)
        sofp = read_labelled_value(
            ctx, StatementType.SOFP,
            ["cash and cash equivalents", "total cash and bank balances"],
            "CY", scope)
        socf_cash, sofp_cash = socf.value, sofp.value
        socf_sheet = socf.sheet or "SOCF"
        sofp_sheet = sofp.sheet or "SOFP"

        if socf_cash is None or sofp_cash is None:
            return CrossCheckResult(
                name=self.name, status="failed",
                message=f"Could not find cash values: SOCF={socf_cash}, SOFP={sofp_cash}",
            )

        diff = abs(socf_cash - sofp_cash)
        group_passed = diff <= tolerance
        primary_label = filing_level_prefix(ctx.filing_level, with_period=False)
        parts = [f"{primary_label}: SOCF ({socf_cash}) vs SOFP ({sofp_cash}), diff={diff:.2f}"]
        if not group_passed and sofp_cash == 0 and socf_cash != 0:
            parts.append(
                "SOFP cash is 0 but SOCF closing cash is non-zero — the "
                "SOFP face likely has a cash line with no separate note. "
                "Fill SOFP cash from the face statement; do not rework SOCF."
            )

        co_socf_cash = None
        co_sofp_cash = None
        co_passed = True
        if ctx.filing_level == "group":
            co_socf_cash = read_labelled_value(
                ctx, StatementType.SOCF,
                "cash and cash equivalents at end of period", "CY", "Company").value
            co_sofp_cash = read_labelled_value(
                ctx, StatementType.SOFP,
                ["cash and cash equivalents", "total cash and bank balances"],
                "CY", "Company").value
            if co_socf_cash is None or co_sofp_cash is None:
                co_passed = False
                parts.append(
                    f"Company: missing cash values (SOCF={co_socf_cash}, SOFP={co_sofp_cash})"
                )
            else:
                co_diff = abs(co_socf_cash - co_sofp_cash)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company: SOCF ({co_socf_cash}) vs SOFP ({co_sofp_cash}), diff={co_diff:.2f}"
                )

        comparands = [
            Comparand(label="Cash and cash equivalents at end of period",
                      sheet=socf_sheet, value=socf_cash, role="lhs",
                      statement=StatementType.SOCF.value),
            Comparand(label="Cash and cash equivalents", sheet=sofp_sheet,
                      value=sofp_cash, role="rhs",
                      statement=StatementType.SOFP.value),
        ]
        if ctx.filing_level == "group":
            comparands += [
                Comparand(label="Cash and cash equivalents at end of period "
                          "[company]", sheet=socf_sheet, value=co_socf_cash,
                          role="lhs", statement=StatementType.SOCF.value),
                Comparand(label="Cash and cash equivalents [company]",
                          sheet=sofp_sheet, value=co_sofp_cash, role="rhs",
                          statement=StatementType.SOFP.value),
            ]

        return CrossCheckResult(
            name=self.name,
            status="passed" if group_passed and co_passed else "failed",
            expected=socf_cash, actual=sofp_cash, diff=diff, tolerance=tolerance,
            message="; ".join(parts),
            comparands=comparands,
        )
