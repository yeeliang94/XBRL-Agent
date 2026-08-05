"""Schema v37 — the placement ledger, the remediation task, and the two
`notes_cells` columns that close peer review's Critical and High findings.

Each table here exists because a specific false green was reproduced:

* `notes_block_placements` — a block relinked out of a cell, or left in a
  cell that was then deleted, stayed recorded as `included` at that cell and
  the run verified clean;
* `content_revision` — the optimistic version check keyed on a one-second
  timestamp, so two saves in the same second shared a token;
* `source_render_version` — the version used to be folded into the content
  hash, so a cell edited back to its source text could never clear divergence.
"""
from __future__ import annotations

import sqlite3

import pytest

from db import repository as repo
from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_current_version_is_at_least_37():
    # v38 (mtool_fill_receipts) landed after this step; the walk-forward
    # behaviour this file pins is unaffected by later versions.
    assert CURRENT_SCHEMA_VERSION >= 37


def test_a_fresh_database_has_everything(tmp_path):
    db = tmp_path / "fresh.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        assert "notes_block_placements" in {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "notes_integrity_tasks" in {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        cols = _columns(conn, "notes_cells")
        assert {"content_revision", "source_render_version"} <= cols
    finally:
        conn.close()


def test_a_real_v36_database_migrates_forward(tmp_path):
    """Starts from a genuine v36 shape — the columns are REMOVED and the new
    tables dropped, so the migration is the only thing that can restore them."""
    db = tmp_path / "v36.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        for t in ("notes_block_placements", "notes_integrity_tasks"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        for c in ("content_revision", "source_render_version"):
            try:
                conn.execute(f"ALTER TABLE notes_cells DROP COLUMN {c}")
            except sqlite3.OperationalError as exc:  # pragma: no cover
                pytest.skip(f"SQLite too old for DROP COLUMN: {exc}")
        conn.execute("UPDATE schema_version SET version = 36")
        conn.commit()
        assert "content_revision" not in _columns(conn, "notes_cells")
    finally:
        conn.close()

    init_db(db)

    conn = sqlite3.connect(db)
    try:
        assert {"content_revision", "source_render_version"} <= _columns(
            conn, "notes_cells"
        )
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"notes_block_placements", "notes_integrity_tasks"} <= names
        assert conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0] == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "twice.sqlite"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(notes_cells)")]
        assert cols.count("content_revision") == 1
    finally:
        conn.close()


def test_one_block_can_be_placed_in_two_cells(tmp_path):
    """`notes_block_usages` is UNIQUE per block, which made a duplicate
    unrepresentable and the duplicate check structurally dead."""
    db = tmp_path / "dup.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
        for sheet, row in (("Notes", 10), ("Policies", 4)):
            conn.execute(
                "INSERT INTO notes_block_placements("
                "  run_id, generation_id, block_id, sheet, row, active,"
                "  created_at) VALUES (?, 1, 'b1', ?, ?, 1, '')",
                (run_id, sheet, row),
            )
        assert conn.execute(
            "SELECT COUNT(*) FROM notes_block_placements WHERE block_id = 'b1'"
        ).fetchone()[0] == 2


def test_the_revision_starts_at_one_and_increments(tmp_path):
    db = tmp_path / "rev.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=10, label="L", html="a"
        )
        first = conn.execute(
            "SELECT content_revision FROM notes_cells WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=10, label="L", html="b"
        )
        second = conn.execute(
            "SELECT content_revision FROM notes_cells WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    assert first == 1
    assert second == 2, "two saves in the same second must differ"
