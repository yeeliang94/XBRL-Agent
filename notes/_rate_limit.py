"""Rate-limit aware retry helpers for the notes pipeline.

OpenAI returns HTTP 429 when a request would push the caller past the
per-minute token bucket. ``pydantic_ai`` wraps the upstream response as
``ModelHTTPError`` with the decoded body on ``exc.body`` — typically a
dict with a ``message`` field like ``"Please try again in 745ms"``. The
helpers here let the coordinator retry loops:

  1. Classify exceptions as rate-limit-or-not (``is_rate_limit_error``).
  2. Parse the upstream retry hint (``parse_retry_after``).
  3. Compute a sensible sleep before the next retry
     (``compute_backoff_delay``) — hint + jitter when available, with
     exponential fallback and a floor that survives the TPM rolling
     window.

A 429 is a throttle, not a real failure, so the rate-limit retry budget
is separate from (and larger than) the generic retry budget — otherwise
a single unlucky rollup during a notes run would consume the whole
single-retry budget for no fault of the agent.
"""
from __future__ import annotations

import random
import re
from typing import Optional

try:
    from pydantic_ai.exceptions import ModelHTTPError
except ImportError:  # pragma: no cover — pydantic-ai is a hard runtime dep
    ModelHTTPError = None  # type: ignore[misc,assignment]


# Separate budget for 429 retries. Four attempts total (1 initial + 3
# retries) comfortably covers a single TPM burst where the first wave
# saturates and the second wave takes a full 60s window to drain.
RATE_LIMIT_MAX_RETRIES = 3

# Jitter range (seconds) added to every computed backoff so 5 parallel
# agents that all hit the same 429 at the same moment don't wake at the
# same millisecond and re-collide.
_JITTER_MIN = 0.25
_JITTER_MAX = 1.25

# Floor on the sleep for rate-limit retries. OpenAI's hint ("try again
# in 745ms") is calibrated for a *small* follow-up request — our retries
# carry a full PDF-page payload, so racing back in under a second almost
# always hits the same TPM wall. 2s + jitter is the empirical sweet spot.
_RL_FLOOR = 2.0

# Exponential fallback when we can't parse a retry-after hint: 2s, 4s,
# 8s, ... before jitter.
_BASE_BACKOFF = 2.0

# Upper bound on any single retry-after sleep. Peer-review I-1: a
# malformed / pathological provider hint like ``"try again in 9999999s"``
# would otherwise pin the task for days and block parent cancellation
# while holding its task_registry slot. 120s is longer than any real
# TPM rolling window (60s) but still within an operator's tolerance
# for a single retry; past that, the operator wants to abort and
# investigate, not wait.
_RL_CEILING = 120.0

_RETRY_AFTER_RE = re.compile(
    r"try\s+again\s+in\s+([\d.]+)\s*(ms|s)\b",
    re.IGNORECASE,
)


def is_rate_limit_error(exc: BaseException) -> bool:
    """True when ``exc`` is a pydantic-ai HTTP 429 from the model provider."""
    if ModelHTTPError is None:
        return False
    return isinstance(exc, ModelHTTPError) and getattr(exc, "status_code", None) == 429


def parse_retry_after(exc: BaseException) -> Optional[float]:
    """Extract the retry-after hint from a 429 body, in seconds.

    OpenAI returns a JSON body that pydantic-ai decodes to a dict on
    ``ModelHTTPError.body``. The message typically reads ``"… Please try
    again in 745ms. Visit …"`` — we accept both ``ms`` and ``s`` units.
    Returns ``None`` when no hint is present (caller falls back to the
    exponential schedule).
    """
    if not is_rate_limit_error(exc):
        return None
    body = getattr(exc, "body", None)
    message: Optional[str] = None
    if isinstance(body, dict):
        message = body.get("message")
        if message is None:
            # LiteLLM / OpenAI-proxy shape: {"error": {"message": "..."}}
            inner = body.get("error")
            if isinstance(inner, dict):
                message = inner.get("message")
    elif isinstance(body, str):
        message = body
    if not message:
        return None
    match = _RETRY_AFTER_RE.search(message)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    seconds = value / 1000.0 if unit == "ms" else value
    # Cap the raw hint itself so downstream callers of parse_retry_after
    # (not just compute_backoff_delay) also see a sane value. A cap on
    # only the computed delay would leave the parser exposed to any
    # future consumer that trusts the hint directly.
    return min(seconds, _RL_CEILING)


def compute_backoff_delay(exc: BaseException, attempt: int) -> float:
    """Seconds to sleep before the next retry.

    ``attempt`` is the 0-indexed *retry* number (0 = first retry after
    the initial failure). For 429s we honour the retry-after hint with
    a 2s floor and a 120s ceiling; otherwise we use an exponential
    schedule (also ceilinged). Jitter is always added so a parallel
    burst de-synchronises on retry.
    """
    jitter = random.uniform(_JITTER_MIN, _JITTER_MAX)
    hint = parse_retry_after(exc)
    if hint is not None:
        return min(max(hint, _RL_FLOOR) + jitter, _RL_CEILING)
    # Exponential schedule also capped so a high ``attempt`` can't
    # produce an absurd sleep if a caller raises the retry budget.
    return min(_BASE_BACKOFF ** (attempt + 1) + jitter, _RL_CEILING)
