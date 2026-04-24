"""Tests for the per-turn LLM timeout in the notes agent runner.

Fixes a real-world hang: after a successful ``write_notes`` call the
LLM's next turn (the "final response" / trailing ``save_result`` tool
call) occasionally stalls for minutes, keeping the agent alive long
after its useful work is done. The sibling notes agents can sit like
this too, so the whole ``run_notes_extraction`` blocks on
``asyncio.wait(ALL_COMPLETED)`` even though every sheet's rows are on
disk. The fix: if the next node from ``agent.iter`` takes longer than
``NOTES_TURN_TIMEOUT``, bail out — treating the agent as succeeded if
it already wrote, failed otherwise.
"""
from __future__ import annotations

import asyncio

import pytest


class _SlowIterable:
    """Async iterator that blocks on the first ``__anext__`` forever.

    Stands in for ``agent.iter`` when the LLM is stuck waiting for the
    model to emit its next turn. Records cancellation so tests can
    assert the wait_for actually propagated a cancel into the stalled
    coroutine (otherwise the orphan would leak).
    """

    def __init__(self) -> None:
        self.cancelled: bool = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class _YieldingIterable:
    """Yields the given node objects one by one, then stops cleanly."""

    def __init__(self, nodes):
        self._nodes = list(nodes)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._nodes:
            raise StopAsyncIteration
        return self._nodes.pop(0)


@pytest.mark.asyncio
async def test_iter_with_turn_timeout_raises_when_next_node_stalls():
    """When ``__anext__`` blocks past the timeout, the helper must raise
    ``asyncio.TimeoutError`` so the caller can decide how to recover."""
    from notes.coordinator import _iter_with_turn_timeout

    slow = _SlowIterable()
    with pytest.raises(asyncio.TimeoutError):
        async for _ in _iter_with_turn_timeout(slow, timeout=0.05):
            pass
    assert slow.cancelled, (
        "the stalled __anext__ coroutine must be cancelled when wait_for times out "
        "so we don't leak a background task forever"
    )


@pytest.mark.asyncio
async def test_iter_with_turn_timeout_yields_all_nodes_when_fast():
    """Sub-timeout happy path: nodes stream normally, helper exits cleanly."""
    from notes.coordinator import _iter_with_turn_timeout

    received = []
    async for node in _iter_with_turn_timeout(
        _YieldingIterable(["a", "b", "c"]), timeout=1.0,
    ):
        received.append(node)
    assert received == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_single_agent_stall_after_write_returns_succeeded(tmp_path, monkeypatch):
    """End-to-end contract: when the LLM stalls after ``write_notes``
    succeeded, the runner must return a succeeded _SingleAgentOutcome
    (not re-raise) so the coordinator's sibling agents finish cleanly.

    Drives the real ``_invoke_single_notes_agent_once`` runner with a
    stubbed agent.iter that:
      1. yields nothing (simulating the LLM taking forever to produce
         the next turn),
      2. but the deps have ``wrote_once=True`` set as if write_notes
         had already succeeded on a previous turn.
    """
    from unittest.mock import patch
    from notes import coordinator as coord

    # Fake agent + deps so the runner doesn't need a real PDF / model.
    class _FakeTokenReport:
        def record_turn(self, *a, **kw): pass
    class _FakeDeps:
        wrote_once = True
        filled_path = str(tmp_path / "NOTES_CORP_INFO_filled.xlsx")
        write_skip_errors: list = []
        write_fuzzy_matches: list = []
        write_sanitizer_warnings: list = []
        cells_written: list = []
        token_report = _FakeTokenReport()
    class _FakeAgentRun:
        def __aiter__(self): return _SlowIterable()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def usage(self):
            class U:  # noqa: D401 — tiny value object
                total_tokens = 0
                request_tokens = 0
                response_tokens = 0
            return U()
    class _FakeAgent:
        def iter(self, *a, **kw): return _FakeAgentRun()

    # Write the "already done" file so filled_path stays truthful.
    (tmp_path / "NOTES_CORP_INFO_filled.xlsx").write_bytes(b"fake-xlsx")

    fake_deps = _FakeDeps()

    def fake_create_notes_agent(*a, **kw):
        return _FakeAgent(), fake_deps

    # Short timeout so the test doesn't actually wait 180s.
    monkeypatch.setattr(coord, "NOTES_TURN_TIMEOUT", 0.05)

    from notes_types import NotesTemplateType

    async def noop_emit(*a, **kw): pass

    with patch.object(coord, "create_notes_agent", side_effect=fake_create_notes_agent):
        outcome = await coord._invoke_single_notes_agent_once(
            template_type=NotesTemplateType.CORP_INFO,
            pdf_path="/tmp/fake.pdf",
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
            event_queue=None,
            agent_id="notes:CORP_INFO",
            emit=noop_emit,
            page_hints=None,
            page_offset=0,
        )

    assert outcome.filled_path == fake_deps.filled_path, (
        "stall-after-write must return the already-written workbook path"
    )


@pytest.mark.asyncio
async def test_single_agent_stall_before_write_raises(tmp_path, monkeypatch):
    """Mirror contract: a stall BEFORE any write is a real failure so the
    retry loop / coordinator treats the sheet as failed."""
    from unittest.mock import patch
    from notes import coordinator as coord

    class _FakeTokenReport:
        def record_turn(self, *a, **kw): pass
    class _FakeDeps:
        wrote_once = False  # never wrote
        filled_path = None
        write_skip_errors: list = []
        write_fuzzy_matches: list = []
        token_report = _FakeTokenReport()
    class _FakeAgentRun:
        def __aiter__(self): return _SlowIterable()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def usage(self):
            class U:
                total_tokens = 0
                request_tokens = 0
                response_tokens = 0
            return U()
    class _FakeAgent:
        def iter(self, *a, **kw): return _FakeAgentRun()

    def fake_create_notes_agent(*a, **kw):
        return _FakeAgent(), _FakeDeps()

    monkeypatch.setattr(coord, "NOTES_TURN_TIMEOUT", 0.05)

    from notes_types import NotesTemplateType

    async def noop_emit(*a, **kw): pass

    with patch.object(coord, "create_notes_agent", side_effect=fake_create_notes_agent), \
         pytest.raises(RuntimeError, match="stalled"):
        await coord._invoke_single_notes_agent_once(
            template_type=NotesTemplateType.CORP_INFO,
            pdf_path="/tmp/fake.pdf",
            inventory=[],
            filing_level="company",
            model="test",
            output_dir=str(tmp_path),
            event_queue=None,
            agent_id="notes:CORP_INFO",
            emit=noop_emit,
            page_hints=None,
            page_offset=0,
        )


@pytest.mark.asyncio
async def test_iter_with_turn_timeout_uses_constant_default():
    """``NOTES_TURN_TIMEOUT`` constant must exist and be reasonable.

    Sanity guard: we don't want a future edit to set it to 3 seconds
    (kills healthy runs) or remove it entirely.
    """
    from notes.coordinator import NOTES_TURN_TIMEOUT

    assert isinstance(NOTES_TURN_TIMEOUT, (int, float))
    assert 30 <= NOTES_TURN_TIMEOUT <= 600, (
        f"NOTES_TURN_TIMEOUT={NOTES_TURN_TIMEOUT} — must be between 30s and 600s "
        "(too short cancels healthy agents mid-think; too long defeats the point)"
    )
