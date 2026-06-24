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
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Mapping, Optional, TypeVar

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


def build_agent_event(
    event_type: str,
    agent_id: str,
    agent_role: str,
    data: dict,
    *,
    extra: Mapping[str, Any] | None = None,
) -> dict:
    """Construct the canonical SSE-shaped event dict with agent identification.

    Single source of truth for the ``{"event": ..., "data": {**data,
    "agent_id", "agent_role"}}`` shape every coordinator emits (rewrite Phase
    2 follow-up). ``extra`` carries caller-specific payload keys that ride
    inside the same ``data`` dict — e.g. the Sheet-12 sub-coordinator passes
    the parent's ``agent_id`` here as ``agent_id`` and ``sub_agent_id`` via
    ``extra`` so parallel sub-agents aggregate into one parent tab while still
    being individually traceable. Applied *after* agent_id/agent_role so a
    caller can override them when needed; dict-order differences are
    irrelevant (consumers read by key)."""
    payload = {**data, "agent_id": agent_id, "agent_role": agent_role}
    if extra:
        payload.update(extra)
    return {"event": event_type, "data": payload}


def make_emitter(
    event_queue,
    agent_id: str,
    agent_role: str,
    *,
    extra: Mapping[str, Any] | None = None,
    safe_swallow: tuple[type[BaseException], ...] = (asyncio.CancelledError,),
):
    """Build the ``(emit, safe_emit)`` pair every coordinator uses.

    ``emit(event_type, data)`` pushes a :func:`build_agent_event` dict onto
    ``event_queue`` (a no-op when the queue is ``None`` — the CLI path).
    ``safe_emit`` is the teardown-safe variant used inside ``except``
    cancellation blocks: awaiting ``queue.put`` during an active cancellation
    can itself raise, which would trap the structured terminal return. The
    *set* of swallowed exceptions is a parameter because the callers diverge —
    the face coordinator swallows ``CancelledError`` only, while the notes
    coordinators swallow ``Exception`` — and this preserves each one's exact
    prior behaviour rather than unifying it."""

    async def emit(event_type: str, data: dict) -> None:
        if event_queue is not None:
            await event_queue.put(
                build_agent_event(event_type, agent_id, agent_role, data, extra=extra)
            )

    async def safe_emit(event_type: str, data: dict) -> None:
        try:
            await emit(event_type, data)
        except safe_swallow:  # type: ignore[misc]
            logger.debug(
                "Dropped %s event during teardown for %s",
                event_type, agent_id or agent_role,
            )

    return emit, safe_emit


class IterationLimitReached(RuntimeError):
    """Agent exceeded its iteration cap (``AgentLoopSpec.max_iters``).

    Subclasses ``RuntimeError`` so callers that already caught ``RuntimeError``
    on the legacy bare-raise (the notes coordinator) keep working unchanged,
    while the face coordinator can catch it precisely. We cap *below*
    pydantic-ai's silent ``request_limit`` of 50 (gotcha #18) so the structured
    "Hit iteration limit" path fires instead of ``UsageLimitExceeded``.
    """


class WallclockExceeded(RuntimeError):
    """Agent exceeded its whole-run wall-clock cap
    (``AgentLoopSpec.wallclock_timeout``) — items 6/17 of
    PLAN-orchestration-hardening. Checked between loop iterations, so it
    bounds the many-quick-turns scenario the per-turn timeout can't catch.
    """


class TokenBudgetExceeded(RuntimeError):
    """Agent crossed its cumulative token budget
    (``AgentLoopSpec.token_budget``) — item 7 of
    PLAN-orchestration-hardening. Checked at each turn boundary against the
    cumulative usage pydantic-ai reports, so spend is bounded approximately
    (within one turn) — exactness is not required (gotcha #6).
    """


