# Implementation Plan: Real-Time SSE Streaming for Multi-Agent Extraction

**Overall Progress:** `100%`
**Last Updated:** 2026-04-07

## Summary

The multi-agent extraction pipeline blocks the SSE generator for the entire duration of all agent runs. After emitting "Starting extraction for 2 statements...", no events reach the frontend until every agent finishes (often 2+ minutes later). The fix restores the same `agent.iter()` streaming pattern that the original single-agent SOFP flow used (`iter_agent_events`, removed in Phase 11.3), but adapted for concurrent multi-agent execution via an `asyncio.Queue` event bridge.

The frontend already has all the infrastructure: per-agent state routing (`agentReducer`), tab UI (`AgentTabs`), pipeline stepper (`PipelineStages`), tool timeline, thinking blocks, and token dashboard — all waiting for events that never arrive.

## Key Decisions

- **asyncio.Queue event bridge**: Each concurrent agent pushes events into a shared queue. The SSE generator drains it. This decouples agent execution from SSE serialization without modifying PydanticAI internals.
- **Reuse existing PHASE_MAP**: The `PHASE_MAP` in `server.py:54` already maps tool names → phases. Move it to coordinator so phase events are emitted at the source.
- **agent.iter() replaces agent.run()**: Same PydanticAI streaming API the old single-agent path used. `agent.iter()` → iterate nodes → `node.stream()` for granular events.
- **Backward-compatible coordinator API**: `run_extraction()` still returns `CoordinatorResult`. The queue is an optional additional output channel — `None` = silent (CLI, tests).
- **No frontend code changes needed** (except one optional line for per-agent phase display).

## Pre-Implementation Checklist

- [x] 🟩 Root cause diagnosed (server.py:534 blocks, coordinator.py:122 gathers, agent.py:189 non-streaming)
- [x] 🟩 Frontend event infrastructure verified (types.ts, App.tsx reducer, AgentTabs, PipelineStages all ready)
- [x] 🟩 Verify pydantic-ai >= 1.77.0 installed (upgraded to 1.77.0 via Python 3.12 venv)
- [x] 🟩 Confirm `agent.iter()` API available: `Agent.is_model_request_node()`, `Agent.is_call_tools_node()`
- [x] 🟩 Existing tests pass: `python -m pytest tests/ -v`

## Tasks

### Phase 1: Event Bridge in coordinator.py

- [x] 🟩 **Step 1: Add event queue plumbing** — Thread an `asyncio.Queue` through the coordinator
  - [x] 🟩 Add `event_queue: Optional[asyncio.Queue] = None` param to `run_extraction()` (line 68)
  - [x] 🟩 Add `event_queue` and `agent_id: str` params to `_run_single_agent()` (line 163)
  - [x] 🟩 Generate `agent_id` in the coordinator loop: `f"{stmt_type.value.lower()}_{idx}"` (matches server.py:542 pattern)
  - [x] 🟩 Push `None` sentinel after `asyncio.gather()` completes (after line 122) when queue provided
  - **Verified:** All e2e tests pass with event_queue=None (backward compatible)

- [x] 🟩 **Step 2: Add PHASE_MAP and event builder** — Helper to construct SSE-shaped events
  - [x] 🟩 Move/duplicate `PHASE_MAP` from server.py:54 into coordinator.py
  - [x] 🟩 Add `_build_event(event_type, agent_id, agent_role, data) -> dict` helper
  - [x] 🟩 Returns `{"event": event_type, "data": {**data, "agent_id": agent_id, "agent_role": agent_role}}`
  - **Verified:** Inline test confirms correct dict shape

