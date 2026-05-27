"""Tests for schema v9 — SOCIE matrix component labels.

v9 is additive: `concept_nodes.matrix_col_label` stores the human row-2
SOCIE component header used by the review grid. `matrix_col` remains the
Excel column letter used for routing facts back to the workbook.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _cols(db: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def test_current_version_is_at_least_9() -> None:
    assert CURRENT_SCHEMA_VERSION >= 9


def test_fresh_init_has_matrix_col_label(tmp_path: Path) -> None:
    db = tmp_path / "xbrl.db"
    init_db(db)
    assert "matrix_col_label" in _cols(db, "concept_nodes")


def test_v8_database_migrates_to_v9_on_init(tmp_path: Path) -> None:
    db = tmp_path / "xbrl.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE schema_version SET version = 8")
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
                render_col       TEXT NOT NULL,
                matrix_col       TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    assert "matrix_col_label" not in _cols(db, "concept_nodes")
    init_db(db)
    assert "matrix_col_label" in _cols(db, "concept_nodes")

    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
    finally:
        conn.close()
    assert version == CURRENT_SCHEMA_VERSION
