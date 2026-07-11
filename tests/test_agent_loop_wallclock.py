"""Item 17 spec-level pin — wall-clock is first-class in run_agent_loop.

One implementation of the whole-run wall-clock check serves every caller
that passes ``AgentLoopSpec.wallclock_timeout`` (face, reviewer, notes
validator); per-caller exception mapping stays local. The notes
coordinator's ``_iter_with_turn_timeout`` re-export (its test import
contract) survives the consolidation.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent_runner import AgentLoopSpec, WallclockExceeded, run_agent_loop


def _usage():
    return SimpleNamespace(
        total_tokens=1, input_tokens=1, output_tokens=0,
        cache_read_tokens=0, cache_write_tokens=0,
    )


class _SlowTurningRun:
    """Generic nodes forever, each fast enough to dodge the per-turn cap."""

    result = None
    ctx = SimpleNamespace(state=SimpleNamespace(message_history=[]))

    @property
    def usage(self):
        return _usage()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0.05)
        return object()


@pytest.mark.asyncio
async def test_wallclock_fires_for_any_caller_spec():
    spec = AgentLoopSpec(
        agent_role="ANY", model="test-model", turn_timeout=60.0,
        phase_map={}, phase_message=lambda r, p: "",
        max_iters=10_000, wallclock_timeout=0.3,
    )

    async def emit(_t, _d):
        pass

    start = time.monotonic()
    with pytest.raises(WallclockExceeded) as excinfo:
        await run_agent_loop(_SlowTurningRun(), MagicMock(), spec, emit, [])
    assert time.monotonic() - start < 5.0
    assert "wall-clock cap" in str(excinfo.value)
    assert "ANY" in str(excinfo.value)


@pytest.mark.asyncio
async def test_wallclock_none_never_fires():
    spec = AgentLoopSpec(
        agent_role="ANY", model="test-model", turn_timeout=60.0,
        phase_map={}, phase_message=lambda r, p: "",
        max_iters=3, wallclock_timeout=None,
    )

    async def emit(_t, _d):
        pass

    # Runs into the iteration cap, never the (disabled) wall-clock.
    from agent_runner import IterationLimitReached

    with pytest.raises(IterationLimitReached):
        await run_agent_loop(_SlowTurningRun(), MagicMock(), spec, emit, [])


def test_notes_turn_timeout_reexport_survives():
    """notes/coordinator.py's `_iter_with_turn_timeout` alias is a test
    import contract (tests/test_notes_turn_timeout.py) — the item-17
    consolidation must keep it."""
    from agent_runner import iter_with_turn_timeout
    from notes.coordinator import _iter_with_turn_timeout

    assert _iter_with_turn_timeout is iter_with_turn_timeout