- [x] 🟩 **Step 3: Replace agent.run() with agent.iter() streaming loop** — The core change
  - [x] 🟩 Import PydanticAI streaming types at top of coordinator.py
  - [x] 🟩 Replace `result = await agent.run(prompt, deps=deps)` (line 189) with `agent.iter()` loop:
    ```python
    async with agent.iter(prompt, deps=deps) as agent_run:
        async for node in agent_run:
            if Agent.is_call_tools_node(node):
                # Stream tool events
                async with node.stream(agent_run.ctx) as stream:
                    async for event in stream:
                        if event_queue:
                            await event_queue.put(_build_tool_event(event, agent_id, agent_role))
            elif Agent.is_model_request_node(node):
                # Stream thinking/text deltas
                async with node.stream(agent_run.ctx) as stream:
                    async for event in stream:
                        if event_queue:
                            await event_queue.put(_build_model_event(event, agent_id, agent_role))
    result = agent_run.result  # Same RunResult as agent.run() returned
    ```
  - [x] 🟩 Map `FunctionToolCallEvent` → emit `status` (phase from PHASE_MAP) + `tool_call` event
  - [x] 🟩 Map `FunctionToolResultEvent` → emit `tool_result` event with summary + duration
  - [x] 🟩 Map `TextPartDelta` → emit `text_delta` event
  - [x] 🟩 Map `ThinkingPartDelta` → emit `thinking_delta` event (if model supports it)
  - [x] 🟩 Push `complete` event when agent finishes (success or failure)
  - [x] 🟩 Error handling emits error + complete events (replaces __agent_done__ sentinel)
  - [x] 🟩 Keep existing trace saving (`_save_agent_trace`) and `AgentResult` construction unchanged
  - **Verified:** All coordinator, e2e, and multi-agent tests pass. Updated test mocks to use agent.iter() pattern.
  - **Note:** Token updates (Step 5) are emitted inline after each node — merged into this step.

### Phase 2: SSE Generator Refactoring in server.py

- [x] 🟩 **Step 4: Replace blocking coordinator call with queue drain** — Wire the event bridge into the SSE generator
  - [x] 🟩 Create `event_queue = asyncio.Queue(maxsize=1000)` before coordinator call
  - [x] 🟩 Launch coordinator as background task: `asyncio.create_task(coordinator_run(config, infopack=infopack, event_queue=event_queue))`
  - [x] 🟩 Add queue drain loop that yields events in real time:
    ```python
    while True:
        event = await event_queue.get()
        if event is None:
            break
        if event["event"] == "__agent_done__":
            continue
        yield event
    ```
  - [x] 🟩 `await coordinator_task` after queue drain to get `CoordinatorResult` for post-processing
  - [x] 🟩 Per-agent completion events now come from the queue (coordinator emits them)
  - [x] 🟩 Keep all post-processing unchanged: merged result.json, workbook merge, cross-checks, audit DB, `run_complete`
  - [x] 🟩 Updated test mocks in test_e2e.py, test_multi_agent_integration.py, test_sse_api.py to push None sentinel into event_queue
  - **Verified:** All 13 tests pass (coordinator, e2e, multi-agent, SSE API, SSE multiplex)

### Phase 3: Token Updates

- [x] 🟩 **Step 5: Emit token_update events during execution** — Merged into Step 3
  - [x] 🟩 After each node completes in the `agent.iter()` loop, read `agent_run.usage()` and emit `token_update`
  - [x] 🟩 Includes `prompt_tokens`, `completion_tokens`, `total_tokens`
  - **Verified:** Implemented in coordinator.py _run_single_agent() — emits after each node iteration

### Phase 4: Error Handling

- [x] 🟩 **Step 6: Handle agent failures gracefully** — Merged into Step 3
  - [x] 🟩 agent.iter() wrapped in try/except; on failure emits `error` + `complete` events
  - [x] 🟩 Existing `AgentResult(status="failed")` return is preserved
  - [x] 🟩 Coordinator-level exceptions handled by server.py's coordinator_task await + error yield
  - **Verified:** test_coordinator_handles_agent_failure passes — one agent fails, other succeeds

### Phase 5: Frontend Polish (Optional)

- [x] 🟩 **Step 7: Per-agent phase display** — Show active tab's pipeline phase instead of global
  - [x] 🟩 In `App.tsx`, changed `PipelineStages` prop to use active tab's phase with null-safe ternary
  - **Verified:** TypeScript compiles cleanly, all 131 frontend tests pass

## Rollback Plan

Changes are contained to two backend files (`coordinator.py`, `server.py`) and one optional frontend line (`App.tsx`).

- **Immediate rollback:** Revert `_run_single_agent()` back to `agent.run()` and `run_multi_agent_stream()` back to `await coordinator_run()`. The frontend gracefully degrades to showing "Starting..." then jumping to completion.
- **Partial rollback:** Keep the queue but use `agent.run()` inside `_run_single_agent()`, emitting only coarse start/complete events per agent. Gives per-agent progress without `agent.iter()` complexity.
- **Data safety:** No database schema changes. No file format changes. Output files are identical.
