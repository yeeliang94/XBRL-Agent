"""SQLite schema for the XBRL-agent audit store.

The schema is deliberately small and additive. `init_db` is idempotent so it
can run on every server start without a separate migration step. Real
migrations (if they become necessary) will go through `schema_version`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Schema version written by the current code. Bump this when you add a
# backward-incompatible change and write a migration block that upgrades
# older databases on init.
CURRENT_SCHEMA_VERSION = 1


# Every CREATE is guarded with IF NOT EXISTS so init_db is safe to call
# repeatedly. Foreign keys use ON DELETE CASCADE so deleting a run sweeps
# up all dependent rows.
_CREATE_STATEMENTS: tuple[str, ...] = (
    # Top-level run: one per user-initiated extraction (web UI or CLI).
    """
    CREATE TABLE IF NOT EXISTS runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at      TEXT NOT NULL,           -- ISO 8601 UTC
        pdf_filename    TEXT NOT NULL,
        status          TEXT NOT NULL,           -- 'running' | 'completed' | 'failed'
        notes           TEXT
    )
    """,

    # One row per (run, statement) agent invocation. Scout is also recorded
    # here with statement_type='SCOUT' so all agent activity is uniform.
    """
    CREATE TABLE IF NOT EXISTS run_agents (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        statement_type  TEXT NOT NULL,           -- 'SOFP' | 'SOPL' | ... | 'SCOUT'
        variant         TEXT,                    -- e.g. 'CuNonCu'
        model           TEXT,
        status          TEXT NOT NULL,           -- 'running' | 'succeeded' | 'failed'
        started_at      TEXT NOT NULL,
        ended_at        TEXT,
        workbook_path   TEXT,
        total_tokens    INTEGER DEFAULT 0,
        total_cost      REAL DEFAULT 0
    )
    """,

    # Every SSE event (tool call, thinking, status, error, ...) for auditing.
    """
    CREATE TABLE IF NOT EXISTS agent_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_agent_id    INTEGER NOT NULL REFERENCES run_agents(id) ON DELETE CASCADE,
        ts              TEXT NOT NULL,
        event_type      TEXT NOT NULL,           -- matches SSE 'event' field
        phase           TEXT,
        payload_json    TEXT                     -- the SSE 'data' blob, JSON-encoded
    )
    """,

    # Structured data-entry cells written to each workbook, for downstream
    # cross-checks and UI display without re-reading the xlsx file.
    """
    CREATE TABLE IF NOT EXISTS extracted_fields (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_agent_id    INTEGER NOT NULL REFERENCES run_agents(id) ON DELETE CASCADE,
        sheet           TEXT NOT NULL,
        field_label     TEXT NOT NULL,
        section         TEXT,
        col             INTEGER NOT NULL,
        row_num         INTEGER,
        value           REAL,
        evidence        TEXT
    )
    """,

    # Cross-statement reconciliation results (Phase 5).
    """
    CREATE TABLE IF NOT EXISTS cross_checks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        check_name      TEXT NOT NULL,
        status          TEXT NOT NULL,           -- passed | failed | not_applicable | pending
        expected        REAL,
        actual          REAL,
        diff            REAL,
        tolerance       REAL,
        message         TEXT
    )
    """,

    # Schema metadata — single-row version marker.
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version         INTEGER PRIMARY KEY
    )
    """,
)


# Indexes on foreign-key columns so per-run queries stay cheap.
_CREATE_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_run_agents_run_id ON run_agents(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_agent_events_run_agent_id ON agent_events(run_agent_id)",
    "CREATE INDEX IF NOT EXISTS ix_extracted_fields_run_agent_id ON extracted_fields(run_agent_id)",
    "CREATE INDEX IF NOT EXISTS ix_cross_checks_run_id ON cross_checks(run_id)",
)


def init_db(path: str | Path) -> None:
    """Create (or open) the SQLite database and ensure all tables exist.

    Idempotent: running init_db twice leaves the database in the same state.
    Called on server startup; safe to call from tests against a fresh file.
    """
    p = Path(path)
    # Make sure the parent directory exists so callers can pass paths like
    # ``output/xbrl_agent.db`` without pre-creating the folder themselves.
    p.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(p))
    try:
        # Foreign keys are off by default in SQLite — turn them on per-conn.
        conn.execute("PRAGMA foreign_keys = ON")
        for sql in _CREATE_STATEMENTS:
            conn.execute(sql)
        for sql in _CREATE_INDEXES:
            conn.execute(sql)
        # Record the current schema version if not already set.
        cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO schema_version(version) VALUES (?)",
                (CURRENT_SCHEMA_VERSION,),
            )
        conn.commit()
    finally:
        conn.close()
