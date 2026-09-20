"""Period-by-scope helpers shared by core statement cross-checks.

Canonical facts always carry both a period role and an entity scope.  The
xlsx representation encodes the same dimensions in columns (and, for MFRS
SOCIE, row blocks).  Keeping the ordinary statement-column matrix here avoids
each check silently drifting back to a CY-only implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from cross_checks.framework import Comparand, CrossCheckResult


@dataclass(frozen=True)
class PeriodScope:
    entity_scope: str
    period: str
    column: int

    @property
    def label(self) -> str:
        return f"{self.entity_scope} {self.period}"


def period_scopes(filing_level: str) -> list[PeriodScope]:
    """Return every ordinary statement period/scope in stable display order."""
    if filing_level == "group":
        return [
            PeriodScope("Group", "CY", 2),
            PeriodScope("Group", "PY", 3),
            PeriodScope("Company", "CY", 4),
            PeriodScope("Company", "PY", 5),
        ]
    return [
        PeriodScope("Company", "CY", 2),
        PeriodScope("Company", "PY", 3),
    ]


@dataclass
class PeriodEvaluation:
    spec: PeriodScope
    lhs: Optional[float]
    rhs: Optional[float]
    message: str
    comparands: list[Comparand]
    target_sheet: Optional[str] = None
    target_row: Optional[int] = None
    # PY is optional when both statements genuinely omit it.  CY remains
    # required, preserving the existing missing-primary-value failure.
    optional_when_both_missing: bool = True

    def verdict(self, tolerance: float) -> str:
        if self.lhs is None and self.rhs is None and (
            self.spec.period == "PY" and self.optional_when_both_missing
        ):
            return "skipped"
        if self.lhs is None or self.rhs is None:
            return "failed"
        return "passed" if abs(self.lhs - self.rhs) <= tolerance else "failed"

    @property
    def diff(self) -> Optional[float]:
        if self.lhs is None or self.rhs is None:
            return None
        return abs(self.lhs - self.rhs)


def combine_period_evaluations(
    name: str,
    evaluations: list[PeriodEvaluation],
    tolerance: float,
) -> CrossCheckResult:
    """Combine a period/scope matrix into the existing one-result contract.

    The first failing cell is the numeric/target anchor so a clean CY plus a
    bad PY reports the PY difference, rather than a misleading zero CY diff.
    Explicit skip messages are retained in ``message`` for genuinely absent
    comparative periods.
    """
    verdicts = [evaluation.verdict(tolerance) for evaluation in evaluations]
    status = "failed" if "failed" in verdicts else "passed"
    anchor = next(
        (evaluation for evaluation in evaluations
         if evaluation.verdict(tolerance) == "failed"),
        None,
    ) or next(
        (evaluation for evaluation in evaluations
         if evaluation.verdict(tolerance) == "passed"),
        None,
    )
    comparands = [
        comparand
        # Keep CY after PY within a scope so legacy callers that collapse by
        # role (``{c.role: c}``) still see the CY anchor. Period-aware callers
        # retain every comparand via the explicit ``period`` field.
        for evaluation in sorted(
            evaluations, key=lambda item: (item.spec.entity_scope, item.spec.period == "CY")
        )
        if evaluation.verdict(tolerance) != "skipped"
        for comparand in evaluation.comparands
    ]
    result = CrossCheckResult(
        name=name,
        status=status,
        tolerance=tolerance,
        message="; ".join(evaluation.message for evaluation in evaluations),
        comparands=comparands,
    )
    if anchor is not None:
        result.expected = anchor.lhs
        result.actual = anchor.rhs
        result.diff = anchor.diff
        result.target_sheet = anchor.target_sheet
        result.target_row = anchor.target_row
    return result
