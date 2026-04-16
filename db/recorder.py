"""SSE → SQLite recorder.

Why: the web pipeline already emits a stream of SSE events. The rollout plan
asks every event to also land in the audit DB without teaching agents about
SQL. This recorder is a thin shim the server wraps around an event stream:
it opens one run + one run_agent row on first use, persists coarse events,
and closes the run/agent on stream end (or on error).

Per the plan: "Event store granularity = coarse (tool calls, status, tokens,
errors + full conversation_trace.json as blob)." Thinking deltas and text
deltas are NOT recorded — they're high-frequency, low-value for auditing,
and would cause lock contention once Phase 4 adds concurrent agents.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from db import repository as repo
from db.schema import init_db

logger = logging.getLogger(__name__)

# Only these SSE event types are worth persisting. Everything else (thinking_delta,
# text_delta, token_update) is high-volume streaming noise.
_COARSE_EVENT_TYPES = frozenset({
    "status", "tool_call", "tool_result", "error", "complete",
})

# Hard caps to protect against runaway tool loops (a known Gemini-3 failure
# mode). Without them a single stuck run can write hundreds of thousands of
# rows with multi-KB payloads and blow up the audit DB. See peer-review I6.
_MAX_PAYLOAD_BYTES = 16 * 1024          # 16 KB per event
_MAX_EVENTS_PER_AGENT = 10_000          # stop recording after this many


class SSEEventRecorder:
    """Persists coarse SSE events to SQLite for one agent invocation.

    Uses a single long-lived connection (WAL mode + busy_timeout) rather
    than opening/closing per-event, so it stays fast even at high event rates
    and is safe alongside concurrent agents in Phase 4.

    Usage:

        rec = SSEEventRecorder(db_path, pdf_filename="finco.pdf",
                               statement_type="SOFP", variant="CuNonCu",
                               model="gemini-3-flash")
        rec.start()
        try:
            for evt in stream:
                rec.record(evt)
                yield evt
            rec.finish(status="succeeded")
        except Exception:
            rec.finish(status="failed")
            raise
    """

    def __init__(
        self,
        db_path: str | Path,
        pdf_filename: str,
        statement_type: str = "SOFP",
        variant: str | None = None,
        model: str | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._pdf_filename = pdf_filename
        self._statement_type = statement_type
        self._variant = variant
        self._model = model
        self._run_id: Optional[int] = None
        self._run_agent_id: Optional[int] = None
        self._conn: Optional[sqlite3.Connection] = None
        # If init or record throws once, stop trying — we don't want to spam
        # failures for every single event when the DB is wedged.
        self._disabled = False
        # Per-agent event counter + one-time cap-reached log flag, so
        # runaway loops don't explode the audit DB (peer-review I6).
        self._events_recorded = 0
        self._cap_logged = False

    @property
    def run_id(self) -> Optional[int]:
        return self._run_id

    @property
    def run_agent_id(self) -> Optional[int]:
        return self._run_agent_id

    def start(self) -> None:
        """Ensure DB exists, open a long-lived connection, create run rows."""
        try:
            init_db(self._db_path)
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("PRAGMA foreign_keys = ON")
            # WAL lets readers (the web UI fetching history) and this writer
            # coexist without blocking. busy_timeout prevents "database is
            # locked" when Phase 4 adds concurrent agent recorders.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.row_factory = sqlite3.Row

            self._run_id = repo.create_run(conn, self._pdf_filename)
            self._run_agent_id = repo.create_run_agent(
                conn,
                run_id=self._run_id,
                statement_type=self._statement_type,
                variant=self._variant,
                model=self._model,
            )
            conn.commit()
            self._conn = conn
        except Exception as exc:
            logger.warning("SSEEventRecorder disabled: failed to start: %s", exc)
            self._disabled = True

    def record(self, evt: dict[str, Any]) -> None:
        """Append a coarse SSE event. Skips high-frequency deltas.

        Runaway protection (peer-review I6):
          * Stop recording after `_MAX_EVENTS_PER_AGENT` rows for this agent.
          * Truncate payloads larger than `_MAX_PAYLOAD_BYTES`, inserting a
            `_truncated: true` marker so UI replay can show the excision.
        """
        if self._disabled or self._conn is None or self._run_agent_id is None:
            return

        event_type = str(evt.get("event", ""))
        if event_type not in _COARSE_EVENT_TYPES:
            return

        if self._events_recorded >= _MAX_EVENTS_PER_AGENT:
            if not self._cap_logged:
                logger.warning(
                    "SSEEventRecorder: event cap %d reached for run_agent %s — "
                    "dropping further events",
                    _MAX_EVENTS_PER_AGENT, self._run_agent_id,
                )
                self._cap_logged = True
            return

        try:
            data = evt.get("data") or {}
            if not isinstance(data, dict):
                data = {"value": data}

            # Rough size guard: serialize, check, replace with a small
            # marker + the event type if it blows the cap. We don't try to
            # preserve partial payloads — downstream UIs already handle
            # "_truncated" explicitly.
            import json as _json
            serialized = _json.dumps(data)
            if len(serialized.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
                data = {
                    "_truncated": True,
                    "_original_bytes": len(serialized.encode("utf-8")),
                    "event": event_type,
                }

            phase = data.get("phase")
            repo.log_event(
                self._conn,
                run_agent_id=self._run_agent_id,
                event_type=event_type,
                payload=data,
                phase=phase if isinstance(phase, str) else None,
            )
            self._conn.commit()
            self._events_recorded += 1
        except Exception as exc:
            logger.warning("SSEEventRecorder: record() failed, disabling: %s", exc)
            self._disabled = True

    def finish(
        self,
        status: str,
        workbook_path: str | None = None,
        total_tokens: int = 0,
        total_cost: float = 0.0,
    ) -> None:
        """Close out the run_agent + run rows. Best-effort."""
        if self._disabled or self._conn is None or self._run_agent_id is None or self._run_id is None:
            return
        try:
            repo.finish_run_agent(
                self._conn,
                run_agent_id=self._run_agent_id,
                status=status,
                workbook_path=workbook_path,
                total_tokens=total_tokens,
                total_cost=total_cost,
            )
            # In single-agent mode the run's status tracks the agent's.
            # With multiple agents (Phase 4+) the coordinator will set
            # the run status itself after aggregating results.
            run_status = "completed" if status == "succeeded" else "failed"
            repo.update_run_status(self._conn, self._run_id, run_status)
            self._conn.commit()
        except Exception as exc:
            logger.warning("SSEEventRecorder: finish() failed: %s", exc)
        finally:
            self._close()

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
