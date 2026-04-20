"""Shared helpers for persisting agent conversation traces.

Previously duplicated verbatim across `coordinator.py`,
`notes/coordinator.py`, and the scout runner. Keep trace format stable by
owning it in one place.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Threshold above which `data`/`content` byte blobs get elided from the
# written JSON (keeps traces human-readable in the face of image payloads).
_STRIP_THRESHOLD_BYTES = 500

# Single source of truth for the "how many node iterations before we give
# up and assume the agent is stuck" cap. Used by face/notes coordinators
# and scout. Raise here rather than per-module.
MAX_AGENT_ITERATIONS = 50


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
