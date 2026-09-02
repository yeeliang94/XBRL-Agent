"""Schema v45: durable notes-finding identity and grounding."""
from __future__ import annotations

import sqlite3

import pytest

from db import repository as repo
from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_schema_has_notes_flag_finding_grounding(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        assert CURRENT_SCHEMA_VERSION >= 45
        assert {"finding_id", "source_pages", "evidence"} <= _columns(
            conn, "notes_review_flags"
        )


def test_v44_upgrade_is_idempotent_and_preserves_notes_flags(tmp_path):
    db = tmp_path / "legacy.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'legacy.pdf', 'completed_with_errors')"
        ).lastrowid
        flag_id = conn.execute(
            "INSERT INTO notes_review_flags(run_id, kind, reason, status) "
            "VALUES (?, 'needs_human', 'legacy reason', 'open')",
            (run_id,),
        ).lastrowid
        conn.execute("UPDATE schema_version SET version = 44")
        try:
            conn.execute("ALTER TABLE notes_review_flags DROP COLUMN finding_id")
            conn.execute("ALTER TABLE notes_review_flags DROP COLUMN source_pages")
            conn.execute("ALTER TABLE notes_review_flags DROP COLUMN evidence")
        except sqlite3.OperationalError as exc:  # pragma: no cover
            pytest.skip(f"SQLite too old for DROP COLUMN: {exc}")

    init_db(db)
    init_db(db)
    with sqlite3.connect(db) as conn:
        assert (
            conn.execute("SELECT version FROM schema_version").fetchone()[0]
            == CURRENT_SCHEMA_VERSION
        )
        row = conn.execute(
            "SELECT reason, finding_id, source_pages, evidence "
            "FROM notes_review_flags WHERE id = ?", (flag_id,),
        ).fetchone()
        assert row == ("legacy reason", None, None, None)


def test_notes_flag_roundtrips_finding_identity_and_grounding(tmp_path):
    db = tmp_path / "roundtrip.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf")
        repo.insert_notes_review_flag(
            conn, run_id=run_id, kind="needs_human", reason="Both fit.",
            finding_id='["collision",49,[4,20]]', source_pages=[36, 37],
            evidence="pages 36-37 show both disclosures",
        )
    with repo.db_session(db) as conn:
        flag = repo.fetch_notes_review_flags(conn, run_id)[0]

    assert flag["finding_id"] == '["collision",49,[4,20]]'
    assert flag["source_pages"] == [36, 37]
    assert flag["evidence"] == "pages 36-37 show both disclosures"
