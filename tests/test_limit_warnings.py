"""Pinning tests for the in-band limit warner (limit_warner.py).

Harness-learnings Item 1 (docs/PLAN-pydantic-ai-v2.md Part D.3): before an
agent hits the iteration cap / token budget hard raise, an escalating
``[LIMIT WARNING]`` user-prompt part is injected into the outgoing request
so the model can wrap up gracefully. Contract pinned here:

- silent below the 70% threshold (message list byte-identical);
- exactly ONE live warning regardless of how many turns fire it;
- URGENT wording at threshold, CRITICAL inside the final stretch;
- token-budget line only when XBRL_MAX_TOKENS_PER_AGENT is set (>0);
- kill switch XBRL_LIMIT_WARNINGS=0 disables entirely;
- non-request tail (unexpected shape) is left untouched;
- the ctx parameter annotation stays ``RunContext`` (pydantic-ai 1.77
  detects ctx-taking processors purely from that hint — load-bearing,
  same contract as strip_stale_images_ctx);
- registered on the face, notes, and scout agent factories.
"""

from types import SimpleNamespace

import pytest

import limit_warner
from limit_warner import (
    WARNING_MARKER,
    limit_warning_processor,
)

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

import agent_tracing


def _ctx(steps: int = 0, total_tokens: int = 0, cap: int = None, requests: int = 0):
    """Minimal RunContext stand-in.

    ``steps``/``cap`` mirror the loop counters run_agent_loop publishes
    onto deps (_loop_iteration/_loop_max_iters — NODE units, the same
    units as the hard cap). ``requests`` feeds the fallback path for
    agents not driven by the shared runner.
    """
    import agent_tracing

    deps = SimpleNamespace()
    if steps:
        deps._loop_iteration = steps
        deps._loop_max_iters = cap or agent_tracing.MAX_AGENT_ITERATIONS
    return SimpleNamespace(
        usage=SimpleNamespace(requests=requests, total_tokens=total_tokens),
        deps=deps,
    )


def _history():
    """A tiny plausible history ending in a pending ModelRequest."""
    return [
        ModelRequest(parts=[UserPromptPart(content="extract SOFP")]),
        ModelResponse(parts=[TextPart(content="viewing pages")]),
        ModelRequest(parts=[UserPromptPart(content="tool results here")]),
    ]


def _warning_texts(messages):
    out = []
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    if p.content.startswith(WARNING_MARKER):
                        out.append(p.content)
    return out


# ---------------------------------------------------------------------------
# Threshold behaviour
# ---------------------------------------------------------------------------


def test_silent_below_threshold():
    msgs = _history()
    out = limit_warning_processor(_ctx(steps=5), msgs)
    assert _warning_texts(out) == []
    # No structural change either — same parts in same order.
    assert [type(m) for m in out] == [type(m) for m in msgs]
    assert len(out[-1].parts) == len(msgs[-1].parts)


def test_urgent_at_threshold():
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    used = int(cap * 0.75)
    out = limit_warning_processor(_ctx(steps=used), _history())
    warnings = _warning_texts(out)
    assert len(warnings) == 1
    assert "URGENT" in warnings[0]
    assert f"{used}/{cap}" in warnings[0]
    # The warning rides on the LAST request, appended after existing parts.
    assert out[-1].parts[-1].content.startswith(WARNING_MARKER)


def test_critical_in_final_stretch():
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    out = limit_warning_processor(_ctx(steps=cap - 2), _history())
    warnings = _warning_texts(out)
    assert len(warnings) == 1
    assert "CRITICAL" in warnings[0]
    assert "URGENT" not in warnings[0]


def test_exactly_one_warning_across_turns():
    """Re-running the processor on already-warned history never stacks."""
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    msgs = _history()
    first = limit_warning_processor(_ctx(steps=int(cap * 0.75)), msgs)
    # Next turn: history grows, processor runs again with higher usage.
    grown = [
        *first,
        ModelResponse(parts=[TextPart(content="writing rows")]),
        ModelRequest(parts=[UserPromptPart(content="more tool results")]),
    ]
    second = limit_warning_processor(_ctx(steps=cap - 1), grown)
    warnings = _warning_texts(second)
    assert len(warnings) == 1
    # And it reflects the LATEST usage, not the stale first warning.
    assert f"{cap - 1}/{cap}" in warnings[0]


# ---------------------------------------------------------------------------
# Token budget line
# ---------------------------------------------------------------------------


def test_token_budget_line_only_when_budget_set(monkeypatch):
    monkeypatch.delenv("XBRL_MAX_TOKENS_PER_AGENT", raising=False)
    out = limit_warning_processor(_ctx(steps=1, total_tokens=10_000_000), _history())
    assert _warning_texts(out) == []

    monkeypatch.setenv("XBRL_MAX_TOKENS_PER_AGENT", "100000")
    out = limit_warning_processor(_ctx(steps=1, total_tokens=80_000), _history())
    warnings = _warning_texts(out)
    assert len(warnings) == 1
    assert "Token budget" in warnings[0]
    assert "URGENT" in warnings[0]