class CallToolsCapExceeded(RuntimeError):
    """Agent exceeded its CALL-TOOLS turn cap
    (``AgentLoopSpec.call_tools_cap``) — the reviewer's dynamic 8-25 turn
    budget counts tool-calling turns, not raw node iterations (item 17
    migration). Raised before the over-cap node is processed, so the last
    recorded turn count equals the cap exactly.
    """


def resolve_token_budget() -> int:
    """XBRL_MAX_TOKENS_PER_AGENT: cumulative-token ceiling per agent run.

    Default 0 = disabled (opt-in first; flip after observing real run
    costs). Non-numeric / negative values disable rather than crash.
    """
    raw = os.environ.get("XBRL_MAX_TOKENS_PER_AGENT", "")
    if not raw:
        return 0
    try:
        v = int(raw)
    except ValueError:
        logger.warning(
            "XBRL_MAX_TOKENS_PER_AGENT=%r is not an int; budget disabled", raw,
        )
        return 0
    return v if v > 0 else 0


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
    # Items 6/17: whole-run wall-clock cap (seconds). Checked at the top of
    # each loop iteration — bounds the 40-slow-but-compliant-turns scenario
    # the per-turn timeout can't catch. None (or <= 0) disables; raises
    # WallclockExceeded on breach.
    wallclock_timeout: float | None = None
    # Item 7: cumulative token ceiling for the whole run. 0 disables.
    # Checked at each turn boundary against pydantic-ai's cumulative usage;
    # raises TokenBudgetExceeded on breach.
    token_budget: int = 0
    # Item 17 (reviewer migration): cap on CALL-TOOLS turns specifically —
    # the reviewer's dynamic budget counts tool-calling turns, not raw node
    # iterations (a node loop interleaves model-request nodes between
    # them). None disables; raises CallToolsCapExceeded on breach.
    call_tools_cap: int | None = None
    # Item 17 (reviewer/validator migration): those passes never streamed
    # model-request nodes (no text_delta/thinking events on their wire
    # contract), and their tests drive them with non-streaming
    # FunctionModels — calling node.stream() on one raises. False skips the
    # model-node streaming block; the node still executes when the loop
    # advances. Face/notes keep the default True.
    stream_model_nodes: bool = True


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
    call_tools_seen = 0
    # Items 6/17: whole-run wall-clock anchor for spec.wallclock_timeout.
    loop_start = time.monotonic()
    wallclock_cap = (
        float(spec.wallclock_timeout)
        if spec.wallclock_timeout and spec.wallclock_timeout > 0
        else None
    )
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
        # Items 6/17: in-loop wall-clock check (same placement as the
        # reviewer's hand-rolled guard in server._run_reviewer_pass).
        if wallclock_cap is not None and (
            time.monotonic() - loop_start > wallclock_cap
        ):
            raise WallclockExceeded(
                f"{spec.agent_role}: exceeded the {wallclock_cap:.0f}s "
                f"wall-clock cap after {iteration - 1} turn(s)."
            )

        node_start = time.monotonic()
        node_tool_names: list[str] = []
        node_kind = (
            "call_tools" if Agent.is_call_tools_node(node)
            else "model_request" if Agent.is_model_request_node(node)
            else None
        )

        if Agent.is_call_tools_node(node):
            call_tools_seen += 1
            if (
                spec.call_tools_cap is not None
                and call_tools_seen > spec.call_tools_cap
            ):
                # Raised BEFORE the over-cap node is processed, so
                # turn_records carries exactly `call_tools_cap` tool turns.
                raise CallToolsCapExceeded(
                    f"{spec.agent_role}: exceeded the "
                    f"{spec.call_tools_cap}-turn tool budget."
                )
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

        elif Agent.is_model_request_node(node) and spec.stream_model_nodes:
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

        # Item 7: token-budget check at the turn boundary, AFTER the turn's
        # telemetry is recorded so salvage paths still see the real spend.
        # `total` is pydantic-ai's cumulative usage — the same number the v8
        # per-turn deltas derive from, so the bound is approximate within
        # one turn (gotcha #6: that's its job; exactness not required).
        if spec.token_budget and total > spec.token_budget:
            raise TokenBudgetExceeded(
                f"{spec.agent_role}: cumulative token usage {total} crossed "
                f"the {spec.token_budget}-token budget after {iteration} "
                f"turn(s)."
            )

    return iteration


