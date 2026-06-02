"""Shared agent-iteration loop for the extraction coordinators.

Both the face coordinator (`coordinator.py`) and the notes coordinator
(`notes/coordinator.py`) drive a pydantic-ai agent via ``agent.iter()`` and
stream the same family of SSE events (tool calls/results, thinking/text
deltas, token usage) while enforcing the same guards (a per-turn timeout and
an iteration cap below pydantic-ai's silent 50, gotcha #18) and capturing the
same v8 per-turn telemetry. That loop used to be copy-pasted into each
coordinator; this module is the single implementation (rewrite Phase 2).

Per-domain differences (phase-label map + message wording, the turn timeout,
whether ``deps.turn_counter`` is published for the save-gate) are carried on
``AgentLoopSpec`` so each caller keeps its exact behaviour. The caller still
owns everything *around* the loop — agent construction, the prompt, the
verify/save gate (face) or no-write/retry semantics (notes), trace saving, and
the outcome object — so this stays a focused loop, not a god-function.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Mapping

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
    ThinkingPartDelta,
)

from agent_tracing import MAX_AGENT_ITERATIONS
from pricing import estimate_cost

logger = logging.getLogger(__name__)


class IterationLimitReached(RuntimeError):
    """Agent exceeded its iteration cap (``AgentLoopSpec.max_iters``).

    Subclasses ``RuntimeError`` so callers that already caught ``RuntimeError``
    on the legacy bare-raise (the notes coordinator) keep working unchanged,
    while the face coordinator can catch it precisely. We cap *below*
    pydantic-ai's silent ``request_limit`` of 50 (gotcha #18) so the structured
    "Hit iteration limit" path fires instead of ``UsageLimitExceeded``.
    """


def _in_tokens(u) -> int:
    """Prompt/input token count from a pydantic-ai Usage, version-tolerant.

    pydantic-ai renamed ``request_tokens`` → ``input_tokens`` (old names emit
    DeprecationWarnings). Read the new name first, fall back to the old one.
    """
    val = getattr(u, "input_tokens", None)
    if val is None:
        val = getattr(u, "request_tokens", 0)
    return int(val or 0)


def _out_tokens(u) -> int:
    """Completion/output token count from a pydantic-ai Usage (see _in_tokens)."""
    val = getattr(u, "output_tokens", None)
    if val is None:
        val = getattr(u, "response_tokens", 0)
    return int(val or 0)


def _cache_read_tokens(u) -> int:
    """Prompt-cache *read* tokens (a cache HIT) from a pydantic-ai Usage.

    pydantic-ai 1.77 normalises every provider's cached-prompt field to
    ``cache_read_tokens``. A non-zero value is the only proof that prompt
    caching is actually working — this is the §6 "measure first" signal.
    getattr-with-default keeps it safe on older interpreters that lack the
    field (returns 0 rather than raising)."""
    return int(getattr(u, "cache_read_tokens", 0) or 0)


def _cache_write_tokens(u) -> int:
    """Prompt-cache *write* tokens from a pydantic-ai Usage (see _cache_read_tokens).

    Captured separately because Anthropic bills cache writes at a premium —
    counting only reads would report phantom savings on a write-heavy run."""
    return int(getattr(u, "cache_write_tokens", 0) or 0)


async def iter_with_turn_timeout(async_iterable, timeout: float):
    """Yield items from ``async_iterable`` with a per-step timeout.

    Each ``__anext__`` is wrapped in ``asyncio.wait_for`` — if a single step
    takes longer than ``timeout`` seconds, ``asyncio.TimeoutError`` propagates
    and ``wait_for`` cancels the pending coroutine (so no background task
    leaks). Used both for the node iteration and the inner tool/model streams
    so a provider that stalls mid-stream is caught, not just one that stalls
    between nodes.
    """
    iterator = async_iterable.__aiter__()
    while True:
        try:
            node = await asyncio.wait_for(iterator.__anext__(), timeout=timeout)
        except StopAsyncIteration:
            return
        yield node


@dataclass
class AgentLoopSpec:
    """Per-caller parameters for :func:`run_agent_loop`."""

    # Display label for status messages (e.g. a StatementType / NotesTemplate
    # value). Passed to ``phase_message``.
    agent_role: str
    # Resolved model object — only used for the per-turn cost estimate.
    model: Any
    # Per-turn wall-clock budget (seconds) for one ``agent.iter()`` step and
    # for each inner tool/model stream step.
    turn_timeout: float
    # tool_name → pipeline phase label. A tool not in the map emits no phase.
    phase_map: Mapping[str, str]
    # (agent_role, phase) → the human-readable status message to emit.
    phase_message: Callable[[str, str], str]
    # Iteration cap; must stay below pydantic-ai's silent 50 (gotcha #18).
    max_iters: int = MAX_AGENT_ITERATIONS
    # When True, publish the running iteration count onto ``deps.turn_counter``
    # so the save-gate in extraction/agent.py can see the real iteration budget
    # (face only; the notes deps has no such field).
    set_turn_counter: bool = False
    # When True, the inner tool/model event streams are ALSO wrapped in the
    # per-step timeout (a provider that stalls mid-stream is caught, not just
    # one that stalls between nodes). The face loop has always done this; the
    # notes loop historically used a BARE ``async for`` over the inner stream
    # and only timed out the outer node iteration. We default True (face's
    # behaviour) but let the notes coordinator opt OUT (False) so a legitimate
    # long-running ``write_notes`` tool call is not cancelled at the per-turn
    # timeout — preserving notes' exact prior behaviour (peer-review MEDIUM,
    # rewrite Phase 2).
    bound_inner_streams: bool = True


async def run_agent_loop(
    agent_run,
    deps,
    spec: AgentLoopSpec,
    emit: Callable[[str, dict], Any],
    turn_records: List[dict],
) -> int:
    """Drive an already-open ``agent.iter()`` run, streaming events via ``emit``.

    ``agent_run`` is the value of ``async with agent.iter(...) as agent_run``;
    the caller opens it (so it can use ``agent_run.result`` / ``.usage()``
    afterwards) and hands it here. This function:

      * iterates nodes with a per-turn timeout (``spec.turn_timeout``),
      * enforces the iteration cap (raising :class:`IterationLimitReached`),
      * streams ``tool_call`` / ``tool_result`` events for call-tools nodes and
        ``text_delta`` / ``thinking_delta`` / ``thinking_end`` for model nodes,
      * emits a ``token_update`` after each node, and
      * appends one v8 per-turn metrics dict per node to ``turn_records``.

    ``turn_records`` is mutated in place so the caller's salvage paths (timeout
    / iteration-cap) still see whatever turns ran before the failure. Returns
    the final iteration count. ``asyncio.TimeoutError`` from a stalled node
    propagates to the caller, which decides salvage-vs-fail.
    """
    tool_start_times: dict[str, float] = {}
    thinking_counter = 0
    iteration = 0
    # Running cumulative usage so each node's per-turn figure is a delta
    # (pydantic-ai's usage() is cumulative). Cache read/write track the same
    # way so the per-turn rows show when a turn hit (or wrote) the cache.
    prev_prompt = prev_completion = prev_total = 0
    prev_cache_read = prev_cache_write = 0

    def _inner(stream):
        # Wrap the inner tool/model event stream in the per-step timeout only
        # when the caller opted in (face). Notes opts out so a legitimate long
        # tool call isn't cancelled mid-execution (see AgentLoopSpec).
        if spec.bound_inner_streams:
            return iter_with_turn_timeout(stream, spec.turn_timeout)
        return stream

    async for node in iter_with_turn_timeout(agent_run, spec.turn_timeout):
        iteration += 1
        if spec.set_turn_counter:
            # Let the save-gate (extraction/agent.py) see the real budget.
            deps.turn_counter = iteration
        if iteration > spec.max_iters:
            raise IterationLimitReached(
                f"Hit iteration limit ({spec.max_iters}). "
                f"Agent appears stuck in a loop."
            )

        node_start = time.monotonic()
        node_tool_names: list[str] = []
        node_kind = (
            "call_tools" if Agent.is_call_tools_node(node)
            else "model_request" if Agent.is_model_request_node(node)
            else None
        )

        if Agent.is_call_tools_node(node):
            async with node.stream(agent_run.ctx) as tool_stream:
                async for event in _inner(tool_stream):
                    if isinstance(event, FunctionToolCallEvent):
                        tool_name = event.part.tool_name
                        node_tool_names.append(tool_name)
                        phase = spec.phase_map.get(tool_name)
                        if phase:
                            await emit("status", {
                                "phase": phase,
                                "message": spec.phase_message(spec.agent_role, phase),
                            })
                        raw_args = event.part.args
                        if isinstance(raw_args, str):
                            try:
                                parsed_args = json.loads(raw_args)
                            except (json.JSONDecodeError, TypeError):
                                parsed_args = {}
                        elif isinstance(raw_args, dict):
                            parsed_args = raw_args
                        else:
                            parsed_args = {}
                        await emit("tool_call", {
                            "tool_name": tool_name,
                            "tool_call_id": event.part.tool_call_id,
                            "args": parsed_args,
                        })
                        tool_start_times[event.part.tool_call_id] = time.monotonic()
                    elif isinstance(event, FunctionToolResultEvent):
                        content = event.result.content
                        summary = str(content)[:800] if content else ""
                        call_id = event.result.tool_call_id
                        start_t = tool_start_times.pop(call_id, None)
                        duration_ms = (
                            int((time.monotonic() - start_t) * 1000) if start_t else 0
                        )
                        await emit("tool_result", {
                            "tool_name": event.result.tool_name,
                            "tool_call_id": call_id,
                            "result_summary": summary,
                            "duration_ms": duration_ms,
                        })

        elif Agent.is_model_request_node(node):
            thinking_id = f"{spec.agent_role}_think_{thinking_counter}"
            thinking_active = False
            async with node.stream(agent_run.ctx) as model_stream:
                async for event in _inner(model_stream):
                    if isinstance(event, PartDeltaEvent):
                        delta = event.delta
                        if isinstance(delta, TextPartDelta):
                            if thinking_active:
                                await emit("thinking_end", {
                                    "thinking_id": thinking_id,
                                    "summary": "",
                                    "full_length": 0,
                                })
                                thinking_active = False
                                thinking_counter += 1
                                thinking_id = f"{spec.agent_role}_think_{thinking_counter}"
                            await emit("text_delta", {"content": delta.content_delta})
                        elif isinstance(delta, ThinkingPartDelta):
                            thinking_active = True
                            await emit("thinking_delta", {
                                "content": delta.content_delta or "",
                                "thinking_id": thinking_id,
                            })
            if thinking_active:
                await emit("thinking_end", {
                    "thinking_id": thinking_id,
                    "summary": "",
                    "full_length": 0,
                })
                thinking_counter += 1

        # Emit token usage after each node completes.
        usage = agent_run.usage()
        total = int(usage.total_tokens or 0)
        prompt_t = _in_tokens(usage)
        completion_t = _out_tokens(usage)
        cache_read_t = _cache_read_tokens(usage)
        cache_write_t = _cache_write_tokens(usage)
        await emit("token_update", {
            "prompt_tokens": prompt_t,
            "completion_tokens": completion_t,
            "thinking_tokens": 0,  # pydantic-ai doesn't separate thinking tokens
            "cumulative": total,
            "cost_estimate": estimate_cost(prompt_t, completion_t, 0, spec.model),
        })

        # v8 telemetry: per-turn metrics row (deltas vs the previous node's
        # cumulative usage). Guarded — a telemetry hiccup must never break
        # streaming or change the run outcome.
        try:
            d_prompt = max(prompt_t - prev_prompt, 0)
            d_completion = max(completion_t - prev_completion, 0)
            d_total = max(total - prev_total, 0)
            turn_records.append({
                "turn_index": iteration,
                "node_kind": node_kind,
                "tool_names": ",".join(node_tool_names) or None,
                "_n_tool_calls": len(node_tool_names),
                "prompt_tokens": d_prompt,
                "completion_tokens": d_completion,
                "total_tokens": d_total,
                "cumulative_tokens": total,
                "cost_estimate": estimate_cost(d_prompt, d_completion, 0, spec.model),
                "duration_ms": int((time.monotonic() - node_start) * 1000),
                # v15 cache telemetry: this turn's contribution to cache
                # read/write (delta of the cumulative usage).
                "cache_read_tokens": max(cache_read_t - prev_cache_read, 0),
                "cache_write_tokens": max(cache_write_t - prev_cache_write, 0),
            })
            prev_prompt, prev_completion, prev_total = prompt_t, completion_t, total
            prev_cache_read, prev_cache_write = cache_read_t, cache_write_t
        except Exception:  # noqa: BLE001 — telemetry is advisory
            logger.debug("per-turn telemetry capture skipped for %s", spec.agent_role)

    return iteration
