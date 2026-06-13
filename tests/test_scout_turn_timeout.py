"""PLAN-orchestration-hardening items 1+2 — scout timeout harness + trace.

The scout was the only agent with no per-turn timeout, no wall-clock cap,
and no trace persistence. These tests pin:

  * a stalled model turn terminates within SCOUT_TURN_TIMEOUT + ε with a
    structured ``scout_timeout`` error event and a degraded-but-valid empty
    Infopack (gotcha #13 — degradation, never page restrictions);
  * the whole-run wall-clock cap fires even when individual turns are fast;
  * every STREAMING scout invocation — success, timeout — leaves
    ``SCOUT_conversation_trace.json`` in the output dir (gotcha #6). The
    legacy non-streaming ``run_scout`` cannot trace its timeout exit
    (``agent.run`` is opaque; the cancelled coroutine leaves no reachable
    history — see its docstring), so its test asserts the bounded return
    only.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import scout.agent as scout_agent
from scout.infopack import Infopack


class _FakeAgentRun:
    """Stands in for the value of ``async with agent.iter(...)``."""

    def __init__(self, node_delay: float = 3600.0, n_nodes: int = 0):
        self._node_delay = node_delay
        self._remaining = n_nodes
        self.result = None
        self.ctx = SimpleNamespace(
            state=SimpleNamespace(message_history=[]),
        )

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._remaining <= 0 and self._node_delay >= 3600:
            await asyncio.sleep(self._node_delay)  # stalled model turn
        if self._remaining <= 0:
            raise StopAsyncIteration
        self._remaining -= 1
        await asyncio.sleep(self._node_delay)
        return object()  # neither call-tools nor model-request node


class _FakeAgent:
    def __init__(self, run: _FakeAgentRun):
        self._run = run

    def iter(self, prompt, deps=None):
        run = self._run

        class _Ctx:
            async def __aenter__(self):
                return run

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _fake_factory(run: _FakeAgentRun, pdf_path):
    deps = scout_agent.ScoutDeps(
        pdf_path=pdf_path, pdf_length=3,
        statements_to_find=None, on_progress=None,
    )

    def factory(**_kwargs):
        return _FakeAgent(run), deps

    return factory, deps


@pytest.mark.asyncio
async def test_stalled_scout_turn_times_out_structured(tmp_path, monkeypatch):
    """A model that never responds terminates within the per-turn timeout
    with a structured error event — not an eternal hang."""
    monkeypatch.setattr(scout_agent, "SCOUT_TURN_TIMEOUT", 0.3)
    monkeypatch.setattr(scout_agent, "SCOUT_WALLCLOCK_TIMEOUT", 60.0)

    run = _FakeAgentRun(node_delay=3600.0)  # first __anext__ stalls forever
    factory, _deps = _fake_factory(run, tmp_path / "f.pdf")
    monkeypatch.setattr(scout_agent, "create_scout_agent", factory)

    events: list[tuple[str, dict]] = []

    async def on_event(event_type, data):
        events.append((event_type, data))

    start = time.monotonic()
    infopack = await scout_agent.run_scout_streaming(
        pdf_path=tmp_path / "f.pdf", model="test",
        on_event=on_event, output_dir=str(tmp_path),
    )
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"scout did not terminate promptly ({elapsed:.1f}s)"
    # Degraded-but-valid empty Infopack — never None, never page limits.
    assert isinstance(infopack, Infopack)
    assert infopack.statements == {}
    # Honesty (Codex review): the pack is flagged degraded so the upload
    # route reports the scout as failed, not a successful empty scout.
    assert infopack.degraded is True
    assert infopack.degraded_reason and "scout" in infopack.degraded_reason.lower()
    # Structured SSE error so the upload page shows the failure.
    error_events = [d for t, d in events if t == "error"]
    assert error_events, f"no error event emitted; events: {events}"
    assert error_events[0].get("type") == "scout_timeout"
    assert "proceed without scout hints" in error_events[0]["message"]
    # Item 2: the timeout path persists the partial trace.
    trace = tmp_path / "SCOUT_conversation_trace.json"
    assert trace.exists(), "timeout path must leave a scout trace on disk"
    assert "messages" in json.loads(trace.read_text(encoding="utf-8"))


class _StallingNodeStream:
    """``node.stream(ctx)`` context manager whose iteration never yields —
    models a provider that opens the HTTP stream then stalls mid-stream."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(3600)  # stalled mid-stream


