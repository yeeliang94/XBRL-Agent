"""Schema v29 — notes_cells.style_source (per-cell styling provenance:
'ops' | 'floor' | 'unstyled' | NULL). Nullable ALTER TABLE column added so the
operator can see which prose cells rendered plain and want a formatter pass."""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_fresh_init_has_style_source_column(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "style_source" in _columns(conn, "notes_cells")
        # The snapshot table mirrors it so revert restores the tag.
        assert "style_source" in _columns(conn, "run_notes_cell_snapshots")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION >= 29
    finally:
        conn.close()


def test_v28_db_walks_forward_and_adds_column(tmp_path):
    """A DB pinned at v28 (notes_cells without style_source) walks forward to
    v29, gaining the nullable column."""
    db = tmp_path / "v28.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        # Rebuild both style_source-carrying tables without the column to
        # simulate a v28 DB.
        conn.executescript(
            """
            PRAGMA foreign_keys=off;
            CREATE TABLE nc_tmp AS SELECT id, run_id, sheet, row, label, html,
                evidence, source_pages, updated_at, concept_uuid FROM notes_cells;
            DROP TABLE notes_cells;
            CREATE TABLE notes_cells (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                sheet TEXT NOT NULL, row INTEGER NOT NULL, label TEXT NOT NULL,
                html TEXT NOT NULL, evidence TEXT, source_pages TEXT,
                updated_at TEXT NOT NULL, concept_uuid TEXT,
                UNIQUE(run_id, sheet, row)
            );
            CREATE TABLE sn_tmp AS SELECT id, run_id, sheet, row, label, html,
                evidence, source_pages, concept_uuid, snapshot_at
                FROM run_notes_cell_snapshots;
            DROP TABLE run_notes_cell_snapshots;
            CREATE TABLE run_notes_cell_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                sheet TEXT NOT NULL, row INTEGER NOT NULL, label TEXT NOT NULL,
                html TEXT NOT NULL, evidence TEXT, source_pages TEXT,
                concept_uuid TEXT, snapshot_at TEXT NOT NULL,
                UNIQUE(run_id, sheet, row)
            );
            UPDATE schema_version SET version = 28;
            """
        )
        conn.commit()
        assert "style_source" not in _columns(conn, "notes_cells")
        assert "style_source" not in _columns(conn, "run_notes_cell_snapshots")
    finally:
        conn.close()

    init_db(db)  # migrate
    conn = sqlite3.connect(str(db))
    try:
        assert "style_source" in _columns(conn, "notes_cells")
        assert "style_source" in _columns(conn, "run_notes_cell_snapshots")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_init_db_is_idempotent_at_v29(tmp_path):
    db = tmp_path / "twice.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
        assert "style_source" in _columns(conn, "notes_cells")
    finally:
        conn.close()


def test_upsert_records_and_preserves_style_source(tmp_path):
    """upsert stamps style_source; a later write that omits it preserves the
    prior value (same 'don't downgrade' rule as concept_uuid)."""
    from db import repository as repo

    db = tmp_path / "rt.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-CI", row=6, label="L",
            html="<p>a</p>", style_source="unstyled",
        )
        # Reviewer-style edit: omits style_source → must preserve "unstyled".
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-CI", row=6, label="L",
            html="<p>b</p>",
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-CI", row=7, label="M",
            html="<p>c</p>", style_source="ops",
        )
        cells = {c.row: c for c in repo.list_notes_cells_for_run(conn, run_id)}
    assert cells[6].style_source == "unstyled"
    assert cells[6].html == "<p>b</p>"
    assert cells[7].style_source == "ops"
