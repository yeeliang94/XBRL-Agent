"""Pinning test (gotcha #18 parallel for the monolith path).

The monolith coordinator passes its OWN `UsageLimits.request_limit` to
pydantic-ai's `Agent.iter`. That request limit must stay strictly
greater than the monolith iteration cap, or pydantic-ai's silent
UsageLimitExceeded fires before our structured `iteration_exhausted`
SSE outcome can — exactly the 2026-04-26 incident the split-path
constant was renamed to prevent.
"""
from __future__ import annotations

from monolith.config import (
    MAX_AGENT_ITERATIONS_MONOLITH,
    MONOLITH_REQUEST_LIMIT,
)


def test_monolith_iteration_cap_strictly_below_request_limit():
    assert MAX_AGENT_ITERATIONS_MONOLITH < MONOLITH_REQUEST_LIMIT, (
        f"MAX_AGENT_ITERATIONS_MONOLITH ({MAX_AGENT_ITERATIONS_MONOLITH}) "
        f"must be strictly less than MONOLITH_REQUEST_LIMIT "
        f"({MONOLITH_REQUEST_LIMIT}); otherwise pydantic-ai's silent "
        "UsageLimitExceeded races our structured iteration_exhausted "
        "outcome (gotcha #18, monolith parallel)."
    )


def test_monolith_iteration_cap_has_sensible_default():
    # The default should be high enough for a 5-statement workload.
    # Operators can override via env; we don't pin the exact number, only
    # that it's at least 60 (a per-statement specialist gets ~40).
    assert MAX_AGENT_ITERATIONS_MONOLITH >= 60