def _stalling_stream_node(node_cls):
    """An instance of the REAL pydantic-ai node class (so the scout loop's
    ``Agent.is_call_tools_node`` / ``is_model_request_node`` isinstance
    dispatch routes it into the streaming branch) whose ``stream()`` stalls."""

    class _Node(node_cls):
        def __init__(self):  # bypass the dataclass init — only stream() is used
            pass

        def stream(self, ctx):
            return _StallingNodeStream()

    return _Node()


class _StreamStallRun:
    """Agent run that yields ONE node promptly; the stall is INSIDE
    ``node.stream()``, not in ``__anext__`` — pre-fix, the outer
    ``iter_with_turn_timeout`` wrap never saw it and the scout hung forever."""

    def __init__(self, node):
        self._node = node
        self._yielded = False
        self.result = None
        self.ctx = SimpleNamespace(
            state=SimpleNamespace(message_history=[]),
        )

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._yielded:
            raise StopAsyncIteration
        self._yielded = True
        return self._node


@pytest.mark.asyncio
@pytest.mark.parametrize("node_kind", ["model_request", "call_tools"])
async def test_stall_inside_node_stream_times_out(tmp_path, monkeypatch, node_kind):
    """A provider that stalls MID-STREAM (inside ``node.stream()``) hits the
    same per-turn timeout as one that stalls between nodes — the inner
    tool/model streams are wrapped too (agent_runner helper contract)."""
    from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode

    monkeypatch.setattr(scout_agent, "SCOUT_TURN_TIMEOUT", 0.3)
    monkeypatch.setattr(scout_agent, "SCOUT_WALLCLOCK_TIMEOUT", 60.0)

    node_cls = ModelRequestNode if node_kind == "model_request" else CallToolsNode
    run = _StreamStallRun(_stalling_stream_node(node_cls))
    factory, _deps = _fake_factory(run, tmp_path / "f.pdf")  # type: ignore[arg-type]
    monkeypatch.setattr(scout_agent, "create_scout_agent", factory)

    events: list[tuple[str, dict]] = []

    async def on_event(event_type, data):
        events.append((event_type, data))

    start = time.monotonic()
    infopack = await scout_agent.run_scout_streaming(
        pdf_path=tmp_path / "f.pdf", model="test",
        on_event=on_event, output_dir=str(tmp_path),
    )
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"scout hung on a mid-stream stall ({elapsed:.1f}s)"
    assert isinstance(infopack, Infopack)
    error_events = [d for t, d in events if t == "error"]
    assert error_events and error_events[0].get("type") == "scout_timeout"
    # The trace persists on this path too (saved BEFORE the error emit).
    assert (tmp_path / "SCOUT_conversation_trace.json").exists()


@pytest.mark.asyncio
async def test_timeout_trace_saved_even_when_error_emit_raises(tmp_path, monkeypatch):
    """Item 2 fix: a raising on_event (disconnected SSE client) must NOT skip
    the partial-trace save — the save runs BEFORE the error emit."""
    monkeypatch.setattr(scout_agent, "SCOUT_TURN_TIMEOUT", 0.3)
    run = _FakeAgentRun(node_delay=3600.0)
    factory, _deps = _fake_factory(run, tmp_path / "f.pdf")
    monkeypatch.setattr(scout_agent, "create_scout_agent", factory)

    async def raising_on_event(event_type, data):
        raise ConnectionError("SSE client disconnected")

    with pytest.raises(ConnectionError):
        await scout_agent.run_scout_streaming(
            pdf_path=tmp_path / "f.pdf", model="test",
            on_event=raising_on_event, output_dir=str(tmp_path),
        )
    trace = tmp_path / "SCOUT_conversation_trace.json"
    assert trace.exists(), "trace must be on disk even when the emit raises"


