"""Typed read/write helpers for the audit DB.

Why: the server and coordinator shouldn't write raw SQL. This module is the
only place that knows about SQLite schema details, so we can evolve the
schema in one place later.

All functions take an explicit `sqlite3.Connection` so the caller controls
the connection lifecycle. Use `db_session()` for the usual commit/rollback
dance.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


def _now() -> str:
    """UTC timestamp in ISO-8601 format. One place so every table agrees."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------

@dataclass
class Run:
    id: int
    created_at: str
    pdf_filename: str
    status: str
    notes: Optional[str] = None


@dataclass
class RunAgent:
    id: int
    run_id: int
    statement_type: str
    variant: Optional[str]
    model: Optional[str]
    status: str
    started_at: str
    ended_at: Optional[str] = None
    workbook_path: Optional[str] = None
    total_tokens: int = 0
    total_cost: float = 0.0


@dataclass
class AgentEvent:
    id: int
    run_agent_id: int
    ts: str
    event_type: str
    phase: Optional[str]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedField:
    id: int
    run_agent_id: int
    sheet: str
    field_label: str
    col: int
    value: Optional[float]
    section: Optional[str] = None
    row_num: Optional[int] = None
    evidence: Optional[str] = None


@dataclass
class CrossCheck:
    id: int
    run_id: int
    check_name: str
    status: str
    expected: Optional[float] = None
    actual: Optional[float] = None
    diff: Optional[float] = None
    tolerance: Optional[float] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

@contextmanager
def db_session(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection, turn on FKs, commit on success, rollback on error.

    Callers typically don't open connections themselves — they wrap a block
    of repository calls in this context manager.
    """
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def create_run(conn: sqlite3.Connection, pdf_filename: str, notes: str = "") -> int:
    """Insert a new run row and return its id."""
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, notes) VALUES (?, ?, ?, ?)",
        (_now(), pdf_filename, "running", notes or None),
    )
    return int(cur.lastrowid)


def update_run_status(conn: sqlite3.Connection, run_id: int, status: str) -> None:
    conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))


def create_run_agent(
    conn: sqlite3.Connection,
    run_id: int,
    statement_type: str,
    variant: str | None = None,
    model: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO run_agents(run_id, statement_type, variant, model, status, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, statement_type, variant, model, "running", _now()),
    )
    return int(cur.lastrowid)


def finish_run_agent(
    conn: sqlite3.Connection,
    run_agent_id: int,
    status: str,
    workbook_path: str | None = None,
    total_tokens: int = 0,
    total_cost: float = 0.0,
) -> None:
    conn.execute(
        "UPDATE run_agents SET status = ?, ended_at = ?, workbook_path = ?, "
        "total_tokens = ?, total_cost = ? WHERE id = ?",
        (status, _now(), workbook_path, total_tokens, total_cost, run_agent_id),
    )


def log_event(
    conn: sqlite3.Connection,
    run_agent_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    phase: str | None = None,
) -> int:
    """Append one SSE-equivalent event. Payload is stored as JSON."""
    cur = conn.execute(
        "INSERT INTO agent_events(run_agent_id, ts, event_type, phase, payload_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_agent_id, _now(), event_type, phase, json.dumps(payload or {})),
    )
    return int(cur.lastrowid)


def save_extracted_field(
    conn: sqlite3.Connection,
    run_agent_id: int,
    sheet: str,
    field_label: str,
    col: int,
    value: float | None,
    section: str | None = None,
    row_num: int | None = None,
    evidence: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO extracted_fields(run_agent_id, sheet, field_label, section, col, "
        "row_num, value, evidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_agent_id, sheet, field_label, section, col, row_num, value, evidence),
    )
    return int(cur.lastrowid)


def save_cross_check(
    conn: sqlite3.Connection,
    run_id: int,
    check_name: str,
    status: str,
    expected: float | None = None,
    actual: float | None = None,
    diff: float | None = None,
    tolerance: float | None = None,
    message: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO cross_checks(run_id, check_name, status, expected, actual, diff, "
        "tolerance, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, check_name, status, expected, actual, diff, tolerance, message),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def fetch_run(conn: sqlite3.Connection, run_id: int) -> Optional[Run]:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return Run(
        id=row["id"], created_at=row["created_at"], pdf_filename=row["pdf_filename"],
        status=row["status"], notes=row["notes"],
    )


def fetch_run_agents(conn: sqlite3.Connection, run_id: int) -> list[RunAgent]:
    rows = conn.execute(
        "SELECT * FROM run_agents WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    return [
        RunAgent(
            id=r["id"], run_id=r["run_id"], statement_type=r["statement_type"],
            variant=r["variant"], model=r["model"], status=r["status"],
            started_at=r["started_at"], ended_at=r["ended_at"],
            workbook_path=r["workbook_path"], total_tokens=r["total_tokens"] or 0,
            total_cost=r["total_cost"] or 0.0,
        )
        for r in rows
    ]


def fetch_events(conn: sqlite3.Connection, run_agent_id: int) -> list[AgentEvent]:
    rows = conn.execute(
        "SELECT * FROM agent_events WHERE run_agent_id = ? ORDER BY id", (run_agent_id,)
    ).fetchall()
    return [
        AgentEvent(
            id=r["id"], run_agent_id=r["run_agent_id"], ts=r["ts"],
            event_type=r["event_type"], phase=r["phase"],
            payload=json.loads(r["payload_json"]) if r["payload_json"] else {},
        )
        for r in rows
    ]


def fetch_fields(conn: sqlite3.Connection, run_id: int) -> list[ExtractedField]:
    """All extracted fields across every agent in the run."""
    rows = conn.execute(
        "SELECT f.* FROM extracted_fields f "
        "JOIN run_agents a ON a.id = f.run_agent_id "
        "WHERE a.run_id = ? ORDER BY f.id",
        (run_id,),
    ).fetchall()
    return [
        ExtractedField(
            id=r["id"], run_agent_id=r["run_agent_id"], sheet=r["sheet"],
            field_label=r["field_label"], col=r["col"], value=r["value"],
            section=r["section"], row_num=r["row_num"], evidence=r["evidence"],
        )
        for r in rows
    ]


def fetch_cross_checks(conn: sqlite3.Connection, run_id: int) -> list[CrossCheck]:
    rows = conn.execute(
        "SELECT * FROM cross_checks WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    return [
        CrossCheck(
            id=r["id"], run_id=r["run_id"], check_name=r["check_name"],
            status=r["status"], expected=r["expected"], actual=r["actual"],
            diff=r["diff"], tolerance=r["tolerance"], message=r["message"],
        )
        for r in rows
    ]
