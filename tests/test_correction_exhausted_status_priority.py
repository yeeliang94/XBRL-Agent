"""Peer-review #5 (MEDIUM, RUN-REVIEW follow-up): correction_exhausted
must beat the `completed` branch in the run-level status logic.

Failure mode the fix prevents:
- The CORRECTION pass exhausts its turn budget but happens to land
  enough writes to clear all cross-checks before bailing.
- Old branch order put `completed` first, so the exhausted run
  silently reports as a clean completion — operators have no signal
  that the corrector bailed early.
- New branch order checks `correction_exhausted` FIRST so the run
  always surfaces in History as "needs review" when the corrector
  hit its cap, regardless of whether checks went green coincidentally.

This test pins the BRANCH ORDER without booting the full server
pipeline. The status decision is a pure-Python computation; we
mirror it here against the matrix of (all_agents_ok, merge_success,
any_check_failed, correction_exhausted) inputs.
"""
from __future__ import annotations


def _compute_status(
    all_agents_ok: bool,
    merge_success: bool,
    any_check_failed: bool,
    correction_exhausted: bool,
) -> str:
    """Mirror of the branch tree at server.py around line 2300.
    Kept in sync by reading the comment block in server.py — if the
    code changes, this helper must be updated and the test will
    catch the contract change."""
    if all_agents_ok and merge_success and correction_exhausted:
        return "correction_exhausted"
    elif all_agents_ok and merge_success and not any_check_failed:
        return "completed"
    elif all_agents_ok and not merge_success:
        return "completed_with_errors"
    elif all_agents_ok and any_check_failed:
        return "completed_with_errors"
    else:
        return "failed"


def test_exhausted_with_green_checks_reports_correction_exhausted() -> None:
    """The canonical failure case the fix targets: corrector exhausted
    its budget but checks happen to be green now."""
    status = _compute_status(
        all_agents_ok=True, merge_success=True,
        any_check_failed=False, correction_exhausted=True,
    )
    assert status == "correction_exhausted", (
        "Exhausted runs must always surface as 'needs review' even "
        "when checks went green coincidentally"
    )


def test_exhausted_with_failed_checks_still_correction_exhausted() -> None:
    """The other exhausted scenario: checks didn't go green either."""
    status = _compute_status(
        all_agents_ok=True, merge_success=True,
        any_check_failed=True, correction_exhausted=True,
    )
    assert status == "correction_exhausted"


def test_clean_run_still_reports_completed() -> None:
    """Sanity: a run with no failed checks and no exhaustion still
    reports `completed`. Don't regress the happy path."""
    status = _compute_status(
        all_agents_ok=True, merge_success=True,
        any_check_failed=False, correction_exhausted=False,
    )
    assert status == "completed"


def test_failed_checks_without_exhaustion_reports_completed_with_errors() -> None:
    """Run with a failed cross-check but no correction exhaustion
    (e.g. correction cleared its turns successfully without
    converging, or correction wasn't triggered at all)."""
    status = _compute_status(
        all_agents_ok=True, merge_success=True,
        any_check_failed=True, correction_exhausted=False,
    )
    assert status == "completed_with_errors"


def test_merge_failure_reports_completed_with_errors() -> None:
    status = _compute_status(
        all_agents_ok=True, merge_success=False,
        any_check_failed=False, correction_exhausted=False,
    )
    assert status == "completed_with_errors"


def test_agent_failure_reports_failed() -> None:
    status = _compute_status(
        all_agents_ok=False, merge_success=True,
        any_check_failed=False, correction_exhausted=False,
    )
    assert status == "failed"


def test_branch_order_in_server_py() -> None:
    """Pin the actual branch ORDER in server.py via source inspection.
    A future edit that re-orders the branches and breaks the
    contract above must trip this test."""
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "server.py"
    text = src.read_text(encoding="utf-8")
    # The post-fix shape: correction_exhausted check appears BEFORE
    # the plain `completed` branch.
    exhausted_idx = text.find('overall_status = "correction_exhausted"')
    completed_idx = text.find('overall_status = "completed"')
    assert exhausted_idx > 0 and completed_idx > 0, (
        "Both branch markers must be present in server.py"
    )
    assert exhausted_idx < completed_idx, (
        "correction_exhausted branch must come BEFORE the completed "
        "branch — otherwise an exhausted run with green checks falls "
        "into completed and never surfaces for review"
    )
