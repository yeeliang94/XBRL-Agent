"""Unit tests for the shared retry scaffold (agent_runner.run_agent_with_retries).

Drives the loop with fake ``attempt`` callables (no real agents) to pin the
mechanics each coordinator depends on: per-lane budgets, backoff scheduling,
failed-attempt bookkeeping, the pre-retry + on-cancel cleanup hook (gotcha #10
stale-data contract), and terminal/cancelled construction.

PLAN-orchestration-seams Part A / Phase A2, Step 3.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from agent_runner import RetryPolicy, run_agent_with_retries


def _run(coro):
    return asyncio.run(coro)


class _Rate(Exception):
    """Stand-in classified as rate-limited by the test policy."""


def _face_policy() -> RetryPolicy:
    """Face budgets: 1 connection retry + N rate-limit retries, no generic."""
    return RetryPolicy(
        rate_limit_retries=3,
        connection_retries=1,
        generic_retries=0,
        is_rate_limit=lambda e: isinstance(e, _Rate),
        is_connection=lambda e: isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)),
        compute_backoff=lambda e, n: 0.0,  # no real sleeping in tests
    )


def _notes_policy(max_retries: int = 1) -> RetryPolicy:
    """Notes budgets: max_retries generic + N rate-limit retries."""
    return RetryPolicy(
        rate_limit_retries=3,
        connection_retries=0,
        generic_retries=max_retries,
        is_rate_limit=lambda e: isinstance(e, _Rate),
        compute_backoff=lambda e, n: 0.0,
    )


# --- success / no-retry ---------------------------------------------------- #

def test_first_attempt_success_returns_directly():
    calls = []

    async def attempt(idx):
        calls.append(idx)
        return "ok"

    async def make_terminal(exc, msg):
        raise AssertionError("should not reach terminal")

    out = _run(run_agent_with_retries(
        attempt=attempt, policy=_face_policy(), make_terminal=make_terminal,
    ))
    assert out == "ok"
    assert calls == [0]  # retry_index 0, no retries


# --- rate-limit lane ------------------------------------------------------- #

def test_rate_limit_backs_off_then_succeeds():
    seq = iter([_Rate(), None])
    backoffs = []

    async def attempt(idx):
        e = next(seq)
        if e is not None:
            raise e
        return f"ok@{idx}"

    async def make_terminal(exc, msg):
        raise AssertionError("should not reach terminal")

    policy = _face_policy()
    policy.compute_backoff = lambda e, n: backoffs.append(n) or 0.0

    out = _run(run_agent_with_retries(
        attempt=attempt, policy=policy, make_terminal=make_terminal,
    ))
    assert out == "ok@1"  # succeeded on the retry
    assert backoffs == [0]  # backoff computed once, with prior-retry count 0


def test_rate_limit_exhausted_is_terminal_not_generic():
    """A rate-limit error out of budget goes terminal — it must NOT fall
    through to the generic lane (notes' break semantics)."""
    attempts = []
    generic_seen = []

    async def attempt(idx):
        attempts.append(idx)
        raise _Rate()

    async def make_terminal(exc, msg):
        return ("terminal", type(exc).__name__)

    policy = _notes_policy(max_retries=5)  # generous generic budget...
    policy.rate_limit_retries = 2          # ...but tiny rate-limit budget
    out = _run(run_agent_with_retries(
        attempt=attempt, policy=policy, make_terminal=make_terminal,
    ))
    assert out == ("terminal", "_Rate")
    # 1 initial + 2 rate-limit retries = 3 attempts; generic budget untouched.
    assert attempts == [0, 1, 2]


# --- connection lane (face) ------------------------------------------------ #

def test_connection_error_one_retry_then_terminal():
    attempts = []

    async def attempt(idx):
        attempts.append(idx)
        raise httpx.ConnectError("down")

    async def make_terminal(exc, msg):
        return "failed"

    out = _run(run_agent_with_retries(
        attempt=attempt, policy=_face_policy(), make_terminal=make_terminal,
    ))
    assert out == "failed"
    assert attempts == [0, 1]  # one connection retry, then terminal


# --- generic lane (notes) -------------------------------------------------- #

def test_generic_error_uses_generic_budget():
    attempts = []

    async def attempt(idx):
        attempts.append(idx)
        raise ValueError("boom")

    async def make_terminal(exc, msg):
        return "failed"

    out = _run(run_agent_with_retries(
        attempt=attempt, policy=_notes_policy(max_retries=1), make_terminal=make_terminal,
    ))
    assert out == "failed"
    assert attempts == [0, 1]  # one generic retry then terminal


def test_face_generic_error_is_terminal_immediately():
    """Face leaves generic_retries=0, so a non-transient error is terminal
    on the first attempt (its attempt only re-raises transient errors)."""
    attempts = []

    async def attempt(idx):
        attempts.append(idx)
        raise ValueError("non-transient")

    async def make_terminal(exc, msg):
        return "failed"

    out = _run(run_agent_with_retries(
        attempt=attempt, policy=_face_policy(), make_terminal=make_terminal,
    ))
    assert out == "failed"
    assert attempts == [0]


# --- cleanup hook: pre-retry + on-cancel (gotcha #10) ---------------------- #

def test_cleanup_runs_before_each_retry():
    cleanups = []
    seq = iter([_Rate(), None])

    async def attempt(idx):
        e = next(seq)
        if e is not None:
            raise e
        return "ok"

    async def make_terminal(exc, msg):
        raise AssertionError

    _run(run_agent_with_retries(
        attempt=attempt, policy=_face_policy(), make_terminal=make_terminal,
        discard_attempt_cleanup=lambda: cleanups.append("clean"),
    ))
    assert cleanups == ["clean"]  # ran once before the single retry


def test_cancelled_during_backoff_runs_cleanup_and_terminal_cancelled():
    cleanups = []

    async def attempt(idx):
        if idx == 0:
            raise _Rate()  # schedules a backoff for the next iteration
        raise AssertionError("second attempt should not start")

    async def make_terminal(exc, msg):
        raise AssertionError("terminal not expected on cancel")

    async def make_cancelled():
        return "cancelled-result"

    # compute_backoff sleeps; cancel the sleep.
    async def driver():
        policy = _face_policy()
        policy.compute_backoff = lambda e, n: 3600.0  # long sleep
        task = asyncio.ensure_future(run_agent_with_retries(
            attempt=attempt, policy=policy, make_terminal=make_terminal,
            make_cancelled=make_cancelled,
            discard_attempt_cleanup=lambda: cleanups.append("clean"),
        ))
        await asyncio.sleep(0.05)  # let it reach the backoff sleep
        task.cancel()
        return await task

    out = _run(driver())
    assert out == "cancelled-result"
    assert cleanups == ["clean"]  # cleanup ran on the cancel path


def test_cancelled_with_no_make_cancelled_reraises():
    async def attempt(idx):
        if idx == 0:
            raise _Rate()
        raise AssertionError

    async def make_terminal(exc, msg):
        raise AssertionError

    async def driver():
        policy = _face_policy()
        policy.compute_backoff = lambda e, n: 3600.0
        task = asyncio.ensure_future(run_agent_with_retries(
            attempt=attempt, policy=policy, make_terminal=make_terminal,
            make_cancelled=None,  # Sheet-12: re-raise
        ))
        await asyncio.sleep(0.05)
        task.cancel()
        return await task

    with pytest.raises(asyncio.CancelledError):
        _run(driver())


def test_cleanup_raise_on_pre_retry_propagates_to_terminal():
    """Pre-retry cleanup that raises must NOT be swallowed — it propagates to
    the except and the run goes terminal (face's stale-facts contract)."""
    async def attempt(idx):
        raise _Rate()  # forces a retry → pre-retry cleanup on the 2nd loop

    seen = {}

    async def make_terminal(exc, msg):
        seen["exc"] = type(exc).__name__
        return "terminal"

    def cleanup():
        raise RuntimeError("db error clearing facts")

    out = _run(run_agent_with_retries(
        attempt=attempt, policy=_face_policy(), make_terminal=make_terminal,
        discard_attempt_cleanup=cleanup,
    ))
    assert out == "terminal"
    # The cleanup RuntimeError became the terminal exception (non-transient).
    assert seen["exc"] == "RuntimeError"


# --- bookkeeping callbacks ------------------------------------------------- #

def test_on_attempt_error_and_annotate_usage_fire():
    errors = []

    async def attempt(idx):
        raise ValueError("x")

    async def make_terminal(exc, msg):
        return {"status": "failed"}

    def annotate(result):
        result["annotated"] = True
        return result

    out = _run(run_agent_with_retries(
        attempt=attempt, policy=_notes_policy(max_retries=0),
        make_terminal=make_terminal,
        on_attempt_error=lambda e: errors.append(type(e).__name__),
        annotate_usage=annotate,
    ))
    assert out == {"status": "failed", "annotated": True}
    assert errors == ["ValueError"]  # called once (no retries: budget 0)


def test_on_retry_marker_carries_attempt_and_last_error():
    markers = []
    seq = iter([ValueError("first failure"), None])

    async def attempt(idx):
        e = next(seq)
        if e is not None:
            raise e
        return "ok"

    async def make_terminal(exc, msg):
        raise AssertionError

    async def on_retry(total_attempts, last_error):
        markers.append((total_attempts, last_error))

    out = _run(run_agent_with_retries(
        attempt=attempt, policy=_notes_policy(max_retries=1),
        make_terminal=make_terminal, on_retry=on_retry,
    ))
    assert out == "ok"
    assert markers == [(2, "first failure")]


def test_retry_lanes_emit_labeled_warning_logs(caplog):
    """Per-lane retry visibility (peer-review): each retry lane logs a WARNING
    prefixed with the caller-supplied label — restores the rate-limit/backoff
    diagnostics the per-coordinator loops had (and Sheet-12 never got)."""
    import logging as _logging

    # rate-limit lane (backoff delay in the message)
    seq = iter([_Rate(), None])

    async def attempt(idx):
        e = next(seq)
        if e is not None:
            raise e
        return "ok"

    async def make_terminal(exc, msg):
        raise AssertionError

    policy = _face_policy()
    policy.compute_backoff = lambda e, n: 2.0
    with caplog.at_level(_logging.WARNING, logger="agent_runner"):
        _run(run_agent_with_retries(
            attempt=attempt, policy=policy, make_terminal=make_terminal,
            label="Face agent SOFP",
        ))
    msgs = [r.getMessage() for r in caplog.records]
    assert any("Face agent SOFP" in m and "rl-retry 1/3" in m and "2.00s" in m
               for m in msgs), msgs

    # generic lane
    caplog.clear()
    seq2 = iter([ValueError("boom"), None])

    async def attempt2(idx):
        e = next(seq2)
        if e is not None:
            raise e
        return "ok"

    with caplog.at_level(_logging.WARNING, logger="agent_runner"):
        _run(run_agent_with_retries(
            attempt=attempt2, policy=_notes_policy(max_retries=1),
            make_terminal=make_terminal, label="Sub-agent sub0",
        ))
    msgs2 = [r.getMessage() for r in caplog.records]
    assert any("Sub-agent sub0" in m and "generic-retry 1/1" in m for m in msgs2), msgs2


def test_annotate_usage_applies_to_success_too():
    async def attempt(idx):
        return {"ok": True}

    async def make_terminal(exc, msg):
        raise AssertionError

    out = _run(run_agent_with_retries(
        attempt=attempt, policy=_face_policy(), make_terminal=make_terminal,
        annotate_usage=lambda r: {**r, "seen": 1},
    ))
    assert out == {"ok": True, "seen": 1}
