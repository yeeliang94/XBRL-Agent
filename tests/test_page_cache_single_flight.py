"""Peer-review [HIGH] — single-flight coalescing for the page cache.

The Phase 3 cache handed back stored bytes on repeat requests from ONE
sub-agent, but N parallel sub-agents racing on the same cold page all
missed and all rendered. These tests pin the new coalescing behaviour:

- Concurrent cold-miss requests for the same page render exactly once;
  every awaiter receives the same bytes.
- Render failures propagate uniformly to every awaiter (no silent
  partial success), and the in-flight entry is cleared so the next
  attempt starts clean.
- Duplicate page numbers in one request list are deduplicated before
  scheduling — same page in a single `_render_pages_async` call is
  rendered only once.
"""
from __future__ import annotations

import asyncio

import pytest

from notes import agent as notes_agent
from tools import page_cache


@pytest.fixture(autouse=True)
def _reset_caches():
    """Each test starts with a clean byte cache AND an empty in-flight
    map. Leaking either causes order-dependent failures."""
    page_cache.reset()
    notes_agent._reset_inflight_for_tests()
    yield
    page_cache.reset()
    notes_agent._reset_inflight_for_tests()


def test_concurrent_cold_miss_renders_exactly_once(monkeypatch):
    """Five concurrent requests for page 32 (cold) must produce ONE
    render, not five. Every awaiter receives the same bytes."""
    render_count = {"n": 0}

    def fake_render_single(pdf_path: str, page_num: int, dpi: int = 200):
        render_count["n"] += 1
        # Hold the render briefly so all 5 tasks have a chance to
        # install their Futures before the first one completes —
        # simulates a real rendering thread taking a few ms.
        import time
        time.sleep(0.02)
        return page_num, f"PNG:{page_num}".encode()

    monkeypatch.setattr(notes_agent, "_render_single_page", fake_render_single)

    async def _run():
        # Kick off 5 concurrent calls targeting page 32 specifically.
        coros = [notes_agent._render_pages_async("x.pdf", [32]) for _ in range(5)]
        return await asyncio.gather(*coros)

    results = asyncio.run(_run())
    assert render_count["n"] == 1, (
        "Expected single-flight to render page 32 exactly once across "
        "5 concurrent callers; got "
        f"{render_count['n']} renders"
    )
    # Every caller got identical bytes.
    first = results[0][32]
    assert all(r[32] == first for r in results)


def test_concurrent_cold_miss_for_different_pages_renders_each_once(monkeypatch):
    """Racing on pp 32 AND 33 must render each exactly once — two
    renders total across 5 callers. Confirms single-flight is per-page,
    not a global mutex."""
    renders: list[int] = []

    def fake_render_single(pdf_path, page_num, dpi=200):
        import time
        renders.append(page_num)
        time.sleep(0.01)
        return page_num, f"PNG:{page_num}".encode()

    monkeypatch.setattr(notes_agent, "_render_single_page", fake_render_single)

    async def _run():
        coros = [
            notes_agent._render_pages_async("x.pdf", [32, 33])
            for _ in range(5)
        ]
        return await asyncio.gather(*coros)

    results = asyncio.run(_run())
    assert sorted(renders) == [32, 33], f"expected one render each, got {renders}"
    for r in results:
        assert r[32] == b"PNG:32"
        assert r[33] == b"PNG:33"


def test_duplicate_page_in_one_request_renders_once(monkeypatch):
    """Even within a single call `_render_pages_async([32, 32, 33])`
    must render p32 once, not twice."""
    renders: list[int] = []

    def fake_render_single(pdf_path, page_num, dpi=200):
        renders.append(page_num)
        return page_num, f"PNG:{page_num}".encode()

    monkeypatch.setattr(notes_agent, "_render_single_page", fake_render_single)

    async def _run():
        return await notes_agent._render_pages_async("x.pdf", [32, 32, 33])

    res = asyncio.run(_run())
    assert sorted(renders) == [32, 33]
    assert res == {32: b"PNG:32", 33: b"PNG:33"}


