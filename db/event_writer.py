"""Save a run's activity events on a background thread.

The run orchestrator streams events from the server's event loop. Writing
them there made the whole server wait whenever another writer held the
database lock: up to 5 seconds on SQLite, longer on Azure SQL, where every
statement is a network round trip. Events are history, not state, so they are
queued and written in order by one thread with its own connection. Events
that arrive within about a second of each other are saved together: one
transaction, and on Azure SQL one request that does not wait for the
database-wide write lock (``db.engine.append_records``). Saving an event no
longer updates the run's activity time; the writer refreshes it at most every
``touch_interval_s`` seconds, and once more when it closes.
A write that fails is retried; a single lost event no longer stops an agent's
history.
"""
from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from typing import Any, Callable, NamedTuple

from db import engine as db_engine
from db import repository as repo

logger = logging.getLogger(__name__)

_ATTEMPTS = 3
_GROUP_MAX = 200
_STOP = object()


class _Item(NamedTuple):
    write: Callable[..., Any]
    kwargs: dict
    run_id: int | None
    run_agent_id: int | None


class _Statements:
    """Stands in for a connection to collect the inserts of a group of events."""

    lastrowid = 0  # the save functions return the new row id; nothing here uses it

    def __init__(self) -> None:
        self.collected: list[tuple[str, tuple]] = []

    def execute(self, sql: str, parameters=()) -> "_Statements":
        self.collected.append((sql, tuple(parameters)))
        return self


class EventWriter:
    def __init__(self, db_path, *, retry_delay_s: float = 1.0, group_window_s: float = 1.0,
                 touch_interval_s: float = 30.0) -> None:
        self._db_path = str(db_path)
        self._retry_delay_s = retry_delay_s
        self._group_window_s = group_window_s
        self._touch_interval_s = touch_interval_s
        self._touched_at = time.monotonic()
        self._active_runs: set[int] = set()
        self._active_agents: set[int] = set()
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="run-event-writer", daemon=True)
        self._thread.start()

    def agent_event(self, *, run_agent_id: int, event_type: str, payload: dict,
                    phase: str | None) -> None:
        self._queue.put(_Item(repo.log_event, dict(run_agent_id=run_agent_id, event_type=event_type,
                                                   payload=payload, phase=phase, ts=repo._now()),
                              run_id=None, run_agent_id=run_agent_id))

    def run_event(self, *, run_id: int, event_type: str, payload: dict, phase: str | None) -> None:
        self._queue.put(_Item(repo.log_run_event, dict(run_id=run_id, event_type=event_type,
                                                       payload=payload, phase=phase, ts=repo._now()),
                              run_id=run_id, run_agent_id=None))

    def close(self, timeout: float = 120.0) -> None:
        """Write everything queued, then stop. Waits up to ``timeout`` seconds."""
        self._queue.put(_STOP)
        self._thread.join(timeout)
        if self._thread.is_alive():
            logger.warning("run event writer still busy after %.0fs; remaining events may be lost", timeout)

    def _loop(self) -> None:
        conn = None
        try:
            conn = db_engine.connect(self._db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
        except Exception:
            logger.exception("run event writer could not open the database; events will not be saved")
        stopping = False
        while not stopping:
            # With activity waiting to be recorded, wake when the refresh is
            # due even if no event arrives (a long model call is quiet).
            pending = self._active_runs or self._active_agents
            wait = max(0.0, self._touched_at + self._touch_interval_s - time.monotonic()) if pending else None
            try:
                group = [self._queue.get(timeout=wait)]
            except queue.Empty:
                group = []
            # Wait a moment for more, so a run's steady stream of events costs
            # one save (and one turn at the write lock) per moment, not per
            # event. Closing the writer ends the wait at once.
            deadline = time.monotonic() + self._group_window_s
            while group and _STOP not in group and len(group) < _GROUP_MAX:
                try:
                    group.append(self._queue.get(timeout=max(0.0, deadline - time.monotonic())))
                except queue.Empty:
                    break
            if _STOP in group:
                stopping = True
                group = group[:group.index(_STOP)]
            if conn is not None and group:
                self._write_group(conn, group)
                self._note_activity(group)
            if conn is not None and (stopping or time.monotonic() - self._touched_at >= self._touch_interval_s):
                self._touch_runs(conn)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _write_group(self, conn, group: list) -> None:
        # Run events go separately: each checks its run's row, which a figure
        # or notes save for that run holds until it commits. Agent events,
        # the bulk, then never wait with them.
        for part in ([i for i in group if i.run_id is None], [i for i in group if i.run_id is not None]):
            if part:
                self._append(conn, part)

    def _append(self, conn, items: list) -> None:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                statements = _Statements()
                for item in items:
                    item.write(statements, **item.kwargs)
                db_engine.run_batch(conn, statements.collected, append_only=True)
                conn.commit()
                return
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                # A busy database: try them all again. Anything else: save
                # them one at a time, so one event that cannot be saved does
                # not take the rest.
                if attempt == _ATTEMPTS or not isinstance(exc, sqlite3.OperationalError):
                    break
                time.sleep(self._retry_delay_s * attempt)
        for item in items:
            self._write(conn, item.write, item.kwargs)

    def _write(self, conn, write: Callable[..., Any], kwargs: dict) -> None:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                write(conn, **kwargs)
                conn.commit()
                return
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                # Only a busy database is worth waiting for; anything else
                # (an event that cannot be saved at all) fails the same way again.
                if attempt == _ATTEMPTS or not isinstance(exc, sqlite3.OperationalError):
                    logger.warning("could not save %s event after %d attempts", kwargs.get("event_type"),
                                   _ATTEMPTS, exc_info=True)
                    return
                time.sleep(self._retry_delay_s * attempt)

    def _note_activity(self, group: list) -> None:
        for item in group:
            if item.run_id is not None:
                self._active_runs.add(item.run_id)
            else:
                self._active_agents.add(item.run_agent_id)

    def _touch_runs(self, conn) -> None:
        """Move the activity time of every run with new events to now.

        One short write per interval in place of one per event.
        """
        self._touched_at = time.monotonic()
        if not self._active_runs and not self._active_agents:
            return
        try:
            repo.touch_runs(conn, self._active_runs, self._active_agents)
            conn.commit()
            self._active_runs.clear()
            self._active_agents.clear()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            # The events are saved; only the History sort time is late. Try
            # again at the next interval.
            logger.warning("could not refresh run activity time", exc_info=True)
