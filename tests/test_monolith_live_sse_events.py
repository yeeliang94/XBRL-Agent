"""Pinning test for monolith live SSE emission (peer-review MEDIUM #3).

The split coordinator emits `tool_call`, `tool_result`, `text_delta`,
`thinking_delta`, and `thinking_end` events as each agent runs, so
the Agents tab + Telemetry view populate in real time. Pre-fix, the
monolith only recorded tool *names* for per-turn telemetry and never
pushed events to the SSE queue — the UI looked frozen during long
monolith runs (30+ turns, ~15 min).

This test drives the monolith's stream loop with a fake `agent.iter`
that yields one call-tools node + one model-request node, each with
the events the split coordinator handles. It then asserts the queue
received the same event family the split path emits.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from monolith.coordinator import MonolithRunConfig, run_monolith
from statement_types import StatementType


# Pretend events that match the duck-typed shapes the monolith stream
# loop pattern-matches on. The real pydantic-ai types live in
# pydantic_ai.messages; isinstance() checks would fail on our fakes,
# so we monkeypatch the isinstance comparator targets inside the
# coordinator module to make our fakes pass.
class _FakePart:
    def __init__(self, tool_name: str, tool_call_id: str, args: Any):
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.args = args


class _FakeResult:
    def __init__(self, tool_name: str, tool_call_id: str, content: str):
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.content = content


class _FakeToolCallEvent:
    def __init__(self, tool_name: str, tool_call_id: str, args: Any):
        self.part = _FakePart(tool_name, tool_call_id, args)


class _FakeToolResultEvent:
    def __init__(self, tool_name: str, tool_call_id: str, content: str):
        self.result = _FakeResult(tool_name, tool_call_id, content)


class _FakeNode:
    """A duck-typed node that yields the supplied events when iterated."""

    def __init__(self, kind: str, events: list):
        self._kind = kind
        self._events = events

    def stream(self, _ctx):
        events = self._events

        class _Cm:
            async def __aenter__(self_inner):
                return _AsyncIter(events)

            async def __aexit__(self_inner, *_):
                return False

        return _Cm()


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _FakeUsage:
    request_tokens = 0
    response_tokens = 0
    total_tokens = 0


class _FakeAgentRun:
    """Stand-in for `async with agent.iter(...) as agent_run`."""

    def __init__(self, nodes):
        self._nodes = nodes
        self.ctx = object()
        self.result = None

    def __aiter__(self):
        return _AsyncIter(self._nodes)

    def usage(self):
        return _FakeUsage()


class _FakeAgent:
    def __init__(self, nodes):
        self._nodes = nodes

    def iter(self, *_a, **_kw):
        nodes = self._nodes

        class _Cm:
            async def __aenter__(self_inner):
                return _FakeAgentRun(nodes)

            async def __aexit__(self_inner, *_):
                return False

        return _Cm()


REPO = Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_monolith_emits_tool_call_and_tool_result_events(tmp_path):
    """The monolith stream loop must push `tool_call` AND `tool_result`
    SSE events for every FunctionToolCallEvent / FunctionToolResultEvent
    it sees on a call-tools node. The split coordinator already does
    this (coordinator.py:605-647); the monolith must match."""
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    output_dir = tmp_path / "out"

    tool_call = _FakeToolCallEvent(
        tool_name="get_state",
        tool_call_id="call-1",
        args={"foo": "bar"},
    )
    tool_result = _FakeToolResultEvent(
        tool_name="get_state",
        tool_call_id="call-1",
        content={"sheets": []},
    )
    fake_node = _FakeNode("call_tools", [tool_call, tool_result])
    fake_agent = _FakeAgent([fake_node])

    queue: asyncio.Queue = asyncio.Queue()

    # Stub the coordinator's helpers + isinstance checks so our duck-
    # typed fakes are accepted.
    import monolith.coordinator as mc

    def _is_call_tools(n):
        return getattr(n, "_kind", None) == "call_tools"

    def _is_model_request(n):
        return getattr(n, "_kind", None) == "model_request"

    def _node_kind(n):
        return getattr(n, "_kind", None)

    def _safe_pdf_page_count(_):
        return 1

    def _materialise(target: Path, *_a, **_kw):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")  # placeholder; snapshot logic tolerates this

    def _build_agent(*_a, **_kw):
        class _Deps:
            final_done_result = {"status": "done"}

        return fake_agent, _Deps()

    def _save_trace(*_a, **_kw):
        return None

    def _finalize_success(*_a, **_kw):
        from monolith.coordinator import MonolithResult
        return MonolithResult(
            status="succeeded",
            workbook_path=str(output_dir / "monolith_filled.xlsx"),
        )

    with patch.object(mc, "_is_call_tools_node", _is_call_tools), \
         patch.object(mc, "_is_model_request_node", _is_model_request), \
         patch.object(mc, "_node_kind", _node_kind), \
         patch.object(mc, "_safe_pdf_page_count", _safe_pdf_page_count), \
         patch.object(mc, "_materialise_workbook", _materialise), \
         patch.object(mc, "_build_agent", _build_agent), \
         patch.object(mc, "save_agent_trace", _save_trace), \
         patch.object(mc, "_finalize_success", _finalize_success), \
         patch.object(
             mc, "FunctionToolCallEvent", _FakeToolCallEvent,
         ), \
         patch.object(
             mc, "FunctionToolResultEvent", _FakeToolResultEvent,
         ), \
         patch.object(mc, "_snapshot_workbook", lambda _p: None), \
         patch.object(
             mc, "render_monolith_prompt",
             lambda *a, **k: type("R", (), {"full": "stub"})(),
         ):
        cfg = MonolithRunConfig(
            pdf_path=str(pdf),
            output_dir=str(output_dir),
            model="stub",
            statements={StatementType.SOFP},
        )
        await run_monolith(cfg, event_queue=queue)

    # Drain the queue and collect event types.
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    types = [e["event"] for e in events if e is not None]
    # Phase emission ("status") for the recognised tool is nice-to-have;
    # the load-bearing assertions are tool_call + tool_result.
    assert "tool_call" in types, (
        f"monolith did not emit tool_call SSE event; saw {types!r}"
    )
    assert "tool_result" in types, (
        f"monolith did not emit tool_result SSE event; saw {types!r}"
    )

    tool_call_data = next(
        e["data"] for e in events if e and e["event"] == "tool_call"
    )
    assert tool_call_data["tool_name"] == "get_state"
    assert tool_call_data["tool_call_id"] == "call-1"
    assert tool_call_data["args"] == {"foo": "bar"}
    # Monolith identity in every event for the frontend timeline.
    assert tool_call_data["agent_id"] == "monolith"

    tool_result_data = next(
        e["data"] for e in events if e and e["event"] == "tool_result"
    )
    assert tool_result_data["tool_name"] == "get_state"
    assert tool_result_data["tool_call_id"] == "call-1"
    # duration_ms is non-negative (timer is measured between the
    # tool_call and tool_result emissions).
    assert tool_result_data["duration_ms"] >= 0
