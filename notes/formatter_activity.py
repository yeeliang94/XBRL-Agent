"""Activity of one notes-formatter sub-agent (a whole sheet or one row group).

The formatter fans out to one agent per sheet, or per group of tables on a
large sheet. Each sub-agent reports its own steps so Activity can show them
side by side, the way the Sheet-12 notes sub-agents are shown:

* live: ``on_event(event_type, data)`` pushes the event to the run's shared
  event queue (server.py wraps it with ``agent_id="notes-formatting"``);
* saved: each event is appended to the sub-agent's own ``run_agents`` row
  while it runs, so History polling can follow active work too.

Every payload carries ``sub_agent_id`` (equal to the ``run_agents.variant``
of the same pass) and ``sheet``. Reporting is advisory: a failing callback or
database write never fails a formatting pass.
"""
from __future__ import annotations

import asyncio
import logging
import time
import threading
from datetime import datetime, timezone
from itertools import count
from typing import Any, Callable

from pydantic_ai.messages import BinaryContent

logger = logging.getLogger(__name__)

# Same bound as agent_runner's tool_result summaries.
_RESULT_SUMMARY_CHARS = 800

EventSink = Callable[[str, dict], None]


def queue_event_sink(queue: "asyncio.Queue", agent_id: str, agent_role: str) -> EventSink:
    """An ``on_event`` that feeds the run's live event queue from any thread.

    Formatter tools such as ``view_pdf_pages`` are synchronous, so PydanticAI
    runs them on worker threads; ``asyncio.Queue`` is not thread-safe, so
    every event is handed to the queue's event loop. Must be created on that
    loop.
    """
    from agent_runner import build_agent_event

    loop = asyncio.get_running_loop()

    def sink(event_type: str, data: dict) -> None:
        event = build_agent_event(event_type, agent_id, agent_role, data)

        def put() -> None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("event_queue full; formatter %s dropped", event_type)

        loop.call_soon_threadsafe(put)
    return sink


def _event_time() -> str:
    """Keep occurrence time across flush retries and parallel worker threads."""
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


# Step changes save at once so History sees them; tool steps between them are
# batched, keeping per-event database writes off the formatter's hot path.
_SAVE_INTERVAL_S = 2.0


class FormatterActivity:
    """Collects and forwards one formatter sub-agent's activity events."""

    def __init__(self, *, sub_agent_id: str, sheet: str, on_event: EventSink | None = None,
                 db_path: str | None = None, run_agent_id: int | None = None):
        self.sub_agent_id = sub_agent_id
        self.sheet = sheet
        self._on_event = on_event
        self.events: list[tuple[str, dict, str]] = []
        self._tool_started: dict[str, float] = {}
        self._call_numbers = count(1)
        self.db_path = db_path
        self.run_agent_id = run_agent_id
        self._saved_count = 0
        self._last_save = 0.0
        self._finished = False
        self._finish_fields: dict[str, Any] | None = None
        # Tools can emit from worker threads while the agent emits on its loop.
        self._lock = threading.RLock()

    def emit(self, event_type: str, data: dict) -> None:
        payload = {**data, "sub_agent_id": self.sub_agent_id, "sheet": self.sheet}
        with self._lock:
            self.events.append((event_type, payload, _event_time()))
            due = (event_type == "status"
                   or time.monotonic() - self._last_save >= _SAVE_INTERVAL_S)
            if due and self.db_path is not None and self.run_agent_id is not None:
                self.save(self.db_path, self.run_agent_id)
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
        """Flush only unsaved events; failed writes remain available to retry."""
        from db import repository as repo

        with self._lock:
            pending = self.events[self._saved_count:]
            if not pending:
                return
            try:
                with repo.db_session(db_path) as conn:
                    for event_type, payload, ts in pending:
                        phase = payload.get("phase")
                        repo.log_event(
                            conn, run_agent_id=run_agent_id, event_type=event_type,
                            payload=payload, phase=phase if isinstance(phase, str) else None,
                            ts=ts,
                        )
                self._saved_count += len(pending)
                self._last_save = time.monotonic()
            except Exception:  # noqa: BLE001 — History detail is advisory
                logger.warning(
                    "Could not save formatter activity run_agent=%s", run_agent_id,
                    exc_info=True,
                )

    def finish(self, status: str, outcome: dict | None = None, **usage: Any) -> bool:
        """Close this part once: final step, remaining steps, audit row.

        The formatter closes its own part with usage; the caller that queued
        it calls this too, which closes a part the formatter never entered.
        The first outcome and usage win. Failed audit writes may be retried
        without repeating the terminal event. Returns whether this call
        persisted completion (or closed a part with no audit row).
        """
        with self._lock:
            if self._finished:
                return False
            if self._finish_fields is None:
                outcome = outcome or {}
                self._finish_fields = {
                    "status": status, "error_type": outcome.get("error_type"),
                    "error_message": outcome.get("error"), **usage,
                }
                if status == "completed":
                    self.status("completed", "Formatting finished")
                elif status == "cancelled":
                    self.status("cancelled", "Formatting stopped")
                else:
                    from notes.formatting_agent import plain_failure
                    # The plain sentence leads; the raw finding stays as technical detail.
                    self.emit("status", {
                        "phase": "failed", "message": plain_failure(outcome),
                        "error_type": outcome.get("error_type"), "detail": outcome.get("error") or "",
                    })
            if self.db_path is not None and self.run_agent_id is not None:
                from db import repository as repo

                self.save(self.db_path, self.run_agent_id)
                try:
                    with repo.db_session(self.db_path) as conn:
                        repo.finish_run_agent(conn, self.run_agent_id, **self._finish_fields)
                except Exception:  # noqa: BLE001 — audit never replaces the outcome
                    logger.warning("Could not close formatter part run_agent=%s", self.run_agent_id,
                                   exc_info=True)
                    return False
            self._finished = True
            return True
