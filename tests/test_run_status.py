from __future__ import annotations

import pytest

from server import _resolve_run_status


_CLEAN = {
    "all_agents_ok": True,
    "merge_success": True,
    "correction_exhausted": False,
    "canonical_reexport_failed": False,
    "artifact_current": True,
    "any_check_failed": False,
    "cross_check_crashed": False,
    "open_conflicts": 0,
    "any_agent_flagged": False,
    "validator_failed": False,
    "reviewer_failed": False,
    "notes_coverage_unresolved": False,
    "notes_integrity_unresolved": False,
    "open_notes_placement_conflicts": False,
    "notes_formatting_incomplete": False,
}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, "completed"),
        ({"correction_exhausted": True}, "correction_exhausted"),
        (
            {"correction_exhausted": True, "any_check_failed": True},
            "correction_exhausted",
        ),
        ({"validator_failed": True}, "completed_with_errors"),
        ({"canonical_reexport_failed": True}, "completed_with_errors"),
        ({"artifact_current": False}, "completed_with_errors"),
        ({"cross_check_crashed": True}, "completed_with_errors"),
        ({"open_conflicts": 1}, "completed_with_errors"),
        ({"notes_formatting_incomplete": True}, "completed_with_errors"),
        ({"merge_success": False}, "completed_with_errors"),
        ({"all_agents_ok": False}, "failed"),
    ],
    ids=[
        "clean",
        "correction-exhausted",
        "correction-exhausted-with-failed-check",
        "validator-failed",
        "canonical-export-stale",
        "artifact-stale",
        "cross-check-crashed",
        "open-conflict",
        "formatting-incomplete",
        "merge-failed",
        "agent-failed",
    ],
)
def test_run_status_reflects_pipeline_outcomes(changes, expected):
    inputs = {**_CLEAN, **changes}
    assert _resolve_run_status(**inputs) == expected
