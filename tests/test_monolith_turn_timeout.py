"""Pinning test for the monolith inner-stream timeout (peer-review HIGH #2).

The monolith coordinator iterates `agent_run` AND each `node.stream(...)`
under `_iter_with_turn_timeout`. The outer wrap only bounds node
acquisition — once inside `node.stream(...)`, a provider that stalls
mid-stream on a single tool's output would hang the whole monolith
run until pydantic-ai's UsageLimitExceeded eventually fires (~50 req).

This test pins the helper's behaviour directly: a slow iterator that
yields past the timeout must raise `asyncio.TimeoutError`, never
silently block.
"""
from __future__ import annotations

import asyncio

import pytest

from monolith.coordinator import _iter_with_turn_timeout


class _SlowIter:
    """Async iterable that yields the first item immediately, then
    sleeps for `delay` seconds before each subsequent item."""

    def __init__(self, items, delay: float):
        self._items = list(items)
        self._delay = delay
        self._first = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        if not self._first:
            await asyncio.sleep(self._delay)
        self._first = False
        return self._items.pop(0)


@pytest.mark.asyncio
async def test_iter_with_turn_timeout_yields_until_stall():
    """Items that arrive faster than the timeout flow through; the
    moment a single __anext__ exceeds the timeout, the wrapper raises."""
    src = _SlowIter(["a", "b", "c"], delay=0.5)
    collected: list[str] = []

    with pytest.raises(asyncio.TimeoutError):
        async for item in _iter_with_turn_timeout(src, timeout=0.1):
            collected.append(item)

    # The first item arrived instantly (no sleep); subsequent items are
    # blocked behind a 0.5s sleep which exceeds the 0.1s timeout.
    assert collected == ["a"]


@pytest.mark.asyncio
async def test_iter_with_turn_timeout_passes_through_when_fast():
    """No timeout fires when every yield arrives within the budget."""
    src = _SlowIter(["x", "y", "z"], delay=0.01)
    collected: list[str] = []

    async for item in _iter_with_turn_timeout(src, timeout=1.0):
        collected.append(item)

    assert collected == ["x", "y", "z"]
