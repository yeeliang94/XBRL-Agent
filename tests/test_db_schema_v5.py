"""Phase 5 schema v4 → v5 tests — SOCIE matrix variant.

v5 is purely additive:
  * `concept_nodes.matrix_col` — nullable column carrying the equity-
    component column label (e.g. 'C' for Retained earnings) on
    MATRIX_CELL concepts. NULL for every linear LEAF/COMPUTED/ABSTRACT
    concept, so the migration is a single idempotent ALTER TABLE.
  * `MATRIX_CELL` becomes a legal `kind` value (no CHECK constraint, so
    no DDL change beyond the column — pinned here so the importer can
    rely on it).

Migration follows the same `current_version < N` BEGIN IMMEDIATE idiom
as v2/v3/v4 (gotcha #11).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _seed_v4(db: Path) -> None:
    """Build a v4-shaped DB then force schema_version back to 4 and drop
    the new column so the v4→v5 migration has work to do."""
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE schema_version SET version = 4")
        # Recreate concept_nodes WITHOUT matrix_col to simulate a real v4
        # database. SQLite can't DROP COLUMN on old versions, so rebuild.
        conn.execute("DROP TABLE IF EXISTS concept_nodes")
        conn.execute(
            """
            CREATE TABLE concept_nodes (
                concept_uuid     TEXT PRIMARY KEY,
                template_id      TEXT NOT NULL,
                parent_uuid      TEXT,
                kind             TEXT NOT NULL,
                canonical_label  TEXT NOT NULL,
                display_label    TEXT,
                render_sheet     TEXT NOT NULL,
                render_row       INTEGER NOT NULL,
                render_col       TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _cols(db: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def test_current_version_is_at_least_5() -> None:
    assert CURRENT_SCHEMA_VERSION >= 5


def test_v4_database_migrates_to_v5_on_init(tmp_path: Path) -> None:
    db = tmp_path / "xbrl.db"
    _seed_v4(db)
    assert "matrix_col" not in _cols(db, "concept_nodes")

    init_db(db)

    assert "matrix_col" in _cols(db, "concept_nodes"), (
        "matrix_col missing after v4→v5 migration"
    )
    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
    finally:
        conn.close()
    assert version == CURRENT_SCHEMA_VERSION


def test_fresh_init_has_matrix_col(tmp_path: Path) -> None:
    db = tmp_path / "xbrl.db"
    init_db(db)
    assert "matrix_col" in _cols(db, "concept_nodes")


def test_migration_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "xbrl.db"
    _seed_v4(db)
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


def test_matrix_cell_kind_and_matrix_col_insertable(tmp_path: Path) -> None:
    """A MATRIX_CELL node with a non-NULL matrix_col must insert cleanly —
    no CHECK constraint should reject the new kind."""
    db = tmp_path / "xbrl.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, shape) "
            "VALUES (?, ?, ?)",
            ("t-matrix", "socie.xlsx", "matrix"),
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col, matrix_col) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("c-mx", "t-matrix", "MATRIX_CELL", "Profit (loss)", "SOCIE", 11, "B", "C"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT kind, matrix_col FROM concept_nodes WHERE concept_uuid = ?",
            ("c-mx",),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("MATRIX_CELL", "C")
