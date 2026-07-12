"""In-band limit warnings before hard caps (Harness-learnings Item 1).

Problem: when an agent hits the iteration cap or token budget, the runner
raises (``IterationLimitReached`` / ``TokenBudgetExceeded``) and kills the
run mid-thought — the agent is never told the end is near, so work in
flight is lost even when it was one turn from saving. This module gives
the model an explicit, escalating "wrap up now" nudge *before* the hard
stop, so it can land its writes and call its terminal save tool.

Mechanism (pattern borrowed from pydantic-ai-harness LimitWarner, re-
implemented natively for 1.x): a ctx-aware history processor that runs
before every model request. At >= 70% of a tracked limit it appends a
``[LIMIT WARNING]``-marked user-prompt part to the outgoing request;
within the final stretch the severity escalates to CRITICAL. It first
strips any previously injected warning, so exactly ONE warning is live
at any time (idempotent — the processed history is persisted back onto
run state, gotcha #6, and must not accumulate stale nudges).

Two limits are tracked, read from the same sources the hard raises use:

- iterations: ``ctx.usage.requests`` vs ``agent_tracing.MAX_AGENT_ITERATIONS``
- token budget: ``ctx.usage.total_tokens`` vs ``agent_runner.resolve_token_budget()``
  (0 = budget disabled = no token warning)

The wall-clock cap is deliberately NOT warned about here: a history
processor has no access to the runner's per-run deadline, and threading
it through would couple this module to the loop internals. Deferred —
see docs/PLAN-pydantic-ai-v2.md Part D.3 Item 1.

Design constraints honoured:

- The warning is appended as an extra ``UserPromptPart`` INSIDE the
  current (last) ``ModelRequest`` — never as a new standalone message —
  so strict-alternation providers never see two consecutive requests.
- Pure over its inputs: rebuilt lists + ``dataclasses.replace``, never
  in-place mutation (the history_processors purity contract).
- Kill switch ``XBRL_LIMIT_WARNINGS`` (default on), read at call time so
  tests and operators can flip it without re-importing.
"""

# NOTE: no `from __future__ import annotations` here — pydantic-ai 1.77's
# `takes_run_context` must see the real RunContext annotation on the ctx
# parameter (string annotations would defeat the detection); same contract
# as extraction/history_processors.py.
import logging
import os
from dataclasses import replace
from typing import List, Optional

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

from agent_runner import resolve_token_budget
from agent_tracing import MAX_AGENT_ITERATIONS

logger = logging.getLogger(__name__)

WARNING_MARKER = "[LIMIT WARNING]"

# Warn once usage crosses this fraction of a limit. 70% mirrors the
# harness default; tune from Telemetry-tab evidence (open question F.2 in
# docs/PLAN-pydantic-ai-v2.md).
WARN_FRACTION = 0.70
# Escalate to CRITICAL wording inside the final stretch. UNITS ARE LOOP
# STEPS (graph nodes) — a model turn plus its tool batch is ~2 steps, so 5
# remaining steps leaves the model roughly two turns: finish writes, save.
CRITICAL_REMAINING_ITERATIONS = 5
CRITICAL_TOKEN_FRACTION = 0.95


def _enabled() -> bool:
    """Kill switch, read per-call (default ON)."""
    return os.environ.get("XBRL_LIMIT_WARNINGS", "1").strip().lower() not in (
        "0",
        "false",
        "off",
    )


def _is_warning_part(part: object) -> bool:
    return (
        isinstance(part, UserPromptPart)
        and isinstance(part.content, str)
        and part.content.startswith(WARNING_MARKER)
    )


def _strip_warnings(messages: List[ModelMessage]) -> List[ModelMessage]:
    """Drop previously injected warning parts (never other content).

    Keeps exactly-one-live-warning semantics: we re-derive severity fresh
    each turn instead of stacking stale nudges the model already read.
    """
    out: List[ModelMessage] = []
    for msg in messages:
        if isinstance(msg, ModelRequest) and any(
            _is_warning_part(p) for p in msg.parts
        ):
            kept = [p for p in msg.parts if not _is_warning_part(p)]
            if not kept:
                # A request that was ONLY our warning (shouldn't happen —
                # we always append to an existing request) is dropped
                # entirely rather than sent empty.
                continue
            out.append(replace(msg, parts=kept))
        else:
            out.append(msg)
    return out


