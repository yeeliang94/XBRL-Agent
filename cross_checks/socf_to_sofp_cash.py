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
from cross_checks._format import fmt_amount, fmt_diff


def _read_sofp_cash_equivalents_fact(ctx, period: str, entity_scope: str):
    """Read SOFP closing cash from facts, preferring the TOTAL concept.

    The SOFP face "cash and cash equivalents" line is a cross-sheet rollup of
    the sub-sheet "*Total cash and cash equivalents" row (gotcha #21): the face
    coord is an *alias*, so the only concepts that resolve by label live on the
    sub-sheet — where the partial component "Other cash and cash equivalents"
    LEAF sits on the row immediately ABOVE the "*Total cash and cash
    equivalents" COMPUTED row. ``read_labelled_value`` returns the first
    POPULATED candidate by row order, so a bare "cash and cash equivalents"
    search reads the partial "Other …" leaf instead of the total. (The
    workbook ``run()`` path is immune: it scans the FACE sheet, whose single
    line already carries the rolled-up total via formula.)

    Putting the total-row labels first makes the read EXACT-match the
    "*Total …" row across all four SOFP variants (MFRS/MPERS × CuNonCu /
    OrderOfLiquidity); exact matches sort before substring matches, so the
    total wins even though the partial leaf sits above it. The bare-label
    fallback only fires on a future variant with no total row, and is no worse
    than the pre-fix behaviour there.
    """
    from cross_checks.facts_util import read_labelled_value
    return read_labelled_value(
        ctx, StatementType.SOFP,
        [
            "total cash and cash equivalents",  # MFRS/MPERS CuNonCu + MPERS OrdOfLiq
            "total cash and bank balances",     # MFRS OrderOfLiquidity
            "cash and cash equivalents",        # last-resort fallback
        ],
        period, entity_scope,
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
        parts = [f"{primary_label}: SOCF ({fmt_amount(socf_cash)}) vs SOFP ({fmt_amount(sofp_cash)}), diff={fmt_diff(diff)}"]
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
                    f"Company: SOCF ({fmt_amount(co_socf_cash)}) vs SOFP ({fmt_amount(co_sofp_cash)}), diff={fmt_diff(co_diff)}"
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
        sofp = _read_sofp_cash_equivalents_fact(ctx, "CY", scope)
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
        parts = [f"{primary_label}: SOCF ({fmt_amount(socf_cash)}) vs SOFP ({fmt_amount(sofp_cash)}), diff={fmt_diff(diff)}"]
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
            co_sofp_cash = _read_sofp_cash_equivalents_fact(
                ctx, "CY", "Company").value
            if co_socf_cash is None or co_sofp_cash is None:
                co_passed = False
                parts.append(
                    f"Company: missing cash values (SOCF={co_socf_cash}, SOFP={co_sofp_cash})"
                )
            else:
                co_diff = abs(co_socf_cash - co_sofp_cash)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company: SOCF ({fmt_amount(co_socf_cash)}) vs SOFP ({fmt_amount(co_sofp_cash)}), diff={fmt_diff(co_diff)}"
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
