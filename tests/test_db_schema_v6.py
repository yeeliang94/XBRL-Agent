"""Phase 7 schema v5 → v6 — notes_cells.concept_uuid.

v6 is purely additive: a nullable `concept_uuid` column on notes_cells so
a notes row can be linked to the canonical concept store. NULL preserves
back-compat for every existing notes write (the coordinator path doesn't
set it). Single idempotent ALTER, same idiom as v2/v5.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _seed_v5(db: Path) -> None:
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE schema_version SET version = 5")
        conn.execute("DROP TABLE IF EXISTS notes_cells")
        conn.execute(
            """
            CREATE TABLE notes_cells (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id        INTEGER NOT NULL,
                sheet         TEXT NOT NULL,
                row           INTEGER NOT NULL,
                label         TEXT NOT NULL,
                html          TEXT NOT NULL,
                evidence      TEXT,
                source_pages  TEXT,
                updated_at    TEXT NOT NULL,
                UNIQUE(run_id, sheet, row)
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


def test_current_version_is_at_least_6() -> None:
    assert CURRENT_SCHEMA_VERSION >= 6


def test_v5_database_migrates_to_v6(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    _seed_v5(db)
    assert "concept_uuid" not in _cols(db, "notes_cells")
    init_db(db)
    assert "concept_uuid" in _cols(db, "notes_cells")
    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
    finally:
        conn.close()
    assert version == CURRENT_SCHEMA_VERSION


def test_fresh_init_has_notes_concept_uuid(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    init_db(db)
    assert "concept_uuid" in _cols(db, "notes_cells")


def test_migration_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    _seed_v5(db)
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


def test_back_compat_insert_without_concept_uuid(tmp_path: Path) -> None:
    """An existing-style notes write (no concept_uuid) still inserts."""
    db = tmp_path / "x.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES ('Z','n.pdf','running','Z')"
        )
        conn.execute(
            "INSERT INTO notes_cells(run_id, sheet, row, label, html, updated_at) "
            "VALUES (1, 'Notes-CI', 4, 'Lbl', '<p>x</p>', 'Z')"
        )
        conn.commit()
        (cu,) = conn.execute(
            "SELECT concept_uuid FROM notes_cells WHERE row = 4"
        ).fetchone()
    finally:
        conn.close()
    assert cu is None
