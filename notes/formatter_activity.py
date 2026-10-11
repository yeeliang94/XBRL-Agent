"""Activity of one notes-formatter sub-agent (a whole sheet or one row group).

The formatter fans out to one agent per sheet, or per group of tables on a
large sheet. Each sub-agent reports its own steps so Activity can show them
side by side, the way the Sheet-12 notes sub-agents are shown:

* live: ``on_event(event_type, data)`` pushes the event to the run's shared
  event queue (server.py wraps it with ``agent_id="notes-formatting"``);
* saved: the same events are appended to the sub-agent's own ``run_agents``
  row when it finishes, so History can replay them per sub-agent.

Every payload carries ``sub_agent_id`` (equal to the ``run_agents.variant``
of the same pass) and ``sheet``. Reporting is advisory: a failing callback or
database write never fails a formatting pass.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from itertools import count
from typing import Any, Callable

from pydantic_ai.messages import BinaryContent

logger = logging.getLogger(__name__)

# Same bound as agent_runner's tool_result summaries.
_RESULT_SUMMARY_CHARS = 800

EventSink = Callable[[str, dict], None]


def _event_time() -> str:
    """When an event happened. Saved events keep it, because they are written
    only when the pass ends; microseconds keep parallel passes in order."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _result_summary(content: Any) -> str:
    """Readable result text. Page images become a count, never bytes."""
    if isinstance(content, list):
        images = sum(isinstance(item, BinaryContent) for item in content)
        text = " ".join(item for item in content if isinstance(item, str))
        if images:
            text = f"{text} [{images} page image(s)]".strip()
        return text[:_RESULT_SUMMARY_CHARS]
    if isinstance(content, BinaryContent):
        return "[1 page image]"
    return str(content)[:_RESULT_SUMMARY_CHARS] if content else ""


class FormatterActivity:
    """Collects and forwards one formatter sub-agent's activity events."""

    def __init__(self, *, sub_agent_id: str, sheet: str, on_event: EventSink | None = None):
        self.sub_agent_id = sub_agent_id
        self.sheet = sheet
        self._on_event = on_event
        self.events: list[tuple[str, dict, str]] = []
        self._tool_started: dict[str, float] = {}
        self._call_numbers = count(1)

    def emit(self, event_type: str, data: dict) -> None:
        payload = {**data, "sub_agent_id": self.sub_agent_id, "sheet": self.sheet}
        self.events.append((event_type, payload, _event_time()))
        if self._on_event is None:
            return
        try:
            self._on_event(event_type, payload)
        except Exception:  # noqa: BLE001 — display only
            logger.warning("Could not publish formatter activity", exc_info=True)

    def status(self, phase: str, message: str) -> None:
        self.emit("status", {"phase": phase, "message": message})

    # Tools report themselves (rather than a PydanticAI event_stream_handler)
    # so the formatter's model requests keep their non-streaming transport.
    def tool_started(self, tool_name: str, args: dict) -> str:
        call_id = f"{self.sub_agent_id}:tool{next(self._call_numbers)}"
        self._tool_started[call_id] = time.monotonic()
        self.emit("tool_call", {"tool_name": tool_name, "tool_call_id": call_id, "args": args})
        return call_id

    def tool_finished(self, call_id: str, tool_name: str, content: Any) -> None:
        started = self._tool_started.pop(call_id, None)
        self.emit("tool_result", {
            "tool_name": tool_name,
            "tool_call_id": call_id,
            "result_summary": _result_summary(content),
            "duration_ms": int((time.monotonic() - started) * 1000) if started else 0,
        })

    def save(self, db_path: str, run_agent_id: int) -> None:
        """Append the collected events to this sub-agent's run_agents row."""
        if not self.events:
            return
        from db import repository as repo

        try:
            with repo.db_session(db_path) as conn:
                for event_type, payload, ts in self.events:
                    phase = payload.get("phase")
                    repo.log_event(
                        conn, run_agent_id=run_agent_id, event_type=event_type,
                        payload=payload, phase=phase if isinstance(phase, str) else None,
                        ts=ts,
                    )
        except Exception:  # noqa: BLE001 — History detail is advisory
            logger.warning(
                "Could not save formatter activity run_agent=%s", run_agent_id,
                exc_info=True,
            )