# --------------------------------------------------------------------------- #
# Retry-and-backoff scaffold (PLAN-orchestration-seams Part A / Phase A2).
#
# The per-agent retry loop used to be copy-pasted across coordinator.py,
# notes/coordinator.py and notes/listofnotes_subcoordinator.py. The mechanics
# — schedule-backoff-then-consume-inside-the-try (so a Stop-All during the
# backoff sleep lands on CancelledError), per-budget transient classification,
# failed-attempt bookkeeping, and terminal/cancelled result construction — are
# identical; only the budgets, the result type, and the cleanup/emit
# side-effects differ per caller. Those differences are data (RetryPolicy) and
# callbacks, so one implementation serves face + notes + Sheet-12 without
# leaking their specifics into the loop.
# --------------------------------------------------------------------------- #

_RetryResult = TypeVar("_RetryResult")


def _default_is_rate_limit(e: BaseException) -> bool:
    from notes._rate_limit import is_rate_limit_error
    return is_rate_limit_error(e)


def _default_compute_backoff(e: BaseException, prior_retries: int) -> float:
    from notes._rate_limit import compute_backoff_delay
    return compute_backoff_delay(e, prior_retries)


@dataclass
class RetryPolicy:
    """Per-caller retry budgets + classifiers for :func:`run_agent_with_retries`.

    Three independent lanes, each consumed separately so a flaky provider on
    one lane never burns another's budget:

    * **rate-limit** (``rate_limit_retries``): provider 429s; uses honoured
      retry-after backoff (``compute_backoff``). A rate-limit error whose
      budget is exhausted is terminal — it does NOT fall through to the
      generic lane (matches the notes coordinators' ``break``).
    * **connection** (``connection_retries``): connection-class errors
      (``is_connection``); retried immediately, no backoff. The face
      coordinator's 1-shot connection retry; notes/Sheet-12 leave this 0.
    * **generic** (``generic_retries``): any other exception; retried
      immediately, no backoff. The notes coordinators' ``max_retries``; the
      face coordinator leaves this 0 because its attempt only re-raises
      transient errors (everything else is already a structured result).
    """

    rate_limit_retries: int
    connection_retries: int = 0
    generic_retries: int = 0
    is_rate_limit: Callable[[BaseException], bool] = _default_is_rate_limit
    is_connection: Callable[[BaseException], bool] = lambda e: False
    compute_backoff: Callable[[BaseException, int], float] = _default_compute_backoff


