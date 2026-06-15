"""P0 Check 1: SOFP balance — Total assets = Total equity + liabilities (CY + PY).

This is the fundamental accounting identity. If the balance sheet doesn't
balance, something is wrong with the extraction.
"""
from __future__ import annotations

from typing import Dict

from statement_types import StatementType
from cross_checks.framework import CrossCheckResult, Comparand
from cross_checks.util import (
    open_workbook, find_sheet, find_value_by_label, find_label_row,
    filing_level_prefix,
)


class SOFPBalanceCheck:
    name = "sofp_balance"
    required_statements = {StatementType.SOFP}

    def applies_to(self, run_config: dict) -> bool:
        # Always applies when SOFP is present — all variants have this identity.
        return True

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float, filing_level: str = "company") -> CrossCheckResult:
        path = workbook_paths[StatementType.SOFP]
        wb = open_workbook(path)

        ws = find_sheet(wb, "SOFP-CuNonCu", "SOFP-OrdOfLiq")
        if ws is None:
            wb.close()
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message="No SOFP main sheet found in workbook",
            )

        assets_cy = find_value_by_label(ws, "total assets", col=2, wb=wb)
        eq_liab_cy = find_value_by_label(ws, "total equity and liabilities", col=2, wb=wb)

        # Click-to-cell target: a balance failure is most actionable at the
        # equity+liabilities total (the side the reviewer compares to assets).
        target_sheet = ws.title
        target_row = find_label_row(ws, "total equity and liabilities")
        # Both sides as comparands so the reviewer can trace down from EITHER
        # total — not just the one `target_row` names. On run 153 the bug was
        # on the assets side while the target pointed at equity+liab.
        assets_row = find_label_row(ws, "total assets")

        # Group filing: also read Company columns (D=4)
        co_assets_cy = None
        co_eq_liab_cy = None
        if filing_level == "group":
            co_assets_cy = find_value_by_label(ws, "total assets", col=4, wb=wb)
            co_eq_liab_cy = find_value_by_label(ws, "total equity and liabilities", col=4, wb=wb)

        wb.close()

        if assets_cy is None or eq_liab_cy is None:
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message=f"Could not find total rows: assets={assets_cy}, equity+liab={eq_liab_cy}",
            )

        diff = abs(assets_cy - eq_liab_cy)
        group_passed = diff <= tolerance
        # Primary column prefix flows through filing_level_prefix so all
        # 5 P0 checks speak a consistent "Company X:" / "Group X:" idiom
        # (peer-review S-3/S-4). SOFP is a CY/PY balance check — include
        # the period marker.
        primary_label = filing_level_prefix(filing_level, with_period=True)
        parts = [f"{primary_label}: assets ({assets_cy}) vs equity+liab ({eq_liab_cy}), diff={diff:.2f}"]

        # Group filings must carry Company totals. Missing values used to
        # default co_passed=True, silently hiding an incomplete extraction.
        co_passed = True
        if filing_level == "group":
            if co_assets_cy is None or co_eq_liab_cy is None:
                co_passed = False
                parts.append(
                    f"Company CY: missing totals (assets={co_assets_cy}, equity+liab={co_eq_liab_cy})"
                )
            else:
                co_diff = abs(co_assets_cy - co_eq_liab_cy)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company CY: assets ({co_assets_cy}) vs equity+liab ({co_eq_liab_cy}), diff={co_diff:.2f}"
                )

        passed = group_passed and co_passed

        sofp = StatementType.SOFP.value
        comparands = [
            Comparand(label="Total assets", sheet=target_sheet, value=assets_cy,
                      role="lhs", statement=sofp, row=assets_row),
            Comparand(label="Total equity and liabilities", sheet=target_sheet,
                      value=eq_liab_cy, role="rhs", statement=sofp,
                      row=target_row),
        ]
        if filing_level == "group":
            comparands += [
                Comparand(label="Total assets [company]", sheet=target_sheet,
                          value=co_assets_cy, role="lhs", statement=sofp,
                          row=assets_row),
                Comparand(label="Total equity and liabilities [company]",
                          sheet=target_sheet, value=co_eq_liab_cy, role="rhs",
                          statement=sofp, row=target_row),
            ]

        return CrossCheckResult(
            name=self.name,
            status="passed" if passed else "failed",
            expected=assets_cy,
            actual=eq_liab_cy,
            diff=diff,
            tolerance=tolerance,
            message="; ".join(parts),
            target_sheet=target_sheet,
            target_row=target_row,
            comparands=comparands,
        )

    def run_facts(self, ctx, tolerance: float) -> CrossCheckResult:
        """Fact-based twin of :meth:`run` (item 32). Reads the two SOFP totals
        from ``run_concept_facts`` by concept uuid instead of evaluating the
        workbook's balance formulas. Every message/comparand/target field is
        produced identically so the result is shadow-equal to the xlsx path."""
        from cross_checks.facts_util import primary_scope, read_labelled_value
        from cross_checks.util import filing_level_prefix

        scope = primary_scope(ctx.filing_level)
        assets = read_labelled_value(ctx, StatementType.SOFP, "total assets", "CY", scope)
        eq_liab = read_labelled_value(
            ctx, StatementType.SOFP, "total equity and liabilities", "CY", scope)

        assets_cy, eq_liab_cy = assets.value, eq_liab.value
        # Both totals live on the SOFP main sheet — use the equity side as the
        # anchor sheet the way the xlsx path uses ws.title for both comparands.
        target_sheet = eq_liab.sheet or assets.sheet
        target_row = eq_liab.row
        assets_row = assets.row

        if assets_cy is None or eq_liab_cy is None:
            return CrossCheckResult(
                name=self.name,
                status="failed",
                message=f"Could not find total rows: assets={assets_cy}, equity+liab={eq_liab_cy}",
            )

        co_assets_cy = None
        co_eq_liab_cy = None
        if ctx.filing_level == "group":
            co_assets_cy = read_labelled_value(
                ctx, StatementType.SOFP, "total assets", "CY", "Company").value
            co_eq_liab_cy = read_labelled_value(
                ctx, StatementType.SOFP, "total equity and liabilities", "CY",
                "Company").value

        diff = abs(assets_cy - eq_liab_cy)
        group_passed = diff <= tolerance
        primary_label = filing_level_prefix(ctx.filing_level, with_period=True)
        parts = [f"{primary_label}: assets ({assets_cy}) vs equity+liab ({eq_liab_cy}), diff={diff:.2f}"]

        co_passed = True
        if ctx.filing_level == "group":
            if co_assets_cy is None or co_eq_liab_cy is None:
                co_passed = False
                parts.append(
                    f"Company CY: missing totals (assets={co_assets_cy}, equity+liab={co_eq_liab_cy})"
                )
            else:
                co_diff = abs(co_assets_cy - co_eq_liab_cy)
                co_passed = co_diff <= tolerance
                parts.append(
                    f"Company CY: assets ({co_assets_cy}) vs equity+liab ({co_eq_liab_cy}), diff={co_diff:.2f}"
                )

        passed = group_passed and co_passed

        sofp = StatementType.SOFP.value
        comparands = [
            Comparand(label="Total assets", sheet=target_sheet, value=assets_cy,
                      role="lhs", statement=sofp, row=assets_row),
            Comparand(label="Total equity and liabilities", sheet=target_sheet,
                      value=eq_liab_cy, role="rhs", statement=sofp,
                      row=target_row),
        ]
        if ctx.filing_level == "group":
            comparands += [
                Comparand(label="Total assets [company]", sheet=target_sheet,
                          value=co_assets_cy, role="lhs", statement=sofp,
                          row=assets_row),
                Comparand(label="Total equity and liabilities [company]",
                          sheet=target_sheet, value=co_eq_liab_cy, role="rhs",
                          statement=sofp, row=target_row),
            ]

        return CrossCheckResult(
            name=self.name,
            status="passed" if passed else "failed",
            expected=assets_cy,
            actual=eq_liab_cy,
            diff=diff,
            tolerance=tolerance,
            message="; ".join(parts),
            target_sheet=target_sheet,
            target_row=target_row,
            comparands=comparands,
        )
