"""Save a run's activity events on a background thread.

The run orchestrator streams events from the server's event loop. Writing
them there made the whole server wait whenever another writer held the
database lock: up to 5 seconds on SQLite, longer on Azure SQL, where every
statement is a network round trip. Events are history, not state, so they are
queued and written in order by one thread with its own connection. A write
that fails is retried; a single lost event no longer stops an agent's history.
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
_STOP = object()


class EventWriter:
    def __init__(self, db_path, *, retry_delay_s: float = 1.0) -> None:
        self._db_path = str(db_path)
        self._retry_delay_s = retry_delay_s
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
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            if conn is not None:
                self._write(conn, *item)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

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
