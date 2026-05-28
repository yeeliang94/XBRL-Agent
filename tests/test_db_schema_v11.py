"""DB migration v10 → v11: `concept_render_aliases` table.

Aliases let one ``concept_uuid`` carry more than one physical render
coordinate. The motivating case is a face-sheet row whose value rolls
up from a sub-sheet total via a cross-sheet formula (e.g. SOFP-CuNonCu
"Property, plant and equipment" → SOFP-Sub-CuNonCu *Total). Both rows
share the canonical concept identity; the primary coord stays on
concept_nodes, every extra physical home lands here so the importer,
cell_resolver, and concepts endpoint can find it.

Same pinning shape as `test_db_schema_v10.py`: fresh init creates the
table, a v10 fixture upgrades cleanly, re-init is idempotent, and the
FK to concept_nodes cascades on delete.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        r[1]: r[2]
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def test_current_schema_version_is_at_least_v11():
    assert CURRENT_SCHEMA_VERSION >= 11


def test_fresh_init_creates_concept_render_aliases(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _table_exists(conn, "concept_render_aliases")
        cols = _table_columns(conn, "concept_render_aliases")
        for required in (
            "id",
            "concept_uuid",
            "alias_sheet",
            "alias_row",
            "alias_col",
        ):
            assert required in cols, f"missing column {required!r}"
        assert _schema_version(conn) >= 11
    finally:
        conn.close()


def test_alias_insert_round_trips(tmp_path):
    """An alias row inserted against a real concept_node reads back
    intact and the UNIQUE(uuid, sheet, row, col) constraint refuses
    duplicates."""
    db = tmp_path / "rt.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # Minimum-viable concept_node so the FK is satisfied.
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, "
            "imported_at, shape) VALUES (?, ?, ?, ?)",
            ("mfrs-company-sofp-test-v1", "test.xlsx",
             "2026-05-28T00:00:00Z", "linear"),
        )
        uid = "00000000-0000-0000-0000-000000000001"
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, ?, 'LEAF', ?, 'SOFP-Sub-CuNonCu', 39, 'B')",
            (uid, "mfrs-company-sofp-test-v1",
             "*Total Property, plant and equipment"),
        )
        conn.execute(
            "INSERT INTO concept_render_aliases("
            "concept_uuid, alias_sheet, alias_row, alias_col) "
            "VALUES (?, 'SOFP-CuNonCu', 5, 'B')",
            (uid,),
        )
        conn.commit()

        row = conn.execute(
            "SELECT alias_sheet, alias_row, alias_col FROM "
            "concept_render_aliases WHERE concept_uuid = ?",
            (uid,),
        ).fetchone()
        assert row == ("SOFP-CuNonCu", 5, "B")

        # UNIQUE constraint refuses an exact duplicate.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO concept_render_aliases("
                "concept_uuid, alias_sheet, alias_row, alias_col) "
                "VALUES (?, 'SOFP-CuNonCu', 5, 'B')",
                (uid,),
            )
    finally:
        conn.close()


def test_alias_cascades_on_concept_delete(tmp_path):
    """Deleting a concept_node must sweep its aliases (FK ON DELETE
    CASCADE) so a dropped template can't leave orphan alias rows."""
    db = tmp_path / "cascade.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, "
            "imported_at, shape) VALUES (?, ?, ?, ?)",
            ("mfrs-company-sofp-test-v1", "test.xlsx",
             "2026-05-28T00:00:00Z", "linear"),
        )
        uid = "00000000-0000-0000-0000-000000000002"
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, ?, 'LEAF', 'x', 'X', 1, 'B')",
            (uid, "mfrs-company-sofp-test-v1"),
        )
        conn.execute(
            "INSERT INTO concept_render_aliases("
            "concept_uuid, alias_sheet, alias_row, alias_col) "
            "VALUES (?, 'X', 2, 'B')",
            (uid,),
        )
        conn.commit()

        conn.execute("DELETE FROM concept_nodes WHERE concept_uuid = ?", (uid,))
        conn.commit()

        row = conn.execute(
            "SELECT COUNT(*) FROM concept_render_aliases "
            "WHERE concept_uuid = ?",
            (uid,),
        ).fetchone()
        assert row[0] == 0
    finally:
        conn.close()


def test_v10_fixture_upgrades_cleanly(tmp_path):
    """A v10 DB walks forward to v11: the new table appears, marker
    bumps to 11, and existing data is undisturbed."""
    db = tmp_path / "v10.db"
    conn = sqlite3.connect(str(db))
    try:
        # Hand-build a minimal v10 DB: schema_version row at 10, plus
        # the runs table (the bits earlier migrations create) and a
        # concept_templates/concept_nodes pair so the new alias FK has
        # something to point at on later inserts. The actual schema in
        # a real v10 has many more tables; init_db is idempotent on
        # CREATE TABLE IF NOT EXISTS so they'll all materialise.
        conn.execute(
            "CREATE TABLE runs("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TEXT NOT NULL, pdf_filename TEXT NOT NULL, "
            "status TEXT NOT NULL, orchestration TEXT DEFAULT 'split')"
        )
        conn.execute(
            "CREATE TABLE schema_version(version INTEGER PRIMARY KEY)"
        )
        conn.execute("INSERT INTO schema_version(version) VALUES (10)")
        conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('2026-05-01T00:00:00Z', 'legacy.pdf', 'completed')"
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        assert _table_exists(conn, "concept_render_aliases")
        assert _schema_version(conn) >= 11
        # Legacy row untouched.
        row = conn.execute(
            "SELECT pdf_filename FROM runs WHERE pdf_filename = 'legacy.pdf'"
        ).fetchone()
        assert row[0] == "legacy.pdf"
    finally:
        conn.close()


def test_re_init_is_idempotent(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _table_exists(conn, "concept_render_aliases")
        # Index also created without errors.
        idx = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' "
            "AND name = 'ix_concept_render_aliases_concept_uuid'"
        ).fetchone()
        assert idx is not None
    finally:
        conn.close()
