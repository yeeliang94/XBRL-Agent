"""Contract test: coordinator event payloads must match frontend types.ts.

Verifies that the field names in events emitted by the coordinator's
_build_event + streaming loop match what the frontend reducer expects.
This catches payload drift between backend and frontend.
"""

import asyncio
import pytest
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import patch, MagicMock

from statement_types import StatementType
from coordinator import run_extraction, RunConfig, _build_event


# --- Expected field contracts (from web/src/lib/types.ts) ---

TOOL_CALL_REQUIRED = {"tool_name", "tool_call_id", "args"}
TOOL_RESULT_REQUIRED = {"tool_name", "tool_call_id", "result_summary", "duration_ms"}
TOKEN_UPDATE_REQUIRED = {"prompt_tokens", "completion_tokens", "thinking_tokens", "cumulative", "cost_estimate"}
STATUS_REQUIRED = {"phase", "message"}
THINKING_DELTA_REQUIRED = {"content", "thinking_id"}
TEXT_DELTA_REQUIRED = {"content"}
# All agent-scoped events must include agent routing fields
AGENT_FIELDS = {"agent_id", "agent_role"}


class TestEventContract:
    """Verify coordinator events conform to the frontend contract."""

    def test_build_event_always_includes_agent_fields(self):
        """_build_event must always inject agent_id and agent_role."""
        evt = _build_event("tool_call", "sofp_0", "SOFP", {"tool_name": "read_template"})
        assert AGENT_FIELDS.issubset(evt["data"].keys())

    @pytest.mark.asyncio
    async def test_streaming_events_match_frontend_types(self):
        """Run coordinator with a mock agent that exercises tool + model nodes,
        and verify every queued event has the correct field names."""
        from pydantic_ai.messages import (
            FunctionToolCallEvent, FunctionToolResultEvent,
            PartDeltaEvent, TextPartDelta, ThinkingPartDelta,
        )

        # Build mock events that simulate real PydanticAI streaming
        mock_tool_call_part = MagicMock()
        mock_tool_call_part.tool_name = "read_template"
        mock_tool_call_part.tool_call_id = "tc_001"
        mock_tool_call_part.args = '{"key": "value"}'

        mock_tool_result_part = MagicMock()
        mock_tool_result_part.tool_name = "read_template"
        mock_tool_result_part.tool_call_id = "tc_001"
        mock_tool_result_part.content = "Template has 30 fields..."

        tool_call_event = FunctionToolCallEvent(part=mock_tool_call_part)
        tool_result_event = FunctionToolResultEvent(result=mock_tool_result_part)

        text_delta = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hello"))
        thinking_delta = PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="thinking..."))

        # Collect events from the queue
        collected_events: list[dict] = []
        event_queue: asyncio.Queue = asyncio.Queue()

        # Mock agent that emits tool + model streaming events
        from pydantic_ai import Agent

        mock_agent = MagicMock()
        mock_deps = MagicMock()
        mock_deps.filled_path = "/tmp/test_filled.xlsx"

        # Build mock nodes
        mock_call_tools_node = MagicMock()
        mock_model_request_node = MagicMock()

        # CallToolsNode.stream() yields tool events
        @asynccontextmanager
        async def mock_tool_stream(ctx):
            async def gen():
                yield tool_call_event
                yield tool_result_event
            yield gen()

        mock_call_tools_node.stream = mock_tool_stream

        # ModelRequestNode.stream() yields text/thinking deltas
        @asynccontextmanager
        async def mock_model_stream(ctx):
            async def gen():
                yield thinking_delta
                yield text_delta
            yield gen()

        mock_model_request_node.stream = mock_model_stream

        # Mock agent.iter() context manager
        mock_run = MagicMock()
        mock_result = MagicMock()
        mock_result.all_messages = MagicMock(return_value=[])
        mock_run.result = mock_result
        mock_run.usage = MagicMock(return_value=MagicMock(
            request_tokens=100, response_tokens=50, total_tokens=150,
        ))

        # __aiter__ yields our mock nodes
        async def node_iter(self_ignored=None):
            yield mock_call_tools_node
            yield mock_model_request_node
        mock_run.__aiter__ = node_iter

        @asynccontextmanager
        async def mock_iter(*args, **kwargs):
            yield mock_run
        mock_agent.iter = mock_iter

        # Patch is_call_tools_node / is_model_request_node to identify our mocks
        original_is_call = Agent.is_call_tools_node
        original_is_model = Agent.is_model_request_node

        def patched_is_call(node):
            return node is mock_call_tools_node

        def patched_is_model(node):
            return node is mock_model_request_node

        config = RunConfig(
            pdf_path="/tmp/test.pdf",
            output_dir="/tmp/test_output",
            model="test-model",
            statements_to_run={StatementType.SOFP},
            variants={StatementType.SOFP: "CuNonCu"},
        )

        with patch("coordinator.create_extraction_agent", return_value=(mock_agent, mock_deps)), \
             patch.object(Agent, "is_call_tools_node", side_effect=patched_is_call), \
             patch.object(Agent, "is_model_request_node", side_effect=patched_is_model):
            await run_extraction(config, event_queue=event_queue)

        # Drain collected events
        while not event_queue.empty():
            evt = event_queue.get_nowait()
            if evt is not None:
                collected_events.append(evt)

        # Group by event type
        by_type: dict[str, list[dict]] = {}
        for evt in collected_events:
            by_type.setdefault(evt["event"], []).append(evt["data"])

        # --- Assert field contracts ---
        assert "tool_call" in by_type, f"Expected tool_call events, got: {list(by_type.keys())}"
        for data in by_type["tool_call"]:
            assert TOOL_CALL_REQUIRED.issubset(data.keys()), \
                f"tool_call missing fields: {TOOL_CALL_REQUIRED - data.keys()}"
            assert AGENT_FIELDS.issubset(data.keys()), \
                f"tool_call missing agent fields: {AGENT_FIELDS - data.keys()}"

        assert "tool_result" in by_type
        for data in by_type["tool_result"]:
            assert TOOL_RESULT_REQUIRED.issubset(data.keys()), \
                f"tool_result missing fields: {TOOL_RESULT_REQUIRED - data.keys()}"

        assert "token_update" in by_type
        for data in by_type["token_update"]:
            assert TOKEN_UPDATE_REQUIRED.issubset(data.keys()), \
                f"token_update missing fields: {TOKEN_UPDATE_REQUIRED - data.keys()}"

        assert "status" in by_type
        for data in by_type["status"]:
            assert STATUS_REQUIRED.issubset(data.keys()), \
                f"status missing fields: {STATUS_REQUIRED - data.keys()}"

        assert "thinking_delta" in by_type
        for data in by_type["thinking_delta"]:
            assert THINKING_DELTA_REQUIRED.issubset(data.keys()), \
                f"thinking_delta missing fields: {THINKING_DELTA_REQUIRED - data.keys()}"

        assert "text_delta" in by_type
        for data in by_type["text_delta"]:
            assert TEXT_DELTA_REQUIRED.issubset(data.keys()), \
                f"text_delta missing fields: {TEXT_DELTA_REQUIRED - data.keys()}"

        # thinking_end should be emitted when thinking transitions to text
        assert "thinking_end" in by_type, "Expected thinking_end event when thinking→text transition"

        assert "complete" in by_type
