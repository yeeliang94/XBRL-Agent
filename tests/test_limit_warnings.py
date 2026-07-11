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


def _ctx(requests: int, total_tokens: int = 0):
    """Minimal stand-in for RunContext: the processor only reads .usage."""
    return SimpleNamespace(
        usage=SimpleNamespace(requests=requests, total_tokens=total_tokens)
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
    out = limit_warning_processor(_ctx(requests=5), msgs)
    assert _warning_texts(out) == []
    # No structural change either — same parts in same order.
    assert [type(m) for m in out] == [type(m) for m in msgs]
    assert len(out[-1].parts) == len(msgs[-1].parts)


def test_urgent_at_threshold():
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    used = int(cap * 0.75)
    out = limit_warning_processor(_ctx(requests=used), _history())
    warnings = _warning_texts(out)
    assert len(warnings) == 1
    assert "URGENT" in warnings[0]
    assert f"{used}/{cap}" in warnings[0]
    # The warning rides on the LAST request, appended after existing parts.
    assert out[-1].parts[-1].content.startswith(WARNING_MARKER)


def test_critical_in_final_stretch():
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    out = limit_warning_processor(_ctx(requests=cap - 2), _history())
    warnings = _warning_texts(out)
    assert len(warnings) == 1
    assert "CRITICAL" in warnings[0]
    assert "URGENT" not in warnings[0]


def test_exactly_one_warning_across_turns():
    """Re-running the processor on already-warned history never stacks."""
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    msgs = _history()
    first = limit_warning_processor(_ctx(requests=int(cap * 0.75)), msgs)
    # Next turn: history grows, processor runs again with higher usage.
    grown = [
        *first,
        ModelResponse(parts=[TextPart(content="writing rows")]),
        ModelRequest(parts=[UserPromptPart(content="more tool results")]),
    ]
    second = limit_warning_processor(_ctx(requests=cap - 1), grown)
    warnings = _warning_texts(second)
    assert len(warnings) == 1
    # And it reflects the LATEST usage, not the stale first warning.
    assert f"{cap - 1}/{cap}" in warnings[0]


# ---------------------------------------------------------------------------
# Token budget line
# ---------------------------------------------------------------------------


def test_token_budget_line_only_when_budget_set(monkeypatch):
    monkeypatch.delenv("XBRL_MAX_TOKENS_PER_AGENT", raising=False)
    out = limit_warning_processor(_ctx(requests=1, total_tokens=10_000_000), _history())
    assert _warning_texts(out) == []

    monkeypatch.setenv("XBRL_MAX_TOKENS_PER_AGENT", "100000")
    out = limit_warning_processor(_ctx(requests=1, total_tokens=80_000), _history())
    warnings = _warning_texts(out)
    assert len(warnings) == 1
    assert "Token budget" in warnings[0]
    assert "URGENT" in warnings[0]


def test_token_budget_critical(monkeypatch):
    monkeypatch.setenv("XBRL_MAX_TOKENS_PER_AGENT", "100000")
    out = limit_warning_processor(_ctx(requests=1, total_tokens=96_000), _history())
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
    out = limit_warning_processor(_ctx(requests=cap - 1), msgs)
    assert out is msgs  # disabled path returns the input untouched


def test_unexpected_tail_shape_skips_injection():
    """History not ending in a ModelRequest: warn nothing, break nothing."""
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="extract")]),
        ModelResponse(parts=[TextPart(content="thinking")]),
    ]
    out = limit_warning_processor(_ctx(requests=cap - 1), msgs)
    assert _warning_texts(out) == []
    assert len(out) == 2


def test_purity_inputs_not_mutated():
    cap = agent_tracing.MAX_AGENT_ITERATIONS
    msgs = _history()
    before_parts = list(msgs[-1].parts)
    limit_warning_processor(_ctx(requests=cap - 1), msgs)
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
