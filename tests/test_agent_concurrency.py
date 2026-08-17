"""agent_concurrency (Step 7): the shared slot used by both coordinators."""
from __future__ import annotations

import asyncio

import pytest

import agent_concurrency as ac


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv(ac.ENV_VAR, raising=False)
    ac._semaphores.clear()
    yield
    ac._semaphores.clear()


def test_cap_parsing(monkeypatch):
    assert ac.max_concurrent_agents() == 0
    monkeypatch.setenv(ac.ENV_VAR, "3")
    assert ac.max_concurrent_agents() == 3
    monkeypatch.setenv(ac.ENV_VAR, "-1")
    assert ac.max_concurrent_agents() == 0
    monkeypatch.setenv(ac.ENV_VAR, "lots")
    assert ac.max_concurrent_agents() == 0  # bad config never stalls a run


@pytest.mark.asyncio
async def test_notes_gate_honours_cap_and_closes_queued_runner_on_cancel(monkeypatch):
    from notes.coordinator import _run_gated

    monkeypatch.setenv(ac.ENV_VAR, "1")
    release = asyncio.Event()

    async def hold():
        await release.wait()
        return "done"

    async def never_started():
        raise AssertionError("queued runner must not start")  # pragma: no cover

    holder = asyncio.create_task(_run_gated("a", hold()))
    await asyncio.sleep(0)  # holder takes the only slot
    queued_coro = never_started()
    queued = asyncio.create_task(_run_gated("b", queued_coro))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    release.set()
    assert await holder == "done"
    # The never-started coroutine was closed, so no "never awaited" warning.
    assert queued_coro.cr_frame is None


@pytest.mark.asyncio
async def test_semaphore_is_per_event_loop(monkeypatch):
    """gotcha #2a — a semaphore belongs to the loop that created it."""
    monkeypatch.setenv(ac.ENV_VAR, "2")

    async def _use():
        async with ac.agent_slot("x"):
            return id(asyncio.get_running_loop())

    a = await _use()
    b = await asyncio.to_thread(lambda: asyncio.run(_use()))
    assert a != b
    assert len(ac._semaphores) == 2
