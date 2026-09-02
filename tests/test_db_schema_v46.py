"""Schema v46: current-template membership for canonical concepts."""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_schema_tracks_current_concept_membership(tmp_path) -> None:
    db = tmp_path / "fresh.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        assert CURRENT_SCHEMA_VERSION >= 46
        assert {"is_current", "retired_at"} <= _columns(conn, "concept_nodes")


def test_v45_upgrade_is_idempotent_and_preserves_existing_concepts(tmp_path) -> None:
    db = tmp_path / "legacy.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path) "
            "VALUES ('legacy-template', 'legacy.xlsx')"
        )
        conn.execute(
            "INSERT INTO concept_nodes("
            "concept_uuid, template_id, kind, canonical_label, "
            "render_sheet, render_row, render_col"
            ") VALUES ('legacy-concept', 'legacy-template', 'LEAF', "
            "'Cash', 'SOFP', 10, 'B')"
        )
        conn.execute("UPDATE schema_version SET version = 45")
        conn.execute("ALTER TABLE concept_nodes DROP COLUMN retired_at")
        conn.execute("ALTER TABLE concept_nodes DROP COLUMN is_current")

    init_db(db)
    init_db(db)

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 46
        assert conn.execute(
            "SELECT is_current, retired_at FROM concept_nodes "
            "WHERE concept_uuid = 'legacy-concept'"
        ).fetchone() == (1, None)
