"""Peer-review HIGH (Phase 6.2 follow-up): a notes-validator failure must tip
an otherwise-green run to ``completed_with_errors``.

Failure mode the fix prevents:
- The notes post-validator pass fails (construction error, wall-clock, EOFError
  race, ...). Its pseudo-agent ``run_agents`` row is stamped ``failed``.
- But ``all_agents_ok`` only covers extraction agents + notes EXTRACTION
  agents — not the validator pass — and ``overall_status`` never folded the
  validator outcome in. So the run badge showed a clean ``completed`` sitting
  over a ``failed`` sub-agent: internally inconsistent and dishonest.
- The validator is a soft-fail pass (gotcha #22) — output is intact, dedup
  just didn't run — but it's still a needs-review signal, mirrored on the
  same ``completed_with_errors`` treatment as open_conflicts / any_agent_flagged.

Mirrors the pattern of ``test_correction_exhausted_status_priority.py``: a
pure-Python mirror of the relevant branch subset + a source-inspection pin so a
future edit that drops the guard trips the test.
"""
from __future__ import annotations

from pathlib import Path


def _compute_status(
    all_agents_ok: bool,
    merge_success: bool,
    any_check_failed: bool,
    validator_failed: bool,
) -> str:
    """Mirror of the relevant branch subset in server.py's overall_status
    tree (the signals this test exercises; correction_exhausted /
    canonical_reexport_failed / open_conflicts / any_agent_flagged are held
    clean here). Kept in sync with the code by the source-inspection test
    below."""
    if (all_agents_ok and merge_success and not any_check_failed
            and not validator_failed):
        return "completed"
    elif all_agents_ok and not merge_success:
        return "completed_with_errors"
    elif all_agents_ok and merge_success and validator_failed:
        return "completed_with_errors"
    elif all_agents_ok and any_check_failed:
        return "completed_with_errors"
    else:
        return "failed"


def test_validator_failure_with_green_checks_tips_to_completed_with_errors() -> None:
    """The canonical case: everything clean except the validator pass failed."""
    status = _compute_status(
        all_agents_ok=True, merge_success=True,
        any_check_failed=False, validator_failed=True,
    )
    assert status == "completed_with_errors", (
        "A failed notes-validator pass must surface as 'needs review', not a "
        "clean 'completed' badge sitting over a failed sub-agent"
    )


def test_clean_run_without_validator_failure_still_completed() -> None:
    """Don't regress the happy path: no validator failure → completed."""
    status = _compute_status(
        all_agents_ok=True, merge_success=True,
        any_check_failed=False, validator_failed=False,
    )
    assert status == "completed"


def test_validator_failure_folded_into_overall_status_in_server_py() -> None:
    """Pin the actual code: the completed branch must EXCLUDE validator_failed
    and a dedicated completed_with_errors branch must handle it. A future edit
    that drops either guard trips this test."""
    src = Path(__file__).resolve().parent.parent / "server.py"
    text = src.read_text(encoding="utf-8")
    assert "validator_failed = bool(" in text, (
        "validator_failed must be computed from validator_outcome"
    )
    # The completed branch's guard excludes a validator failure.
    assert "and not any_agent_flagged and not validator_failed" in text, (
        "the 'completed' branch must exclude validator_failed"
    )
    # And there's a dedicated branch mapping it to completed_with_errors.
    assert "merge_result.success and validator_failed" in text, (
        "a validator failure must have its own completed_with_errors branch"
    )
