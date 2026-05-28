"""Monolith-path constants.

Kept separate from `agent_tracing.MAX_AGENT_ITERATIONS` so the experiment
can carry its own (higher) cap without weakening the split-pipeline
invariant pinned by
`tests/test_max_agent_iterations_below_pydantic_cap.py` (gotcha #18).

The monolith does ~5× the work of any specialist (PRD §9), so 40 iterations
isn't enough. We lift to 80 internally and pass
`UsageLimits(request_limit=100)` explicitly to pydantic-ai so its silent
50-ceiling doesn't fire first. The pinning test
`tests/test_monolith_iteration_cap.py` asserts strict ordering.
"""
from __future__ import annotations

import os


def _resolve_monolith_max_iterations() -> int:
    raw = os.environ.get("XBRL_MAX_AGENT_ITERATIONS_MONOLITH", "")
    if not raw:
        return 80
    try:
        v = int(raw)
    except ValueError:
        return 80
    if v <= 0:
        return 80
    # Caller-supplied UsageLimits.request_limit must stay strictly above
    # our iteration cap or pydantic-ai wins the race (gotcha #18). The cap
    # here is the iteration cap; the `request_limit` constant below tracks
    # it with a comfortable buffer.
    return v


MAX_AGENT_ITERATIONS_MONOLITH: int = _resolve_monolith_max_iterations()
# Explicit UsageLimits.request_limit passed to pydantic-ai. Must be
# strictly greater than MAX_AGENT_ITERATIONS_MONOLITH or pydantic-ai's
# UsageLimitExceeded fires before our structured `iteration_exhausted`
# outcome can. Default 100 leaves a 20-turn buffer for pydantic-ai's
# per-iteration overhead, mirroring the 40/50 buffer in the split path.
MONOLITH_REQUEST_LIMIT: int = max(
    MAX_AGENT_ITERATIONS_MONOLITH + 20, 100,
)

# Hard wall-clock for one monolith run. The soft warning fires at 10 min
# via a `pipeline_stage` SSE event so operators can see the run is
# approaching the ceiling.
MONOLITH_WALLCLOCK_SECONDS: float = 15 * 60.0
MONOLITH_WALLCLOCK_WARNING_SECONDS: float = 10 * 60.0

# Per-turn timeout — mirrors the split-pipeline FACE_TURN_TIMEOUT so a
# stalled provider call doesn't pin the coordinator until the iteration
# cap fires. Same threshold; same failure mode.
MONOLITH_TURN_TIMEOUT: float = 180.0

# Maximum bytes of cached-prefix system prompt. Derived from slice-0a
# Windows proxy probe in production; defaulted here so the renderer can
# still apply a ceiling on dev. Operators can override via env. (PRD §8 +
# §10 edge case #16).
MONOLITH_PROMPT_BYTE_CEILING: int = int(
    os.environ.get("XBRL_MONOLITH_PROMPT_BYTE_CEILING", str(200 * 1024))
)


def validate_monolith_compatibility(
    *,
    filing_standard: str,
    filing_level: str,
    statement_values: set[str],
    notes_values: set[str],
) -> list[str]:
    """Single source of truth for monolith scope eligibility.

    Returns a list of human-readable scope violations; empty list means
    every constraint passes. Callers (server, CLI, frontend) format their
    own surrounding messages — the validator returns only the per-rule
    reasons so the three call sites can't drift.

    Scope rules (PRD §3 / docs/PLAN-monolith-face-experiment.md):
      - filing_standard must be 'mfrs'
      - filing_level must be 'company'
      - no notes templates
      - all 5 face statements selected
    """
    problems: list[str] = []
    if filing_standard != "mfrs":
        problems.append(
            f"filing_standard must be 'mfrs' for monolith "
            f"(got {filing_standard!r})"
        )
    if filing_level != "company":
        problems.append(
            f"filing_level must be 'company' for monolith "
            f"(got {filing_level!r})"
        )
    if notes_values:
        problems.append(
            f"notes templates not supported on monolith "
            f"(got {sorted(notes_values)})"
        )
    required = {"SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"}
    missing = sorted(required - set(statement_values))
    if missing:
        problems.append(
            f"all 5 face statements required on monolith; missing: {missing}"
        )
    return problems
