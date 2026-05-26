"""Phase 4 step 4.4 — per-scope cross-check runner.

CLAUDE.md gotcha #12: Group filings have FOUR value columns (B=Group
CY, C=Group PY, D=Company CY, E=Company PY).  A cross-check that runs
once against the workbook can only see one half of the data — either
the Group columns or the Company columns, depending on how the
underlying check is wired.

The canonical pipeline solves this by routing every check through
this helper, which runs the entire registry twice for Group filings:

  pass 1: filing_level='group'   → Group columns
  pass 2: filing_level='company' → Company columns

Each pass's results are tagged with the scope they ran under so the
Validator tab can render two distinct result sets.  Company-only
filings short-circuit to a single pass.
"""
from __future__ import annotations

from typing import Any, Callable

from cross_checks.framework import CrossCheckResult, run_all


def run_cross_checks_per_scope(
    *,
    checks: list[Any],
    workbook_paths: dict,
    run_config: dict,
    tolerance: float = 1.0,
    on_check: Callable[[int, int, CrossCheckResult], None] | None = None,
) -> list[CrossCheckResult]:
    """Run the cross-check registry once per ``entity_scope``.

    For Group filings: runs the registry twice (Group then Company),
    tagging each ``CrossCheckResult.name`` with the scope so the UI
    can group them.  For Company filings: short-circuits to the
    single-pass legacy behaviour — no scope tag is added since there
    is no second pass to disambiguate from.
    """
    filing_level = (run_config.get("filing_level") or "company").lower()

    if filing_level != "group":
        return run_all(
            checks,
            workbook_paths=workbook_paths,
            run_config=run_config,
            tolerance=tolerance,
            on_check=on_check,
        )

    out: list[CrossCheckResult] = []
    for scope in ("group", "company"):
        scoped_config = {**run_config, "filing_level": scope}
        scoped_results = run_all(
            checks,
            workbook_paths=workbook_paths,
            run_config=scoped_config,
            tolerance=tolerance,
            on_check=on_check,
        )
        # Tag each result with the scope so the UI can render two
        # result sets without conflating them.  The tag rides in the
        # check name's suffix; ValidatorTab grouping branches on it.
        for r in scoped_results:
            out.append(
                CrossCheckResult(
                    name=f"{r.name} [{scope}]",
                    status=r.status,
                    expected=r.expected,
                    actual=r.actual,
                    diff=r.diff,
                    tolerance=r.tolerance,
                    message=r.message,
                )
            )
    return out
