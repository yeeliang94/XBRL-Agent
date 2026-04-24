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
#
# v2 (frontend-upgrade-history): runs table gained seven lifecycle columns so
# the History page can surface every run — including failed / aborted ones —
# and download its merged workbook from `run_id` alone without guessing
# filesystem paths.
# v3 (notes-rich-editor): adds the `notes_cells` table that holds canonical
# HTML per notes row. Excel downloads flatten HTML at write time; the
# post-run editor edits the HTML in place. Additive-only — rollback is a
# code revert; the stray table is harmless (no FK points at it from any
# legacy reader).
CURRENT_SCHEMA_VERSION = 3


# Every CREATE is guarded with IF NOT EXISTS so init_db is safe to call
# repeatedly. Foreign keys use ON DELETE CASCADE so deleting a run sweeps
# up all dependent rows.
_CREATE_STATEMENTS: tuple[str, ...] = (
    # Top-level run: one per user-initiated extraction (web UI or CLI).
    #
    # v2 fields (see CURRENT_SCHEMA_VERSION note above):
    #   session_id            — output directory name; the single source of
    #                            truth that ties a DB row to its on-disk files.
    #   output_dir            — absolute path to the session output folder.
    #   merged_workbook_path  — absolute path to the final filled.xlsx, set
    #                            only after a successful merge. Nullable so
    #                            failed runs can still be listed.
    #   run_config_json       — raw RunConfigRequest body; display-only. It is
    #                            NEVER authoritative for per-agent model
    #                            attribution — use run_agents.model for that.
    #   scout_enabled         — whether scout was run before extraction.
    #   started_at / ended_at — explicit lifecycle timestamps so History can
    #                            show wall-clock duration without re-reading
    #                            the last run_agent row.
    #
    # `status` now accepts the enum {running, completed, completed_with_errors,
    # failed, aborted}. No CHECK constraint: a future enum addition would
    # otherwise require a full-table migration.
    """
    CREATE TABLE IF NOT EXISTS runs (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at            TEXT NOT NULL,           -- ISO 8601 UTC
        pdf_filename          TEXT NOT NULL,
        status                TEXT NOT NULL,           -- 'running' | 'completed' | 'completed_with_errors' | 'failed' | 'aborted'
        notes                 TEXT,
        session_id            TEXT NOT NULL DEFAULT '',
        output_dir            TEXT NOT NULL DEFAULT '',
        merged_workbook_path  TEXT,
        run_config_json       TEXT,
        scout_enabled         INTEGER NOT NULL DEFAULT 0,
        started_at            TEXT NOT NULL DEFAULT '',
        ended_at              TEXT
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

    # v3: canonical per-run store for notes HTML payloads. Every notes
    # agent write lands here; the Excel download path flattens HTML to
    # plaintext on demand, and the post-run editor reads/writes HTML
    # directly against this table. UNIQUE(run_id, sheet, row) is the
    # upsert key — one row per template cell per run.
    """
    CREATE TABLE IF NOT EXISTS notes_cells (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        sheet         TEXT NOT NULL,
        row           INTEGER NOT NULL,
        label         TEXT NOT NULL,
        html          TEXT NOT NULL,
        evidence      TEXT,
        source_pages  TEXT,
        updated_at    TEXT NOT NULL,
        UNIQUE(run_id, sheet, row)
    )
    """,
)


# Indexes on foreign-key columns so per-run queries stay cheap.
# ix_runs_created_at supports the History list's default DESC sort.
_CREATE_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_run_agents_run_id ON run_agents(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_agent_events_run_agent_id ON agent_events(run_agent_id)",
    "CREATE INDEX IF NOT EXISTS ix_extracted_fields_run_agent_id ON extracted_fields(run_agent_id)",
    "CREATE INDEX IF NOT EXISTS ix_cross_checks_run_id ON cross_checks(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_runs_created_at ON runs(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_notes_cells_run_id ON notes_cells(run_id)",
)


# v2 columns that need to be added via ALTER TABLE when migrating an
# existing v1 database. SQLite ALTER TABLE cannot add a NOT NULL column
# without a default, so every entry here either is nullable or carries a
# safe default. Order matters: later migrations may read earlier columns.
_V2_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("session_id",            "TEXT NOT NULL DEFAULT ''"),
    ("output_dir",             "TEXT NOT NULL DEFAULT ''"),
    ("merged_workbook_path",   "TEXT"),
    ("run_config_json",        "TEXT"),
    ("scout_enabled",          "INTEGER NOT NULL DEFAULT 0"),
    ("started_at",             "TEXT NOT NULL DEFAULT ''"),
    ("ended_at",               "TEXT"),
)


def init_db(path: str | Path) -> None:
    """Create (or open) the SQLite database and ensure all tables exist.

    Idempotent: running init_db twice leaves the database in the same state.
    Called on server startup; safe to call from tests against a fresh file.

    Concurrency (peer-review C7): when two processes start simultaneously
    against a v1 DB, both could read version<2 and both try to ALTER TABLE
    the same column. We serialize the migration with `BEGIN IMMEDIATE`
    (acquires the write lock up front), re-check the version inside the
    transaction, and tolerate `duplicate column` errors as idempotent
    success (a racer beat us to the column). `busy_timeout` keeps the
    second starter waiting briefly instead of failing instantly.
    """
    p = Path(path)
    # Make sure the parent directory exists so callers can pass paths like
    # ``output/xbrl_agent.db`` without pre-creating the folder themselves.
    p.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(p))
    try:
        # Foreign keys are off by default in SQLite — turn them on per-conn.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        for sql in _CREATE_STATEMENTS:
            conn.execute(sql)

        # Figure out the current schema version BEFORE running index/migration
        # logic, so we know whether this is a fresh DB or an older one that
        # needs to be walked forward.
        cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
        existing = cur.fetchone()
        current_version = int(existing[0]) if existing is not None else None

        # Migrate v1 → v2 inside an IMMEDIATE write transaction. Two
        # concurrent init_db calls against a v1 DB will serialize here —
        # the loser re-reads schema_version after the winner commits and
        # finds v2, skipping the ALTER loop entirely.
        #
        # Each per-version block advances schema_version by exactly ONE
        # step (peer-review I-1). A block that jumps to
        # CURRENT_SCHEMA_VERSION would cause subsequent per-version blocks
        # to short-circuit on a multi-step walk (e.g. v1 → future-v4 would
        # skip the v2→v3 and v3→v4 bodies). Today both v2→v3 and (when it
        # exists) v3→v4 are additive so the jump is harmless, but the
        # discipline keeps every block runnable independently.
        if current_version is not None and current_version < 2:
            try:
                conn.execute("BEGIN IMMEDIATE")
                # Re-check inside the tx — the racer may have migrated while
                # we waited on the busy_timeout.
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 2:
                    existing_cols = {
                        r[1]
                        for r in conn.execute("PRAGMA table_info(runs)").fetchall()
                    }
                    for col_name, col_ddl in _V2_MIGRATION_COLUMNS:
                        if col_name not in existing_cols:
                            try:
                                conn.execute(
                                    f"ALTER TABLE runs ADD COLUMN {col_name} {col_ddl}"
                                )
                            except sqlite3.OperationalError as exc:
                                # "duplicate column name" — a racing process
                                # added it in between our PRAGMA and ALTER.
                                # Idempotent success.
                                if "duplicate column" not in str(exc).lower():
                                    raise
                    conn.execute(
                        "UPDATE runs SET started_at = created_at "
                        "WHERE (started_at IS NULL OR started_at = '')"
                    )
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (2,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            # Re-read so the next per-version block sees the advanced marker.
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            existing = cur.fetchone()
            current_version = int(existing[0]) if existing is not None else None

        # v2 → v3: the table was already created above via
        # CREATE TABLE IF NOT EXISTS; we only need to walk the version
        # marker forward. Serialised with BEGIN IMMEDIATE so two
        # concurrent starters don't race on the schema_version UPDATE.
        if current_version is not None and current_version < 3:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                ).fetchone()
                latest = int(row[0]) if row else None
                if latest is not None and latest < 3:
                    conn.execute(
                        "UPDATE schema_version SET version = ?",
                        (3,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        for sql in _CREATE_INDEXES:
            conn.execute(sql)

        # Record the current schema version if not already set (fresh DB).
        if current_version is None:
            conn.execute(
                "INSERT INTO schema_version(version) VALUES (?)",
                (CURRENT_SCHEMA_VERSION,),
            )
        conn.commit()
    finally:
        conn.close()
