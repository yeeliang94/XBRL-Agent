"""Shared helpers for persisting agent conversation traces.

Previously duplicated verbatim across `coordinator.py`,
`notes/coordinator.py`, and the scout runner. Keep trace format stable by
owning it in one place.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Threshold above which `data`/`content` byte blobs get elided from the
# written JSON (keeps traces human-readable in the face of image payloads).
_STRIP_THRESHOLD_BYTES = 500

# Single source of truth for the "how many node iterations before we give
# up and assume the agent is stuck" cap. Used by face/notes coordinators
# and scout.
#
# PLAN-stop-and-validation-visibility Phase 0.3 (2026-04-27): the value
# MUST stay strictly below pydantic-ai's silent default
# ``UsageLimits.request_limit=50``. The 2026-04-26 incident was a face
# agent racing that silent cap and losing — pydantic-ai fired
# ``UsageLimitExceeded`` from inside its own request preparation,
# bypassing our coordinator.py iteration-cap path that would have
# emitted a structured "Hit iteration limit" SSE error. We hold a
# 10-turn buffer (40 vs 50) so pydantic-ai's per-iteration request
# overhead can't tip a 49-iteration agent over the silent cap.
#
# Operators who need more headroom can set ``XBRL_MAX_AGENT_ITERATIONS``
# in env. Setting it >= 50 reintroduces the silent-cap race and is
# explicitly documented as risky; pinned by
# tests/test_max_agent_iterations_below_pydantic_cap.py.
def _resolve_max_iterations() -> int:
    # Hard ceiling: pydantic-ai's silent ``UsageLimits.request_limit=50``
    # races our cap. If our value is >= 50, pydantic-ai wins and the
    # user sees ``UsageLimitExceeded`` instead of our structured "Hit
    # iteration limit" message — exactly the 2026-04-26 incident this
    # constant exists to prevent. Clamp the env override to 45 (5-turn
    # buffer absorbs pydantic-ai's per-iteration overhead) and log a
    # loud warning when an operator tried to push it past the safe
    # ceiling. Peer-review fix (2026-04-27).
    _SAFE_CEILING = 45

    raw = os.environ.get("XBRL_MAX_AGENT_ITERATIONS", "")
    if not raw:
        return 40
    try:
        v = int(raw)
    except ValueError:
        logger.warning(
            "XBRL_MAX_AGENT_ITERATIONS=%r is not an int; using default 40", raw,
        )
        return 40
    if v <= 0:
        return 40
    if v > _SAFE_CEILING:
        logger.warning(
            "XBRL_MAX_AGENT_ITERATIONS=%d exceeds safe ceiling of %d "
            "(pydantic-ai's silent request_limit=50). Clamping to %d to "
            "preserve the structured 'Hit iteration limit' surfacing path. "
            "If you genuinely need more headroom, raise this with the "
            "team — there's a deeper fix that involves explicit "
            "UsageLimits config per agent role.",
            v, _SAFE_CEILING, _SAFE_CEILING,
        )
        return _SAFE_CEILING
    return v


MAX_AGENT_ITERATIONS = _resolve_max_iterations()


def strip_binary(obj: Any) -> None:
    """Recursively elide large binary/content payloads from a dict tree in place."""
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            value = obj[key]
            if key in ("data", "content") and isinstance(value, (bytes, str)):
                if len(str(value)) > _STRIP_THRESHOLD_BYTES:
                    obj[key] = f"<{len(str(value))} bytes stripped>"
                    continue
            strip_binary(value)
    elif isinstance(obj, list):
        for item in obj:
            strip_binary(item)


def save_agent_trace(result: Any, output_dir: str, prefix: str) -> None:
    """Dump an agent's `all_messages()` to `{output_dir}/{prefix}_conversation_trace.json`.

    Binary image data is elided. Best-effort — errors are logged but not
    raised, so trace-save failures never mask the underlying run result.
    """
    try:
        messages: list[dict] = []
        for msg in result.all_messages():
            if hasattr(msg, "model_dump"):
                msg_dict = msg.model_dump(mode="json")
            elif dataclasses.is_dataclass(msg):
                msg_dict = dataclasses.asdict(msg)
            else:
                msg_dict = {"raw": str(msg)}
            strip_binary(msg_dict)
            messages.append(msg_dict)

        trace_path = Path(output_dir) / f"{prefix}_conversation_trace.json"
        trace_path.write_text(
            json.dumps({"messages": messages}, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Failed to save trace for %s: %s", prefix, e)
