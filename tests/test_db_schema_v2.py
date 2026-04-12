"""Tests for schema v2 — run lifecycle columns on the `runs` table.

Phase 1, Step 1.1 of the frontend-upgrade-history plan. These assertions
pin down the exact shape History and RunDetail depend on: every run — even
failed / aborted / disconnected ones — has a durable locator for its
output directory and merged workbook path.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(db_path: Path, table: str) -> dict[str, dict]:
    """Return {column_name: {notnull, default_value}} from PRAGMA table_info."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        return {
            r[1]: {"type": r[2], "notnull": bool(r[3]), "default": r[4]}
            for r in rows
        }
    finally:
        conn.close()


def test_runs_table_has_new_v2_columns(tmp_path: Path) -> None:
    """Fresh DB init should create all seven new lifecycle columns."""
    db = tmp_path / "xbrl.db"
    init_db(db)

    cols = _columns(db, "runs")
    # Each new column must exist. NOT NULL-ness is verified case-by-case
    # because ALTER TABLE on older DBs forces relaxed constraints.
    assert "session_id" in cols, "session_id column missing"
    assert "output_dir" in cols, "output_dir column missing"
    assert "merged_workbook_path" in cols, "merged_workbook_path column missing"
    assert "run_config_json" in cols, "run_config_json column missing"
    assert "scout_enabled" in cols, "scout_enabled column missing"
    assert "started_at" in cols, "started_at column missing"
    assert "ended_at" in cols, "ended_at column missing"

    # merged_workbook_path, run_config_json, ended_at are nullable (failed
    # runs never produce these). session_id / output_dir / started_at are
    # always set by create_run.
    assert cols["merged_workbook_path"]["notnull"] is False
    assert cols["run_config_json"]["notnull"] is False
    assert cols["ended_at"]["notnull"] is False


def test_schema_version_is_v2(tmp_path: Path) -> None:
    db = tmp_path / "xbrl.db"
    init_db(db)
    assert CURRENT_SCHEMA_VERSION == 2

    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
    finally:
        conn.close()
    assert version == 2


def test_aborted_status_is_accepted(tmp_path: Path) -> None:
    """The new 'aborted' status is a documented enum value — no CHECK constraint
    blocks it. This is the lightweight way to introduce it without a migration
    on the CHECK clause itself."""
    db = tmp_path / "xbrl.db"
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
            "output_dir, started_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("2026-04-10T00:00:00Z", "x.pdf", "aborted", "abc", "/tmp/abc", "2026-04-10T00:00:00Z"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT status FROM runs WHERE session_id = 'abc'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "aborted"


def test_migration_from_v1_db_adds_columns(tmp_path: Path) -> None:
    """Simulate an existing v1 database (created before the upgrade) and
    confirm init_db migrates it forward without data loss."""
    db = tmp_path / "xbrl.db"

    # Hand-create a v1-shaped runs table + schema_version row
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                pdf_filename TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT
            )"""
        )
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_version(version) VALUES (1)")
        # Seed a pre-migration row so we can confirm it survives.
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) VALUES (?, ?, ?)",
            ("2026-03-01T00:00:00Z", "legacy.pdf", "completed"),
        )
        conn.commit()
    finally:
        conn.close()

    # Run the migration.
    init_db(db)

    cols = _columns(db, "runs")
    for new_col in (
        "session_id", "output_dir", "merged_workbook_path",
        "run_config_json", "scout_enabled", "started_at", "ended_at",
    ):
        assert new_col in cols, f"{new_col} missing after migration"

    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        legacy = conn.execute(
            "SELECT pdf_filename, status FROM runs WHERE pdf_filename = 'legacy.pdf'"
        ).fetchone()
    finally:
        conn.close()

    assert version == 2
    assert legacy is not None
    assert legacy == ("legacy.pdf", "completed")


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Running init_db on an already-v2 DB is a no-op."""
    db = tmp_path / "xbrl.db"
    init_db(db)
    init_db(db)
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        (count,) = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    finally:
        conn.close()
    assert version == 2
    assert count == 1


def test_runs_created_at_index_exists(tmp_path: Path) -> None:
    """History queries sort by created_at DESC; a supporting index keeps the
    list endpoint cheap even with thousands of rows."""
    db = tmp_path / "xbrl.db"
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='runs'"
        ).fetchall()
        names = {r[0] for r in rows}
    finally:
        conn.close()
    assert any("created_at" in n for n in names), (
        f"Expected a runs.created_at index, got: {names}"
    )