async def run_agent_with_retries(
    *,
    attempt: Callable[[int], Awaitable[_RetryResult]],
    policy: RetryPolicy,
    make_terminal: Callable[[BaseException, str], Awaitable[_RetryResult]],
    make_cancelled: Optional[Callable[[], Awaitable[_RetryResult]]] = None,
    discard_attempt_cleanup: Optional[Callable[[], None]] = None,
    on_retry: Optional[Callable[[int, Optional[str]], Awaitable[None]]] = None,
    on_attempt_error: Optional[Callable[[BaseException], None]] = None,
    annotate_usage: Optional[Callable[[_RetryResult], _RetryResult]] = None,
) -> _RetryResult:
    """Drive whole-attempt retries with per-lane budgets (see :class:`RetryPolicy`).

    ``attempt(retry_index)`` runs ONE whole attempt (fresh agent + deps — never
    a resumed half-run). It returns the caller's result on success (or on a
    failure it already converted to a structured result), and RAISES only for
    errors the caller wants the loop to classify/retry. ``retry_index`` is the
    number of prior attempts (0 on the first), threaded through for callers
    (Sheet-12) that pass an attempt counter into their invocation.

    Callbacks carry the per-caller side-effects so this loop stays generic:

    * ``make_terminal(exc, last_error)`` — build (and emit) the terminal
      *failed* result once budgets are exhausted.
    * ``make_cancelled()`` — build (and emit) the terminal *cancelled* result
      when a ``CancelledError`` reaches the loop (during the backoff sleep or
      the pre-retry cleanup/marker). ``None`` re-raises instead (Sheet-12,
      whose parent fan-out maps a raised ``CancelledError`` to a cancelled
      sub-agent).
    * ``discard_attempt_cleanup()`` — discard a scheduled-but-abandoned
      attempt's side-effects. Invoked in TWO places: before a retry (where a
      raise propagates — shipping stale state is worse than failing) AND on the
      ``CancelledError`` path (guarded, so a cleanup hiccup never masks the
      cancellation). The face adapter clears stale ``run_concept_facts`` + the
      scratch workbook here; notes/Sheet-12 pass ``None``.
    * ``on_retry(total_attempts, last_error)`` — emit the visible retry marker.
      ``None`` skips it (Sheet-12 retries silently).
    * ``on_attempt_error(exc)`` — per-exception bookkeeping the caller needs
      regardless of retry/terminal (face accumulates failed-attempt tokens off
      the exception; notes appends to its side-log; Sheet-12 snapshots usage).
    * ``annotate_usage(result)`` — finalize usage on EVERY returned result
      (success / cancelled / terminal). Defaults to identity.
    """
    _annotate = annotate_usage or (lambda r: r)

    rl_retries = 0
    connect_retries_used = 0
    generic_retries = 0
    total_attempts = 0
    last_error: Optional[str] = None
    # Backoff is scheduled on the previous iteration and consumed at the top
    # of the next, inside the try — so a user abort during the sleep lands on
    # the CancelledError branch (the coordinators' verbatim pattern).
    pending_backoff: float = 0.0

    while True:
        total_attempts += 1
        try:
            if pending_backoff > 0:
                await asyncio.sleep(pending_backoff)
                pending_backoff = 0.0
            if total_attempts > 1:
                # Discard the abandoned prior attempt's side-effects BEFORE the
                # retry. A raise here propagates to the except below (shipping
                # stale state silently is worse than failing the statement).
                if discard_attempt_cleanup is not None:
                    discard_attempt_cleanup()
                if on_retry is not None:
                    await on_retry(total_attempts, last_error)
            return _annotate(await attempt(total_attempts - 1))
        except asyncio.CancelledError:
            # Abort during the backoff sleep / pre-retry cleanup. (In-attempt
            # cancellation is converted to a structured result by the attempt
            # itself, where the caller does so.) The discarded attempt's
            # side-effects are still live, so run cleanup here too — guarded,
            # because a cancellation must always win.
            if discard_attempt_cleanup is not None:
                try:
                    discard_attempt_cleanup()
                except Exception:  # noqa: BLE001 — cancellation must win
                    logger.warning(
                        "failed-attempt cleanup during cancellation skipped",
                        exc_info=True,
                    )
            if make_cancelled is None:
                raise
            return _annotate(await make_cancelled())
        except Exception as e:  # noqa: BLE001 — classify-then-retry filter
            last_error = str(e)
            if on_attempt_error is not None:
                on_attempt_error(e)
            rate_limited = bool(policy.is_rate_limit(e))
            if rate_limited and rl_retries < policy.rate_limit_retries:
                pending_backoff = policy.compute_backoff(e, rl_retries)
                rl_retries += 1
                continue
            if (
                not rate_limited
                and policy.connection_retries
                and policy.is_connection(e)
                and connect_retries_used < policy.connection_retries
            ):
                connect_retries_used += 1
                continue
            if (
                not rate_limited
                and policy.generic_retries
                and generic_retries < policy.generic_retries
            ):
                generic_retries += 1
                continue
            return _annotate(await make_terminal(e, last_error))
