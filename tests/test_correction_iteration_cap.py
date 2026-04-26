"""RUN-REVIEW P0-1 (2026-04-26): correction agent dynamic-cap test.

Pre-P0-1 the correction loop relied on pydantic-ai's silent default
`request_limit=50` and emitted no distinct status when it fired.
This test exercises `_run_correction_pass` end-to-end against the
`inspect_flood_model` fixture from `tests/fixtures/run_review/`,
asserting:

1. The new dynamic cap fires BEFORE pydantic-ai's hidden 50-cap.
2. The outcome dict carries `exhausted=True` so server.py can flip
   the run-level status to `correction_exhausted`.
3. The cap formula scales with run shape — Group filings get more
   headroom than Company filings.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cross_checks.framework import CrossCheckResult
from statement_types import StatementType
from tests.fixtures.run_review.correction_models import (
    diff_first_model,
    inspect_flood_model,
)


def _make_failed_checks(n: int = 1):
    return [
        CrossCheckResult(
            name=f"check_{i}",
            status="failed",
            expected=100.0,
            actual=90.0,
            diff=-10.0,
            tolerance=1.0,
            message=f"synthetic failed check {i}",
        )
        for i in range(n)
    ]


def _drain(queue: asyncio.Queue) -> list:
    """Drain whatever's in the queue without blocking."""
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


@pytest.mark.asyncio
async def test_inspect_flood_trips_dynamic_cap(tmp_path: Path) -> None:
    """A model that emits 30 inspect calls must trip the new cap long
    before pydantic-ai's hidden 50-request limit fires. The outcome
    must carry exhausted=True with the actual budget recorded."""
    from server import _run_correction_pass

    queue: asyncio.Queue = asyncio.Queue()

    # Use a path that doesn't exist — the agent never actually opens it
    # because the FunctionModel returns scripted ToolCalls; whatever the
    # tools do with the path is irrelevant for cap-firing semantics.
    outcome = await _run_correction_pass(
        failed_checks=_make_failed_checks(1),
        merged_workbook_path=str(tmp_path / "merged.xlsx"),
        pdf_path=str(tmp_path / "x.pdf"),
        infopack=None,
        filing_level="company",
        filing_standard="mfrs",
        model=inspect_flood_model(n=30),
        output_dir=str(tmp_path),
        event_queue=queue,
        statements_to_run={StatementType.SOFP},
    )

    assert outcome["invoked"] is True
    assert outcome["exhausted"] is True
    # Company + 1 failed check → max_turns = 8 + 0 + 2 = 10
    assert outcome["max_turns"] == 10, (
        f"Company/1-check budget should be 10, got {outcome['max_turns']}"
    )
    assert outcome["turns_used"] >= outcome["max_turns"]
    assert outcome["error"] == "correction_exhausted"


@pytest.mark.asyncio
async def test_group_run_gets_larger_budget(tmp_path: Path) -> None:
    """Group filings get +4 turns over Company because there's more
    cells / two column-sets to reconcile. This test pins the formula
    against MPERS Group with 2 failed checks: 8 + 4 + 4 = 16 turns."""
    from server import _run_correction_pass

    queue: asyncio.Queue = asyncio.Queue()
    outcome = await _run_correction_pass(
        failed_checks=_make_failed_checks(2),
        merged_workbook_path=str(tmp_path / "merged.xlsx"),
        pdf_path=str(tmp_path / "x.pdf"),
        infopack=None,
        filing_level="group",
        filing_standard="mpers",
        model=inspect_flood_model(n=30),
        output_dir=str(tmp_path),
        event_queue=queue,
        statements_to_run={StatementType.SOFP},
    )
    assert outcome["max_turns"] == 16, (
        f"Group/2-check budget should be 16 (8 + 4 group + 2*2 checks), "
        f"got {outcome['max_turns']}"
    )
    assert outcome["exhausted"] is True


@pytest.mark.asyncio
async def test_budget_clamped_to_25_max(tmp_path: Path) -> None:
    """Even Group + many failed checks shouldn't exceed 25 turns.
    With 10 failed checks: 8 + 4 + 20 = 32 → clamped to 25."""
    from server import _run_correction_pass

    queue: asyncio.Queue = asyncio.Queue()
    outcome = await _run_correction_pass(
        failed_checks=_make_failed_checks(10),
        merged_workbook_path=str(tmp_path / "merged.xlsx"),
        pdf_path=str(tmp_path / "x.pdf"),
        infopack=None,
        filing_level="group",
        filing_standard="mfrs",
        model=inspect_flood_model(n=30),
        output_dir=str(tmp_path),
        event_queue=queue,
        statements_to_run={StatementType.SOFP},
    )
    assert outcome["max_turns"] == 25, (
        "Budget must clamp to 25 even when the formula would compute higher"
    )


@pytest.mark.asyncio
async def test_diff_first_model_completes_under_budget(tmp_path: Path) -> None:
    """A well-behaved model that does ONE inspect → ONE fill → ONE verify
    finishes well under any budget — verifies the cap doesn't false-fire
    on legitimate convergence."""
    from server import _run_correction_pass

    queue: asyncio.Queue = asyncio.Queue()
    outcome = await _run_correction_pass(
        failed_checks=_make_failed_checks(1),
        merged_workbook_path=str(tmp_path / "merged.xlsx"),
        pdf_path=str(tmp_path / "x.pdf"),
        infopack=None,
        filing_level="company",
        filing_standard="mfrs",
        model=diff_first_model(),
        output_dir=str(tmp_path),
        event_queue=queue,
        statements_to_run={StatementType.SOFP},
    )
    # exhausted should NOT be set on a converging run
    assert outcome["exhausted"] is False
    assert outcome["error"] != "correction_exhausted"
