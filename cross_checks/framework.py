"""Pluggable cross-check runner.

Each check implements a simple protocol: .name, .required_statements,
.applies_to(run_config), .run(workbook_paths, tolerance). The runner
handles missing-statement detection (→ pending) and variant gating
(→ not_applicable) before calling .run().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Set, runtime_checkable

from statement_types import StatementType

# Default tolerance for numeric comparisons (RM 1 absolute).
DEFAULT_TOLERANCE_RM = 1.0


@dataclass
class CrossCheckResult:
    """Outcome of a single cross-check."""
    name: str
    # "passed" | "failed" | "not_applicable" | "pending"
    status: str
    expected: Optional[float] = None
    actual: Optional[float] = None
    diff: Optional[float] = None
    tolerance: Optional[float] = None
    message: str = ""


@runtime_checkable
class CrossCheck(Protocol):
    """Protocol that every cross-check must implement."""
    name: str
    required_statements: Set[StatementType]

    def applies_to(self, run_config: dict) -> bool:
        """Return False to mark this check not_applicable (e.g. wrong variant)."""
        ...

    def run(self, workbook_paths: Dict[StatementType, str], tolerance: float, filing_level: str = "company") -> CrossCheckResult:
        """Execute the check against filled workbooks. Only called when all
        required statements are present and applies_to returned True."""
        ...


def run_all(
    checks: list,
    workbook_paths: Dict[StatementType, str],
    run_config: dict,
    tolerance: float = DEFAULT_TOLERANCE_RM,
) -> list[CrossCheckResult]:
    """Run all registered checks, handling missing statements and variant gating.

    Args:
        checks: list of objects implementing the CrossCheck protocol.
        workbook_paths: {StatementType: path} for each extracted workbook.
        run_config: dict with at least 'statements_to_run' (set of StatementType),
                    optionally 'variants' (dict of StatementType -> str),
                    and optionally 'filing_level' (str: "company" or "group", default "company").
        tolerance: absolute RM tolerance for numeric comparisons.

    Returns:
        One CrossCheckResult per check, in the same order as the input list.
    """
    statements_run = run_config.get("statements_to_run", set())
    filing_level = run_config.get("filing_level", "company")
    results: list[CrossCheckResult] = []

    for check in checks:
        # 1. Are all required statements present in this run?
        missing = check.required_statements - statements_run
        if missing:
            missing_names = sorted(s.value for s in missing)
            results.append(CrossCheckResult(
                name=check.name,
                status="pending",
                message=(
                    f"{', '.join(missing_names)} not extracted in this run; "
                    f"cannot verify {check.name}"
                ),
            ))
            continue

        # 2. Are all required workbooks actually available?
        # A statement may have been selected but its agent failed, producing no output.
        missing_workbooks = check.required_statements - set(workbook_paths.keys())
        if missing_workbooks:
            missing_names = sorted(s.value for s in missing_workbooks)
            results.append(CrossCheckResult(
                name=check.name,
                status="failed",
                message=(
                    f"Workbook missing for {', '.join(missing_names)} "
                    f"(agent may have failed); cannot run {check.name}"
                ),
            ))
            continue

        # 3. Does this check apply to the current variant configuration?
        if not check.applies_to(run_config):
            results.append(CrossCheckResult(
                name=check.name,
                status="not_applicable",
                message=f"{check.name} does not apply to the current variant configuration",
            ))
            continue

        # 4. Run the actual check — catch exceptions so one broken check
        # doesn't abort the entire validation pass.
        try:
            result = check.run(workbook_paths, tolerance, filing_level=filing_level)
        except Exception as e:
            result = CrossCheckResult(
                name=check.name,
                status="failed",
                message=f"Check raised an exception: {e}",
            )
        results.append(result)

    return results