def _build_warning(ctx) -> Optional[str]:
    """Compose the warning text from current usage, or None if under thresholds."""
    usage = getattr(ctx, "usage", None)
    if usage is None:
        return None

    lines: List[str] = []
    critical = False

    # --- Iterations (always tracked; the cap always exists) ---
    # UNIT CONTRACT (2026-07-12 V2-review fix): the hard cap in
    # agent_runner counts graph NODES (model-request and call-tools nodes
    # alternate), NOT model requests. Compare like with like: prefer the
    # live loop counter + per-run cap the runner publishes onto deps;
    # fall back to a documented nodes≈2×requests approximation for agents
    # not driven by run_agent_loop (e.g. scout's own loop).
    deps = getattr(ctx, "deps", None)
    used = getattr(deps, "_loop_iteration", None)
    cap = getattr(deps, "_loop_max_iters", None)
    if used is None:
        req = int(getattr(usage, "requests", 0) or 0)
        used = max(2 * req - 1, 0)
    if not cap:
        cap = MAX_AGENT_ITERATIONS
    used = int(used)
    cap = int(cap)
    if cap > 0 and used / cap >= WARN_FRACTION:
        remaining = max(cap - used, 0)
        pct = int(round(100 * used / cap))
        lines.append(
            f"Turns: {used}/{cap} loop steps used ({pct}%); "
            f"{remaining} remaining (a model turn + its tools is ~2 steps)."
        )
        if remaining <= CRITICAL_REMAINING_ITERATIONS:
            critical = True

    # --- Token budget (opt-in via XBRL_MAX_TOKENS_PER_AGENT; 0 = off) ---
    budget = resolve_token_budget()
    if budget > 0:
        total = int(getattr(usage, "total_tokens", 0) or 0)
        frac = total / budget
        if frac >= WARN_FRACTION:
            pct = int(round(100 * frac))
            lines.append(f"Token budget: {total:,}/{budget:,} used ({pct}%).")
            if frac >= CRITICAL_TOKEN_FRACTION:
                critical = True

    if not lines:
        return None

    severity = "CRITICAL" if critical else "URGENT"
    guidance = (
        "Finish NOW: complete any pending write, then call your terminal "
        "save/summary tool this turn. Do not open new pages or start new "
        "investigation."
        if critical
        else "Start wrapping up: prioritise completing the remaining writes "
        "and reaching your terminal save/summary tool. Avoid opening new "
        "lines of investigation."
    )
    return (
        f"{WARNING_MARKER} {severity}: run limits are approaching. "
        + " ".join(lines)
        + " "
        + guidance
    )


def limit_warning_processor(
    ctx: RunContext, messages: List[ModelMessage]
) -> List[ModelMessage]:
    """History processor: inject/refresh the single in-band limit warning.

    The first parameter MUST stay annotated ``RunContext`` — pydantic-ai
    1.77 detects the ctx-taking variant purely from that type hint (same
    load-bearing contract as ``strip_stale_images_ctx``).
    """
    if not _enabled():
        # Disabled mid-run: a warning injected while enabled must not
        # linger (off means NO warning is live). Clean histories return
        # the input object untouched — pinned by test_kill_switch.
        if any(
            isinstance(m, ModelRequest) and any(_is_warning_part(p) for p in m.parts)
            for m in messages
        ):
            return _strip_warnings(list(messages))
        return messages

    out = _strip_warnings(list(messages))
    text = _build_warning(ctx)
    if text is None:
        return out

    # Append INTO the current request (the last message). If the last
    # message is not a ModelRequest — an unexpected shape — skip this
    # turn rather than risk a malformed conversation; the hard cap still
    # protects the run.
    if not out or not isinstance(out[-1], ModelRequest):
        return out
    last = out[-1]
    out[-1] = replace(last, parts=[*last.parts, UserPromptPart(content=text)])
    return out
