"""PLAN-stop-and-validation-visibility Phase 3: wall-clock cap on the
correction pass.

Defence-in-depth on top of the dynamic turn cap (RUN-REVIEW P0-1) and
the per-turn timeout (180s). The slow-LLM scenario where every turn
takes 100s but the agent never reaches the turn cap or per-turn timeout
would otherwise loop for ~40 minutes (25 turns × 100s) before any cap
fires. A 5-minute wall-clock cap bounds total time even in that case.

Today the wall-clock check is missing entirely; this test pins the
contract.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from cross_checks.framework import CrossCheckResult
from statement_types import StatementType


def _make_failed_checks(n: int = 1):
    return [
        CrossCheckResult(
            name=f"check_{i}",
            status="failed",
            expected=100.0, actual=90.0, diff=-10.0, tolerance=1.0,
            message=f"synthetic failed check {i}",
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_correction_wallclock_cap_fires_on_slow_iter(monkeypatch, tmp_path):
    """A correction agent whose iter() takes longer than the wall-clock
    cap must abort and emit a structured error event with a recognizable
    type discriminator. The outcome dict must carry an
    ``error: "correction_wallclock_exceeded"`` flag.
    """
    from server import _run_correction_pass

    # Tighten the cap to 1 second so the test doesn't have to actually
    # wait 5 minutes. Use a monkeypatched constant — env override would
    # also work but the constant is simpler.
    monkeypatch.setattr(
        "server.CORRECTION_WALLCLOCK_TIMEOUT", 1.0, raising=False,
    )

    queue: asyncio.Queue = asyncio.Queue()

    # Stub create_correction_agent so the agent.iter() coroutine sleeps
    # past the wall-clock cap before yielding any node. This simulates
    # the slow-LLM scenario.
    class _FakeAgentRun:
        def __init__(self):
            self.ctx = object()
        def __aiter__(self):
            async def _gen():
                # Sleep just past the cap so the wallclock check fires
                # on the first iteration attempt. The cap path must
                # see this as a wall-clock breach, not a per-turn
                # timeout.
                await asyncio.sleep(1.5)
                # Should never be reached.
                yield object()  # pragma: no cover
            return _gen()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False

        class _Usage:
            request_tokens = 0
            response_tokens = 0
            total_tokens = 0
        def usage(self):
            return self._Usage()

    class _FakeAgent:
        def iter(self, prompt, deps=None):
            return _FakeAgentRun()

    class _FakeDeps:
        writes_performed = 0

    def _fake_create(*a, **k):
        return _FakeAgent(), _FakeDeps()

    monkeypatch.setattr(
        "correction.agent.create_correction_agent", _fake_create,
    )

    started = time.monotonic()
    outcome = await _run_correction_pass(
        failed_checks=_make_failed_checks(1),
        merged_workbook_path=str(tmp_path / "merged.xlsx"),
        pdf_path=str(tmp_path / "x.pdf"),
        infopack=None,
        filing_level="company",
        filing_standard="mfrs",
        model=object(),  # unused with stubbed agent
        output_dir=str(tmp_path),
        event_queue=queue,
        statements_to_run={StatementType.SOFP},
    )
    elapsed = time.monotonic() - started

    # Bounded by the cap + a small slack for test machine variance.
    assert elapsed < 5.0, (
        f"Correction pass should have aborted near the 1s cap; "
        f"actually took {elapsed:.2f}s"
    )
    assert outcome["error"] == "correction_wallclock_exceeded", (
        f"Expected outcome.error='correction_wallclock_exceeded', got "
        f"{outcome['error']!r}"
    )

    # Drain the queue and find the structured error event.
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    err_events = [
        e for e in events
        if e.get("event") == "error"
        and "wall-clock" in str(e.get("data", {}).get("message", "")).lower()
    ]
    assert err_events, (
        f"Expected an error SSE event mentioning 'wall-clock'; "
        f"got events={[e.get('event') for e in events]!r}"
    )
