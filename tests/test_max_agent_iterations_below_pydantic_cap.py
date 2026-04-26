"""PLAN-stop-and-validation-visibility Phase 0.3 — pin the iteration cap
below pydantic-ai's silent ``request_limit=50``.

The 2026-04-26 user-reported incident (terminal traceback:
``pydantic_ai.exceptions.UsageLimitExceeded: request_limit of 50``) was
caused by ``MAX_AGENT_ITERATIONS = 50`` racing pydantic-ai's silent cap
of the same value. Whichever fires first owns the error message; in the
incident case pydantic-ai won and the user got an unactionable
traceback instead of our structured "iteration limit hit" message.

Lowering ``MAX_AGENT_ITERATIONS`` to a value strictly less than 50
guarantees our cap fires first and emits a structured SSE error before
pydantic-ai's silent cap can. This test pins that contract so a later
"raise the cap because it's too tight" change can't silently re-create
the incident.
"""
from __future__ import annotations

import os

import pytest


# Pydantic-ai 1.77's default ``UsageLimits.request_limit`` is 50 — see
# the smoking-gun screenshot in
# docs/PLAN-stop-and-validation-visibility.md. We never set explicit
# UsageLimits anywhere in the codebase, so this default is what every
# agent inherits. The buffer below it (5+ turns) absorbs the request
# overhead pydantic-ai itself spends on tool-call book-keeping inside a
# single iteration node.
PYDANTIC_AI_SILENT_REQUEST_LIMIT = 50
SAFE_BUFFER = 5


def test_max_agent_iterations_stays_below_pydantic_silent_cap():
    """Our turn cap must fire at least ``SAFE_BUFFER`` turns before
    pydantic-ai's silent 50-cap so the user sees an actionable
    "Hit iteration limit" message rather than ``UsageLimitExceeded``.
    """
    from agent_tracing import MAX_AGENT_ITERATIONS
    assert MAX_AGENT_ITERATIONS <= PYDANTIC_AI_SILENT_REQUEST_LIMIT - SAFE_BUFFER, (
        f"MAX_AGENT_ITERATIONS={MAX_AGENT_ITERATIONS} is too close to "
        f"pydantic-ai's silent request_limit={PYDANTIC_AI_SILENT_REQUEST_LIMIT}. "
        f"Drop it to <= {PYDANTIC_AI_SILENT_REQUEST_LIMIT - SAFE_BUFFER} or "
        f"pydantic-ai will fire its UsageLimitExceeded first and the user "
        f"will see an unactionable traceback instead of our structured "
        f"'Hit iteration limit' SSE error."
    )


def test_env_override_takes_effect(monkeypatch):
    """``XBRL_MAX_AGENT_ITERATIONS`` env override allows operators to
    tune the cap without a redeploy when a particular workload genuinely
    needs more headroom (and they're willing to risk pydantic-ai's
    silent cap firing instead).

    Reload the module under the override so the constant is recomputed.
    """
    import importlib

    monkeypatch.setenv("XBRL_MAX_AGENT_ITERATIONS", "30")
    import agent_tracing
    importlib.reload(agent_tracing)
    try:
        assert agent_tracing.MAX_AGENT_ITERATIONS == 30
    finally:
        # Restore the env-free default for the rest of the test session.
        monkeypatch.delenv("XBRL_MAX_AGENT_ITERATIONS", raising=False)
        importlib.reload(agent_tracing)


def test_env_override_clamped_to_safe_ceiling(monkeypatch, caplog):
    """Peer-review fix (2026-04-27): an operator who sets
    ``XBRL_MAX_AGENT_ITERATIONS=50`` (or anything ≥ 50) reintroduces
    the exact UsageLimitExceeded race this whole change was supposed
    to prevent. Clamp to 45 with a loud warning."""
    import importlib

    monkeypatch.setenv("XBRL_MAX_AGENT_ITERATIONS", "60")
    import agent_tracing
    with caplog.at_level("WARNING"):
        importlib.reload(agent_tracing)
    try:
        # Clamped, not 60.
        assert agent_tracing.MAX_AGENT_ITERATIONS == 45
        # Below pydantic-ai's silent cap by the required buffer.
        assert agent_tracing.MAX_AGENT_ITERATIONS < 50
        # Operator gets a loud warning so they know their override was overridden.
        clamp_warnings = [
            r for r in caplog.records
            if "exceeds safe ceiling" in r.getMessage()
        ]
        assert clamp_warnings, (
            "Clamping must log a warning so operators notice their "
            "override didn't take effect."
        )
    finally:
        monkeypatch.delenv("XBRL_MAX_AGENT_ITERATIONS", raising=False)
        importlib.reload(agent_tracing)


def test_env_override_at_safe_ceiling_is_accepted(monkeypatch):
    """An override at exactly the safe ceiling (45) is accepted as-is."""
    import importlib

    monkeypatch.setenv("XBRL_MAX_AGENT_ITERATIONS", "45")
    import agent_tracing
    importlib.reload(agent_tracing)
    try:
        assert agent_tracing.MAX_AGENT_ITERATIONS == 45
    finally:
        monkeypatch.delenv("XBRL_MAX_AGENT_ITERATIONS", raising=False)
        importlib.reload(agent_tracing)
