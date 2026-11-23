"""Save a run's activity events on a background thread.

The run orchestrator streams events from the server's event loop. Writing
them there made the whole server wait whenever another writer held the
database lock, for up to 5 seconds. Events are history, not state, so they are
queued and written in order by one thread with its own connection. Events
that arrive within about a second of each other are saved together in one
transaction.
A write that fails is retried; a single lost event no longer stops an agent's
history.
"""
from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from typing import Any, Callable

from db import engine as db_engine
from db import repository as repo

logger = logging.getLogger(__name__)

_ATTEMPTS = 3
_GROUP_MAX = 200
_STOP = object()


class EventWriter:
    def __init__(self, db_path, *, retry_delay_s: float = 1.0, group_window_s: float = 1.0) -> None:
        self._db_path = str(db_path)
        self._retry_delay_s = retry_delay_s
        self._group_window_s = group_window_s
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="run-event-writer", daemon=True)
        self._thread.start()

    def agent_event(self, *, run_agent_id: int, event_type: str, payload: dict,
                    phase: str | None) -> None:
        self._submit(repo.log_event, dict(run_agent_id=run_agent_id, event_type=event_type,
                                          payload=payload, phase=phase, ts=repo._now()))

    def run_event(self, *, run_id: int, event_type: str, payload: dict, phase: str | None) -> None:
        self._submit(repo.log_run_event, dict(run_id=run_id, event_type=event_type,
                                              payload=payload, phase=phase, ts=repo._now()))

    def close(self, timeout: float = 120.0) -> None:
        """Write everything queued, then stop. Waits up to ``timeout`` seconds."""
        self._queue.put(_STOP)
        self._thread.join(timeout)
        if self._thread.is_alive():
            logger.warning("run event writer still busy after %.0fs; remaining events may be lost", timeout)

    def _submit(self, write: Callable[..., Any], kwargs: dict) -> None:
        self._queue.put((write, kwargs))

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
            group = [self._queue.get()]
            # Wait a moment for more, so a run's steady stream of events costs
            # one save (and one turn at the write lock) per moment, not per
            # event. Closing the writer ends the wait at once.
            deadline = time.monotonic() + self._group_window_s
            while _STOP not in group and len(group) < _GROUP_MAX:
                try:
                    group.append(self._queue.get(timeout=max(0.0, deadline - time.monotonic())))
                except queue.Empty:
                    break
            if _STOP in group:
                stopping = True
                group = group[:group.index(_STOP)]
            if conn is not None and group:
                self._write_group(conn, group)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _write_group(self, conn, group: list) -> None:
        for attempt in range(1, _ATTEMPTS + 1) if len(group) > 1 else ():
            try:
                for write, kwargs in group:
                    write(conn, **kwargs)
                conn.commit()
                return
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                # A busy database: try the whole group again. Anything else:
                # save them one at a time, so one event that cannot be saved
                # does not take the rest.
                if attempt == _ATTEMPTS or not isinstance(exc, sqlite3.OperationalError):
                    break
                time.sleep(self._retry_delay_s * attempt)
        for write, kwargs in group:
            self._write(conn, write, kwargs)

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