@pytest.mark.asyncio
async def test_scout_wallclock_cap_fires_between_fast_turns(tmp_path, monkeypatch):
    """Many quick-but-not-quick-enough turns trip the whole-run cap."""
    monkeypatch.setattr(scout_agent, "SCOUT_TURN_TIMEOUT", 60.0)
    monkeypatch.setattr(scout_agent, "SCOUT_WALLCLOCK_TIMEOUT", 0.3)

    run = _FakeAgentRun(node_delay=0.15, n_nodes=100)
    factory, _deps = _fake_factory(run, tmp_path / "f.pdf")
    monkeypatch.setattr(scout_agent, "create_scout_agent", factory)

    events: list[tuple[str, dict]] = []

    async def on_event(event_type, data):
        events.append((event_type, data))

    start = time.monotonic()
    infopack = await scout_agent.run_scout_streaming(
        pdf_path=tmp_path / "f.pdf", model="test",
        on_event=on_event, output_dir=str(tmp_path),
    )
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    assert isinstance(infopack, Infopack)
    error_events = [d for t, d in events if t == "error"]
    assert error_events and error_events[0].get("type") == "scout_timeout"
    assert "wall-clock" in error_events[0]["message"]


@pytest.mark.asyncio
async def test_scout_timeout_keeps_already_saved_infopack(tmp_path, monkeypatch):
    """If the scout saved a valid infopack BEFORE stalling, the timeout path
    returns it rather than discarding the work."""
    monkeypatch.setattr(scout_agent, "SCOUT_TURN_TIMEOUT", 0.3)
    run = _FakeAgentRun(node_delay=3600.0)
    factory, deps = _fake_factory(run, tmp_path / "f.pdf")
    monkeypatch.setattr(scout_agent, "create_scout_agent", factory)
    saved = Infopack(toc_page=2, page_offset=4)
    deps.infopack = saved

    infopack = await scout_agent.run_scout_streaming(
        pdf_path=tmp_path / "f.pdf", model="test",
    )
    assert infopack is saved


@pytest.mark.asyncio
async def test_scout_success_path_saves_trace(tmp_path, monkeypatch):
    """Item 2: a successful scout run leaves the trace file too."""
    run = _FakeAgentRun(node_delay=0.0, n_nodes=1)
    factory, deps = _fake_factory(run, tmp_path / "f.pdf")
    monkeypatch.setattr(scout_agent, "create_scout_agent", factory)
    deps.infopack = Infopack(toc_page=1, page_offset=0)

    infopack = await scout_agent.run_scout_streaming(
        pdf_path=tmp_path / "f.pdf", model="test",
        output_dir=str(tmp_path),
    )
    assert isinstance(infopack, Infopack)
    trace = tmp_path / "SCOUT_conversation_trace.json"
    assert trace.exists(), "success path must leave a scout trace on disk"


@pytest.mark.asyncio
async def test_non_streaming_run_scout_wallclock(tmp_path, monkeypatch):
    """Item 1 step 4: the CLI-style ``run_scout`` is bounded too."""
    monkeypatch.setattr(scout_agent, "SCOUT_WALLCLOCK_TIMEOUT", 0.3)

    class _HangingAgent:
        async def run(self, prompt, deps=None):
            await asyncio.sleep(3600)

    deps = scout_agent.ScoutDeps(
        pdf_path=tmp_path / "f.pdf", pdf_length=3,
        statements_to_find=None, on_progress=None,
    )
    monkeypatch.setattr(
        scout_agent, "create_scout_agent",
        lambda **_kwargs: (_HangingAgent(), deps),
    )

    start = time.monotonic()
    infopack = await scout_agent.run_scout(
        pdf_path=tmp_path / "f.pdf", model="test",
    )
    assert time.monotonic() - start < 5.0
    assert isinstance(infopack, Infopack)
    assert infopack.statements == {}