def test_render_failure_propagates_to_every_awaiter_and_clears_inflight(monkeypatch):
    """If the render crashes, every concurrent awaiter must raise the
    same exception (no silent partial success), and the in-flight map
    must be cleared so a subsequent call can retry cleanly."""
    attempt = {"n": 0}

    def failing_render(pdf_path, page_num, dpi=200):
        attempt["n"] += 1
        # First (coalesced) attempt raises; a later caller gets a
        # clean slate and would render normally.
        if attempt["n"] == 1:
            import time
            time.sleep(0.01)  # give racers time to install awaiters
            raise RuntimeError("render blew up")
        return page_num, f"PNG:{page_num}".encode()

    monkeypatch.setattr(notes_agent, "_render_single_page", failing_render)

    async def _run_concurrent_failures():
        # 3 concurrent callers all targeting p 32 on the first attempt.
        coros = [notes_agent._render_pages_async("x.pdf", [32]) for _ in range(3)]
        results = await asyncio.gather(*coros, return_exceptions=True)
        return results

    results = asyncio.run(_run_concurrent_failures())
    # All three must have failed with the same error class/text.
    assert all(isinstance(r, RuntimeError) for r in results), results
    assert all("render blew up" in str(r) for r in results)
    # In-flight map is cleared — a fresh call must not see a stale Future.
    assert not notes_agent._inflight, (
        "in-flight map leaked after render failure: " + repr(notes_agent._inflight)
    )

    async def _run_retry():
        return await notes_agent._render_pages_async("x.pdf", [32])

    retry = asyncio.run(_run_retry())
    assert retry == {32: b"PNG:32"}
    assert attempt["n"] == 2  # one failed + one successful retry


def test_solo_render_failure_does_not_leak_unretrieved_exception(monkeypatch, caplog):
    """Peer-review MEDIUM: a render failure with ONLY the owner
    coroutine (no secondary waiters) must not leave an unretrieved
    exception on the Future. Otherwise asyncio logs "Future exception
    was never retrieved" when the Future is GC'd, burying real errors.

    We force a GC pass after the failure and assert the asyncio logger
    recorded no such warning.
    """
    import gc
    import logging

    def failing_render(pdf_path, page_num, dpi=200):
        raise RuntimeError("boom")

    monkeypatch.setattr(notes_agent, "_render_single_page", failing_render)

    async def _run():
        # Single caller — no secondary waiters to consume the exception
        # via their own await. This is the path the peer review flagged.
        try:
            await notes_agent._render_pages_async("x.pdf", [42])
        except RuntimeError:
            pass

    with caplog.at_level(logging.ERROR, logger="asyncio"):
        asyncio.run(_run())
        gc.collect()

    leaked = [r for r in caplog.records if "never retrieved" in r.getMessage()]
    assert not leaked, (
        f"asyncio logged an unretrieved exception: {[r.getMessage() for r in leaked]}"
    )


def test_second_call_after_successful_render_hits_byte_cache(monkeypatch):
    """After a cold render populates the cache, a subsequent call on
    the same page must NOT re-render — it hits the byte cache directly
    without going through the in-flight path."""
    renders: list[int] = []

    def fake_render_single(pdf_path, page_num, dpi=200):
        renders.append(page_num)
        return page_num, f"PNG:{page_num}".encode()

    monkeypatch.setattr(notes_agent, "_render_single_page", fake_render_single)

    async def _run():
        first = await notes_agent._render_pages_async("x.pdf", [32])
        second = await notes_agent._render_pages_async("x.pdf", [32])
        return first, second

    first, second = asyncio.run(_run())
    assert first == second
    assert renders == [32], (
        f"expected single render on the first call and a cache hit on the "
        f"second; got renders={renders}"
    )
