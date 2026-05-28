"""Pinning test for the strict `_finalize_success` contract.

Peer-review finding (HIGH): success was previously granted whenever the
agent finished, regardless of whether `done()` was called or returned
"done". A conversational finish marked the run green; a `not_done`
result was demoted to `completed_with_residuals` but still treated as
success downstream. Both are now rejected.

Contract:
  - `done_result is None`              → status="failed", error names the cause.
  - `done_result["status"] == "done"`  → status="succeeded".
  - any other `done_result["status"]`  → status="failed", error names the cause.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from monolith.coordinator import _finalize_success
from monolith.tools import MonolithToolContext
from statement_types import StatementType


def _ctx(tmp_path: Path) -> MonolithToolContext:
    return MonolithToolContext(
        workbook_path=str(tmp_path / "monolith_filled.xlsx"),
        pdf_page_count=10,
        statements=list(StatementType),
    )


class _FakeRun:
    """Minimal stand-in for a PydanticAI agent_run carrying `.usage()`."""

    def usage(self):
        return SimpleNamespace(total_tokens=42)


def test_missing_done_result_fails(tmp_path):
    ctx = _ctx(tmp_path)
    out = _finalize_success(
        ctx=ctx,
        workbook_path=Path(ctx.workbook_path),
        agent_run=_FakeRun(),
        turn_records=[],
        done_result=None,
    )
    assert out.status == "failed"
    assert out.error and "did not finalise" in out.error
    assert out.failing_checks == []
    assert out.accepted_residuals == []


def test_not_done_result_fails(tmp_path):
    ctx = _ctx(tmp_path)
    out = _finalize_success(
        ctx=ctx,
        workbook_path=Path(ctx.workbook_path),
        agent_run=_FakeRun(),
        turn_records=[],
        done_result={
            "status": "not_done",
            "failing_checks": ["sofp_balance"],
            "accepted_residuals": [],
            "message": "Cannot finalise — sofp_balance still failing.",
        },
    )
    assert out.status == "failed"
    assert out.error and "sofp_balance" in out.error
    assert out.failing_checks == ["sofp_balance"]


def test_not_done_with_invalid_accepts_fails(tmp_path):
    """An accept_imbalance entry that failed server-side validation must
    register as failure, not success."""
    ctx = _ctx(tmp_path)
    out = _finalize_success(
        ctx=ctx,
        workbook_path=Path(ctx.workbook_path),
        agent_run=_FakeRun(),
        turn_records=[],
        done_result={
            "status": "not_done",
            "failing_checks": [],
            "accepted_residuals": [],
            "message": "Some accept_imbalance entries failed validation.",
            "invalid_accepts": [
                {
                    "entry": {"check_id": "x"},
                    "reason": "not currently failing",
                },
            ],
        },
    )
    assert out.status == "failed"
    assert out.error and "validation" in out.error


def test_done_status_succeeded(tmp_path):
    ctx = _ctx(tmp_path)
    out = _finalize_success(
        ctx=ctx,
        workbook_path=Path(ctx.workbook_path),
        agent_run=_FakeRun(),
        turn_records=[],
        done_result={
            "status": "done",
            "failing_checks": [],
            "accepted_residuals": [
                {"check_id": "sofp_balance", "reason": "rounding"},
            ],
        },
    )
    assert out.status == "succeeded"
    assert out.error is None
    assert out.accepted_residuals == [
        {"check_id": "sofp_balance", "reason": "rounding"},
    ]


def test_no_completed_with_residuals_status(tmp_path):
    """`completed_with_residuals` was the pre-fix soft-success label.
    The contract is now binary (succeeded | failed) so the server's
    finish_run_agent mapping doesn't need a third bucket."""
    ctx = _ctx(tmp_path)
    out = _finalize_success(
        ctx=ctx,
        workbook_path=Path(ctx.workbook_path),
        agent_run=_FakeRun(),
        turn_records=[],
        done_result={"status": "done"},
    )
    assert out.status == "succeeded"
    out2 = _finalize_success(
        ctx=ctx,
        workbook_path=Path(ctx.workbook_path),
        agent_run=_FakeRun(),
        turn_records=[],
        done_result={"status": "not_done", "failing_checks": ["x"]},
    )
    assert out2.status == "failed"
    assert out.status != "completed_with_residuals"
    assert out2.status != "completed_with_residuals"
