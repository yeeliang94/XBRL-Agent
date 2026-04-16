"""Peer-review I6: SSE recorder must cap per-agent event volume and
truncate oversized payloads, so a runaway Gemini-3 tool loop can't
explode the audit DB."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from db.recorder import (
    SSEEventRecorder,
    _MAX_EVENTS_PER_AGENT,
    _MAX_PAYLOAD_BYTES,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.db"


def _events_for(conn: sqlite3.Connection, run_agent_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT event_type, payload_json FROM agent_events WHERE run_agent_id = ?",
        (run_agent_id,),
    ).fetchall()
    return [{"event_type": r[0], "payload": json.loads(r[1])} for r in rows]


def test_events_are_capped(db_path: Path):
    rec = SSEEventRecorder(db_path, pdf_filename="x.pdf")
    rec.start()
    assert rec.run_agent_id is not None
    # Push well past the cap.
    for i in range(_MAX_EVENTS_PER_AGENT + 50):
        rec.record({"event": "tool_call", "data": {"i": i}})
    conn = sqlite3.connect(str(db_path))
    try:
        rows = _events_for(conn, rec.run_agent_id)
        assert len(rows) == _MAX_EVENTS_PER_AGENT
    finally:
        conn.close()


def test_oversized_payload_is_truncated(db_path: Path):
    rec = SSEEventRecorder(db_path, pdf_filename="x.pdf")
    rec.start()
    assert rec.run_agent_id is not None

    # Build a payload that comfortably exceeds the byte cap.
    big = "x" * (_MAX_PAYLOAD_BYTES * 2)
    rec.record({"event": "tool_result", "data": {"big": big}})

    conn = sqlite3.connect(str(db_path))
    try:
        rows = _events_for(conn, rec.run_agent_id)
        assert len(rows) == 1
        assert rows[0]["payload"].get("_truncated") is True
        assert rows[0]["payload"].get("_original_bytes") is not None
    finally:
        conn.close()


def test_small_payload_passes_through_unchanged(db_path: Path):
    rec = SSEEventRecorder(db_path, pdf_filename="x.pdf")
    rec.start()
    assert rec.run_agent_id is not None
    rec.record({"event": "status", "data": {"phase": "thinking", "ok": True}})
    conn = sqlite3.connect(str(db_path))
    try:
        rows = _events_for(conn, rec.run_agent_id)
        assert rows[0]["payload"] == {"phase": "thinking", "ok": True}
    finally:
        conn.close()
