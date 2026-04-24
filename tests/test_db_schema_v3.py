"""Tests for schema v3 — `notes_cells` table backing the rich-editor payloads.

Step 2 of docs/PLAN-NOTES-RICH-EDITOR.md. Adds a durable per-run notes-cell
store so the post-run editor has a canonical payload to read/write and the
Excel download path can regenerate sheets from JSON rather than patching
the original xlsx in place.

The v3 migration is additive only — no existing column or table is
touched — so rollback is trivial.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(db_path: Path, table: str) -> dict[str, dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {
            r[1]: {"type": r[2], "notnull": bool(r[3]), "default": r[4]}
            for r in rows
        }
    finally:
        conn.close()


def test_v2_to_v3_creates_notes_cells_table(tmp_path: Path) -> None:
    """Simulate an existing v2 database and confirm v3 migration adds the
    `notes_cells` table without touching prior data."""
    db = tmp_path / "xbrl.db"

    # Hand-create a v2-shaped DB: create a minimal runs table and pin
    # schema_version at 2 so init_db has to walk v2→v3 forward.
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                pdf_filename TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                session_id TEXT NOT NULL DEFAULT '',
                output_dir TEXT NOT NULL DEFAULT '',
                merged_workbook_path TEXT,
                run_config_json TEXT,
                scout_enabled INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL DEFAULT '',
                ended_at TEXT
            )"""
        )
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_version(version) VALUES (2)")
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-04-10T00:00:00Z", "legacy.pdf", "completed",
             "2026-04-10T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)

    # Table exists with the expected columns.
    cols = _columns(db, "notes_cells")
    for name in ("id", "run_id", "sheet", "row", "label", "html",
                 "evidence", "source_pages", "updated_at"):
        assert name in cols, f"notes_cells.{name} missing after migration"

    # Legacy row survives untouched.
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT pdf_filename, status FROM runs WHERE pdf_filename = 'legacy.pdf'"
        ).fetchone()
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
    finally:
        conn.close()
    assert row == ("legacy.pdf", "completed")
    assert version == CURRENT_SCHEMA_VERSION
    assert version >= 3


def test_v3_init_is_idempotent(tmp_path: Path) -> None:
    """Repeat `init_db` on a v3 DB leaves everything unchanged."""
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
    assert version == CURRENT_SCHEMA_VERSION
    assert count == 1


def test_notes_cells_unique_on_run_sheet_row(tmp_path: Path) -> None:
    """UNIQUE(run_id, sheet, row) prevents duplicates — upserts rely on it."""
    db = tmp_path / "xbrl.db"
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        # Insert a parent run so FK is satisfied.
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-04-10T00:00:00Z", "x.pdf", "running",
             "2026-04-10T00:00:00Z"),
        )
        run_id = cur.lastrowid

        conn.execute(
            "INSERT INTO notes_cells(run_id, sheet, row, label, html, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, "Notes-CI", 4, "Corporate info",
             "<p>First</p>", "2026-04-10T00:00:01Z"),
        )
        conn.commit()

        dup_raised = False
        try:
            conn.execute(
                "INSERT INTO notes_cells(run_id, sheet, row, label, html, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, "Notes-CI", 4, "Corporate info",
                 "<p>Second</p>", "2026-04-10T00:00:02Z"),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            dup_raised = True
        assert dup_raised, "UNIQUE(run_id, sheet, row) not enforced"
    finally:
        conn.close()


def test_v1_to_v3_migration_walks_through_each_block(tmp_path: Path) -> None:
    """Peer-review I-1: the v1→v2 block must write literal version `2`,
    not `CURRENT_SCHEMA_VERSION`, so the v2→v3 block is actually entered
    on a v1 DB. Currently harmless (v3 is additive + CREATE TABLE IF NOT
    EXISTS), but a future v4 with non-additive ALTERs would silently
    skip when a v1 DB comes through.

    Contract: after init_db on a v1 DB, we verify that BOTH the v2 column
    additions (lifecycle fields) AND the v3 notes_cells table are present.
    The specific failure mode (bypassing v2→v3) is latent; this test
    anchors the "walk through each migration" invariant so a refactor
    that re-introduces the jump can be caught.
    """
    db = tmp_path / "xbrl.db"
    conn = sqlite3.connect(str(db))
    try:
        # v1 shape: original runs table (no lifecycle fields) + version=1.
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
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES (?, ?, ?)",
            ("2026-04-10T00:00:00Z", "v1_legacy.pdf", "completed"),
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)

    # v2 migrations ran — lifecycle columns present on runs.
    runs_cols = _columns(db, "runs")
    for name in ("session_id", "output_dir", "merged_workbook_path",
                 "run_config_json", "scout_enabled", "started_at", "ended_at"):
        assert name in runs_cols, (
            f"v1→v2 migration did not add runs.{name} — check "
            f"init_db's migration walk"
        )

    # v3 migrations ran — notes_cells table present.
    notes_cols = _columns(db, "notes_cells")
    assert "run_id" in notes_cols and "html" in notes_cols

    # schema_version walked forward to CURRENT_SCHEMA_VERSION.
    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
    finally:
        conn.close()
    assert version == CURRENT_SCHEMA_VERSION


def test_v1_to_v2_block_does_not_skip_past_intermediate_versions(tmp_path: Path) -> None:
    """Peer-review I-1 follow-up: the v1→v2 migration block must NOT write
    CURRENT_SCHEMA_VERSION directly. If it does, subsequent per-version
    blocks won't re-enter because they read schema_version and see it's
    already at CURRENT.

    We verify this by inspecting the migration source for the anti-pattern
    `UPDATE schema_version SET version = ?,` + `(CURRENT_SCHEMA_VERSION,)`
    inside the v1→v2 guard block. That's a hard-to-test behavioural
    property (the bug is silent until a future v4 lands), so we pin the
    code shape instead.
    """
    import inspect
    from db import schema as _schema
    src = inspect.getsource(_schema.init_db)
    # Find the v1→v2 block boundaries.
    assert "current_version < 2" in src, "v1→v2 guard missing"
    v1_block_start = src.index("current_version < 2")
    # The v2→v3 block follows; bound our search there.
    v2_block_marker = "current_version < 3"
    v1_block_end = src.index(v2_block_marker, v1_block_start)
    v1_block = src[v1_block_start:v1_block_end]
    # Inside the v1→v2 block, the UPDATE must write literal `2`, not
    # `CURRENT_SCHEMA_VERSION`. Any version-advance must hop exactly one
    # step so the next migration block runs.
    assert "CURRENT_SCHEMA_VERSION" not in v1_block, (
        "v1→v2 block writes CURRENT_SCHEMA_VERSION — that skips every "
        "subsequent per-version migration block on a v1 DB. Write the "
        "target version literally (2) and let the next block advance further."
    )


def test_notes_cells_cascades_on_run_delete(tmp_path: Path) -> None:
    """FK ON DELETE CASCADE — deleting a run sweeps its notes_cells rows."""
    db = tmp_path / "xbrl.db"
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-04-10T00:00:00Z", "x.pdf", "running",
             "2026-04-10T00:00:00Z"),
        )
        run_id = cur.lastrowid
        conn.execute(
            "INSERT INTO notes_cells(run_id, sheet, row, label, html, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, "Notes-CI", 4, "L", "<p>X</p>",
             "2026-04-10T00:00:01Z"),
        )
        conn.commit()

        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        (cells_left,) = conn.execute(
            "SELECT COUNT(*) FROM notes_cells"
        ).fetchone()
        assert cells_left == 0
    finally:
        conn.close()
