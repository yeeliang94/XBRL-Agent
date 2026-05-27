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

# v8 (docs/PLAN-run-page-and-telemetry.md): the run-page Telemetry feature
# serves these traces so the user can read the exact request/response per
# agent. That requires keeping TEXT content verbatim — unlike the legacy
# 500-byte elision which hid tool results and prompts. We still strip true
# binary (image bytes) and cap any single oversized string at 100 KB so a
# pathological payload can't bloat the trace without bound (full-verbatim
# decision with a per-cell cap).
_MAX_TRACE_STR_CHARS = 100_000


def _sanitize_for_trace(obj: Any) -> None:
    """Recursively make a message-dict tree safe + bounded for trace JSON.

    - Raw `bytes` anywhere are replaced with a size marker (never human
      readable, usually image payloads).
    - Any string longer than `_MAX_TRACE_STR_CHARS` is truncated with a
      marker so the verbatim text stays useful without growing unbounded.
    Text content is otherwise preserved so the trace shows exactly what was
    sent and returned.
    """
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            value = obj[key]
            if isinstance(value, bytes):
                obj[key] = f"<{len(value)} bytes stripped>"
            elif isinstance(value, str) and len(value) > _MAX_TRACE_STR_CHARS:
                obj[key] = (
                    value[:_MAX_TRACE_STR_CHARS]
                    + f"...[truncated {len(value) - _MAX_TRACE_STR_CHARS} chars]"
                )
            else:
                _sanitize_for_trace(value)
    elif isinstance(obj, list):
        for item in obj:
            _sanitize_for_trace(item)

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


def save_agent_trace(
    result: Any,
    output_dir: str,
    prefix: str,
    turns: list[dict] | None = None,
) -> None:
    """Dump an agent's `all_messages()` to `{output_dir}/{prefix}_conversation_trace.json`.

    Text content is preserved verbatim (capped per cell) so the trace shows
    exactly what was sent and returned each turn; true binary is elided. When
    `turns` is supplied (v8 per-turn metrics), it is written alongside the
    messages so a reader can line up token deltas + timing with the
    conversation. Best-effort — errors are logged but not raised, so
    trace-save failures never mask the underlying run result.
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
            _sanitize_for_trace(msg_dict)
            messages.append(msg_dict)

        payload: dict[str, Any] = {"messages": messages}
        if turns is not None:
            # Strip the coordinator-internal `_n_tool_calls` helper key so the
            # trace carries only the user-meaningful per-turn metrics.
            payload["turns"] = [
                {k: v for k, v in t.items() if not k.startswith("_")}
                for t in turns
            ]

        trace_path = Path(output_dir) / f"{prefix}_conversation_trace.json"
        trace_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Failed to save trace for %s: %s", prefix, e)