def test_token_budget_critical(monkeypatch):
    monkeypatch.setenv("XBRL_MAX_TOKENS_PER_AGENT", "100000")
    out = limit_warning_processor(_ctx(steps=1, total_tokens=96_000), _history())
    warnings = _warning_texts(out)
    assert len(warnings) == 1
    assert "CRITICAL" in warnings[0]


# ---------------------------------------------------------------------------
# Safety valves
# ---------------------------------------------------------------------------


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("XBRL_LIMIT_WARNINGS", "0")
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    msgs = _history()
    out = limit_warning_processor(_ctx(steps=cap - 1), msgs)
    assert out is msgs  # disabled path returns the input untouched


def test_kill_switch_strips_stale_warning_mid_run(monkeypatch):
    """Flipping the switch OFF after a warning was injected must remove it
    — 'off' means no warning is live, not 'freeze whatever is there'."""
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    with_warning = limit_warning_processor(_ctx(steps=cap - 1), _history())
    assert _warning_texts(with_warning), "precondition: warning injected"

    monkeypatch.setenv("XBRL_LIMIT_WARNINGS", "0")
    out = limit_warning_processor(_ctx(steps=cap - 1), with_warning)
    assert _warning_texts(out) == []


def test_unexpected_tail_shape_skips_injection():
    """History not ending in a ModelRequest: warn nothing, break nothing."""
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="extract")]),
        ModelResponse(parts=[TextPart(content="thinking")]),
    ]
    out = limit_warning_processor(_ctx(steps=cap - 1), msgs)
    assert _warning_texts(out) == []
    assert len(out) == 2


def test_purity_inputs_not_mutated():
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    msgs = _history()
    before_parts = list(msgs[-1].parts)
    limit_warning_processor(_ctx(steps=cap - 1), msgs)
    assert msgs[-1].parts == before_parts  # original request untouched


# ---------------------------------------------------------------------------
# Wiring contracts
# ---------------------------------------------------------------------------


def test_ctx_annotation_is_run_context():
    """pydantic-ai detects the ctx-taking variant from the annotation —
    the same load-bearing contract test_history_processor_escalation pins
    for the compaction wrappers."""
    from pydantic_ai._utils import takes_run_context

    assert takes_run_context(limit_warning_processor)


def test_registered_on_all_three_agent_factories():
    import inspect

    import extraction.agent as face_mod
    import notes.agent as notes_mod
    import scout.agent as scout_mod

    for mod in (face_mod, notes_mod, scout_mod):
        src = inspect.getsource(mod)
        assert "limit_warning_processor" in src, mod.__name__


# ---------------------------------------------------------------------------
# Unit contract (2026-07-12 V2-review fix) — node units, warning before cap
# ---------------------------------------------------------------------------


def test_warning_fires_before_hard_cap_in_node_units():
    """Walk the counter like the real loop (one node at a time): the first
    warning must arrive strictly BEFORE the iteration the hard cap fires
    (iteration > max_iters). This is the regression the V2 review caught —
    a warner keyed on model requests could never fire before a node-based
    cap."""
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    first_warned_at = None
    for step in range(1, cap + 1):  # the cap raises at step cap+1
        out = limit_warning_processor(_ctx(steps=step, cap=cap), _history())
        if _warning_texts(out):
            first_warned_at = step
            break
    assert first_warned_at is not None, "warning never fired before the cap"
    assert first_warned_at <= cap  # strictly before IterationLimitReached
    # And with useful headroom: at ~70%, not in the final steps.
    assert first_warned_at <= int(cap * 0.75)


def test_fallback_approximates_nodes_from_requests():
    """Agents not driven by run_agent_loop (no published counters) fall back
    to nodes ~= 2*requests - 1 so the unit stays comparable to the cap."""
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    # requests such that 2*r-1 >= 0.7*cap  -> warning fires
    r = (int(cap * 0.7) + 2) // 2 + 1
    out = limit_warning_processor(_ctx(requests=r), _history())
    assert _warning_texts(out)


def test_runner_publishes_loop_counters():
    """run_agent_loop publishes _loop_iteration/_loop_max_iters onto deps
    every node — the warner's primary counter source (source-level pin)."""
    import inspect

    import agent_runner

    src = inspect.getsource(agent_runner.run_agent_loop)
    assert "deps._loop_iteration = iteration" in src
    assert "deps._loop_max_iters = spec.max_iters" in src
    # published BEFORE the cap check so the warner sees the compared value
    assert src.index("deps._loop_iteration") < src.index("raise IterationLimitReached")
