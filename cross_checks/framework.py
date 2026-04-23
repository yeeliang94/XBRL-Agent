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

# Default `applies_to_standard` for checks that don't narrow themselves —
# the vast majority of cross-checks run on both taxonomies. MPERS-only
# checks (e.g. SoRE → SOFP retained-earnings) override this on the class.
# Hoisted to module scope so the contract is visible without reading
# `run_all` (peer-review S10).
DEFAULT_APPLIES_TO_STANDARD: frozenset[str] = frozenset({"mfrs", "mpers"})


@dataclass
class CrossCheckResult:
    """Outcome of a single cross-check.

    ``status`` values:
    - ``"passed"`` / ``"failed"`` — hard numeric pass/fail.
    - ``"pending"`` — a required statement is missing; run again after
      the missing sheet ships.
    - ``"not_applicable"`` — the check's gating condition excludes this
      run (e.g. wrong variant).
    - ``"warning"`` — advisory signal (Phase 6.1 notes-consistency
      check). Never affects the overall run status; surfaces in the
      Validator tab so operators can eyeball the disagreement.
    """
    name: str
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


def build_default_cross_checks() -> list:
    """Return a fresh list of the cross-checks the server runs on every merge.

    Instantiated per call so callers can't accidentally share check state
    across runs. Moved here from `server._build_default_cross_checks` in
    the peer-review round (I2) so the correction agent's internal re-run
    doesn't have to do a lazy `from server import …` — that cycle was
    only safe as long as every touch stayed deferred. The registry now
    lives in the same module as `run_all`, which is where a discoverer
    would expect it.
    """
    from cross_checks.sofp_balance import SOFPBalanceCheck
    from cross_checks.sopl_to_socie_profit import SOPLToSOCIEProfitCheck
    from cross_checks.soci_to_socie_tci import SOCIToSOCIETCICheck
    from cross_checks.socie_to_sofp_equity import SOCIEToSOFPEquityCheck
    from cross_checks.socf_to_sofp_cash import SOCFToSOFPCashCheck
    from cross_checks.sore_to_sofp_retained_earnings import (
        SoREToSOFPRetainedEarningsCheck,
    )
    return [
        SOFPBalanceCheck(),
        SOPLToSOCIEProfitCheck(),
        SOCIToSOCIETCICheck(),
        SOCIEToSOFPEquityCheck(),
        SOCFToSOFPCashCheck(),
        SoREToSOFPRetainedEarningsCheck(),
    ]


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
    filing_standard = run_config.get("filing_standard", "mfrs")
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

        # 3. Is this check defined for the current filing standard?
        #    MPERS-only checks (e.g. sore_to_sofp_retained_earnings) carry a
        #    narrowed set; gate them out on MFRS runs so they don't fire on
        #    filings that don't produce the necessary sheet.
        check_standards = getattr(
            check, "applies_to_standard", DEFAULT_APPLIES_TO_STANDARD,
        )
        if filing_standard not in check_standards:
            allowed = ", ".join(sorted(check_standards)).upper() or "(none)"
            results.append(CrossCheckResult(
                name=check.name,
                status="not_applicable",
                message=(
                    f"{check.name} only applies to {allowed} filings "
                    f"(this run is {filing_standard.upper()})"
                ),
            ))
            continue

        # 4. Does this check apply to the current variant configuration?
        if not check.applies_to(run_config):
            results.append(CrossCheckResult(
                name=check.name,
                status="not_applicable",
                message=f"{check.name} does not apply to the current variant configuration",
            ))
            continue

        # 5. Run the actual check — catch exceptions so one broken check
        # doesn't abort the entire validation pass.
        # Phase 5: pass `filing_standard` through so MPERS-aware checks
        # can branch on layout (MPERS SOCIE col 2 vs MFRS col 24). Uses
        # a try/except on TypeError so pre-Phase-5 check implementations
        # that haven't added the kwarg yet still run — they just lose
        # the standard signal, same as before.
        try:
            try:
                result = check.run(
                    workbook_paths, tolerance,
                    filing_level=filing_level,
                    filing_standard=filing_standard,
                )
            except TypeError:
                result = check.run(
                    workbook_paths, tolerance, filing_level=filing_level,
                )
        except Exception as e:
            result = CrossCheckResult(
                name=check.name,
                status="failed",
                message=f"Check raised an exception: {e}",
            )
        results.append(result)

    return results
