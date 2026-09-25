"""Gotcha #18 — the structured iteration cap must fire before PydanticAI's
request limit.

The 2026-04-26 incident (terminal traceback
``pydantic_ai.exceptions.UsageLimitExceeded: request_limit of 50``) came from
our cap racing PydanticAI's silent default request limit and losing: the user
got an unactionable traceback instead of the structured "Hit iteration limit"
error. Face and notes runs now pass ``agent_usage_limits(cap)`` to
``agent.iter``. The cap counts graph nodes and every model request is one
node, so our cap is always reached first, even above 50 requests.
"""
from __future__ import annotations

import importlib

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent_runner import AgentLoopSpec, IterationLimitReached, run_agent_loop
from agent_tracing import MAX_AGENT_ITERATIONS, agent_usage_limits


def test_default_cap_leaves_room_for_long_documents():
    """The 2026-09-25 trace audit saw agents hit the old 40-node cap mid-task."""
    assert MAX_AGENT_ITERATIONS == 60


@pytest.mark.asyncio
async def test_iteration_cap_fires_before_request_limit_beyond_fifty_requests():
    """A looping agent with a cap above 100 nodes (over 50 model requests)
    stops on the structured cap, not on PydanticAI's UsageLimitExceeded."""

    def always_call_tool(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(tool_name="noop", args={})])

    agent = Agent(FunctionModel(always_call_tool))

    @agent.tool_plain
    def noop() -> str:
        return "ok"

    cap = 110
    spec = AgentLoopSpec(
        agent_role="TEST",
        model=None,
        turn_timeout=30,
        phase_map={},
        phase_message=lambda role, phase: phase,
        max_iters=cap,
        stream_model_nodes=False,
    )

    async def emit(event_type, data):
        return None

    with pytest.raises(IterationLimitReached):
        async with agent.iter(
            "loop", usage_limits=agent_usage_limits(cap),
        ) as agent_run:
            await run_agent_loop(agent_run, object(), spec, emit, [])


@pytest.mark.parametrize("raw,expected", [
    ("30", 30),
    ("120", 120),
    ("500", 120),  # clamped to the safe ceiling
    ("abc", 60),
])
def test_env_override(monkeypatch, raw, expected):
    monkeypatch.setenv("XBRL_MAX_AGENT_ITERATIONS", raw)
    import agent_tracing
    importlib.reload(agent_tracing)
    try:
        assert agent_tracing.MAX_AGENT_ITERATIONS == expected
    finally:
        monkeypatch.delenv("XBRL_MAX_AGENT_ITERATIONS", raising=False)
        importlib.reload(agent_tracing)
