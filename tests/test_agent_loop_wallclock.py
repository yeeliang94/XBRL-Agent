"""Item 17 spec-level pin — wall-clock is first-class in run_agent_loop.

One implementation of the whole-run wall-clock check serves every caller
that passes ``AgentLoopSpec.wallclock_timeout`` (face, reviewer, notes
validator); per-caller exception mapping stays local. The notes
coordinator's ``_iter_with_turn_timeout`` re-export (its test import
contract) survives the consolidation.

Run-83 hardening (Phase 2 Step 3): the cap stops NEW MODEL THINKING, not
the execution of a tool call the model already issued — the breach fires
only before model-request nodes. Run 83's reviewer diagnosed correctly
for 364s, emitted a three-fix correction batch, and lost it because the
old check raised on the pending call-tools node.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import agent_runner
from agent_runner import AgentLoopSpec, WallclockExceeded, run_agent_loop


def _usage():
    return SimpleNamespace(
        total_tokens=1, input_tokens=1, output_tokens=0,
        cache_read_tokens=0, cache_write_tokens=0,
    )


class _ModelNode:
    """Stands in for a pydantic-ai model-request node (via _AgentShim)."""


class _EmptyStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _ToolsNode:
    """Stands in for a call-tools node; records whether it executed."""

    def __init__(self):
        self.executed = False

    def stream(self, _ctx):
        node = self

        class _CM:
            async def __aenter__(self):
                node.executed = True
                return _EmptyStream()

            async def __aexit__(self, *args):
                return False

        return _CM()


class _AgentShim:
    """Node-kind oracle the loop consults; lets tests use plain fakes."""

    @staticmethod
    def is_model_request_node(node):
        return isinstance(node, _ModelNode)

    @staticmethod
    def is_call_tools_node(node):
        return isinstance(node, (_ToolsNode, _StallingToolsNode))

    @staticmethod
    def is_end_node(node):
        return False


class _ScriptedRun:
    """Yields a scripted node sequence, 0.05s apart (dodges per-turn cap)."""

    result = None
    ctx = SimpleNamespace(state=SimpleNamespace(message_history=[]))

    def __init__(self, nodes):
        self._nodes = list(nodes)

    @property
    def usage(self):
        return _usage()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._nodes:
            raise StopAsyncIteration
        await asyncio.sleep(0.05)
        item = self._nodes.pop(0)
        if callable(item) and not isinstance(item, (_ModelNode, _ToolsNode)):
            item = item()  # allow lazy actions (e.g. a sleep) in the script
        return item


class _SlowTurningRun:
    """Generic nodes forever, each fast enough to dodge the per-turn cap.

    Generic nodes are neither call-tools nor End, so the run-83 grace rule
    does NOT apply — the cap must still fire (the
    40-slow-but-compliant-turns scenario the wall-clock exists to bound).
    """

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
async def test_issued_tool_node_executes_past_the_cap(monkeypatch):
    """Run-83 Phase 2 Step 3: a call-tools node the model already produced
    executes even after the deadline; the loop raises before the NEXT
    model request instead."""
    monkeypatch.setattr(agent_runner, "Agent", _AgentShim)
    tools_node = _ToolsNode()

    async def _sleep_past_cap():
        await asyncio.sleep(0.35)
        return tools_node

    class _GraceRun(_ScriptedRun):
        async def __anext__(self):
            if not self._nodes:
                raise StopAsyncIteration
            item = self._nodes.pop(0)
            if item is tools_node:
                await asyncio.sleep(0.35)  # cross the 0.3s cap first
            else:
                await asyncio.sleep(0.01)
            return item

    run = _GraceRun([_ModelNode(), tools_node, _ModelNode()])
    spec = AgentLoopSpec(
        agent_role="REVIEWER", model="test-model", turn_timeout=60.0,
        phase_map={}, phase_message=lambda r, p: "",
        max_iters=10_000, wallclock_timeout=0.3,
        stream_model_nodes=False,
    )

    async def emit(_t, _d):
        pass

    with pytest.raises(WallclockExceeded):
        await run_agent_loop(run, MagicMock(), spec, emit, [])
    assert tools_node.executed, (
        "the already-issued tool node must execute past the cap — "
        "discarding it is the run-83 failure mode"
    )


class _StallingToolsNode:
    """A call-tools node whose event stream hangs forever (a stalled tool)."""

    def stream(self, _ctx):
        class _CM:
            async def __aenter__(self):
                class _Stall:
                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        await asyncio.Event().wait()  # never set — stalls

                return _Stall()

            async def __aexit__(self, *args):
                return False

        return _CM()


@pytest.mark.asyncio
async def test_graced_tool_node_is_bounded_with_unbound_inner_streams(monkeypatch):
    """The LIVE reviewer and notes reviewer run bound_inner_streams=False
    (server.py loop specs) — their inner tool streams are deliberately
    untimed. Past the wall-clock deadline there is no next node boundary
    left to catch a stall, so the grace rule must FORCE the per-step
    timeout onto the graced node's stream, or a stalled tool hangs the
    pass forever."""
    monkeypatch.setattr(agent_runner, "Agent", _AgentShim)
    stall_node = _StallingToolsNode()

    class _StallRun(_ScriptedRun):
        async def __anext__(self):
            if not self._nodes:
                raise StopAsyncIteration
            item = self._nodes.pop(0)
            if item is stall_node:
                await asyncio.sleep(0.15)  # cross the 0.1s cap first
            else:
                await asyncio.sleep(0.01)
            return item

    run = _StallRun([_ModelNode(), stall_node])
    spec = AgentLoopSpec(
        agent_role="REVIEWER", model="test-model", turn_timeout=0.2,
        phase_map={}, phase_message=lambda r, p: "",
        max_iters=10_000, wallclock_timeout=0.1,
        stream_model_nodes=False,
        bound_inner_streams=False,  # the live reviewer configuration
    )

    async def emit(_t, _d):
        pass

    start = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await run_agent_loop(run, MagicMock(), spec, emit, [])
    assert time.monotonic() - start < 5.0, (
        "a stalled graced tool must hit the forced per-step timeout, "
        "never hang the pass"
    )


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
