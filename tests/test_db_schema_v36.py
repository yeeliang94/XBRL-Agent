"""Schema v36 — `runs.notes_integrity_mode` (plan Step 3.5).

Peer review found that the v34→v35 test never exercised its ALTER, because the
fixture started from a fresh current-version database and only dropped tables.
This one builds a genuine v35 shape: the column is REMOVED before `init_db`
runs, so the migration is the only thing that can put it back.
"""
from __future__ import annotations

import sqlite3

import pytest

from db import repository as repo
from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_v36_is_reachable_from_the_current_version():
    assert CURRENT_SCHEMA_VERSION >= 36


def test_fresh_database_has_the_column(tmp_path):
    db = tmp_path / "fresh.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        assert "notes_integrity_mode" in _columns(conn, "runs")
        assert conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0] == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_a_real_v35_database_migrates_forward(tmp_path):
    db = tmp_path / "v35.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        try:
            conn.execute("ALTER TABLE runs DROP COLUMN notes_integrity_mode")
        except sqlite3.OperationalError as exc:  # pragma: no cover
            pytest.skip(f"SQLite too old for DROP COLUMN: {exc}")
        conn.execute("UPDATE schema_version SET version = 35")
        conn.commit()
        assert "notes_integrity_mode" not in _columns(conn, "runs"), (
            "fixture must genuinely lack the column or the ALTER is untested"
        )
    finally:
        conn.close()

    init_db(db)

    conn = sqlite3.connect(db)
    try:
        assert "notes_integrity_mode" in _columns(conn, "runs")
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
        cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
        assert cols.count("notes_integrity_mode") == 1
    finally:
        conn.close()


def test_a_pre_feature_run_reads_null_not_off(tmp_path):
    """NULL means the feature did not exist for this run. `off` means somebody
    turned it off. Collapsing the two would invent a decision nobody made."""
    db = tmp_path / "runs.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.pdf", session_id="s", output_dir=str(tmp_path / "s")
        )
        row = conn.execute(
            "SELECT notes_integrity_mode FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["notes_integrity_mode"] is None


def test_the_mode_can_be_recorded_and_read_back(tmp_path):
    db = tmp_path / "mode.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.pdf", session_id="s", output_dir=str(tmp_path / "s")
        )
        repo.set_notes_integrity_mode(conn, run_id, "shadow")
        assert repo.notes_integrity_mode(conn, run_id) == "shadow"


def test_an_unknown_mode_is_refused(tmp_path):
    """The column has no CHECK constraint (gotcha #11), so the guard lives in
    the writer — otherwise a typo becomes an unexplainable historical run."""
    db = tmp_path / "bad.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.pdf", session_id="s", output_dir=str(tmp_path / "s")
        )
        with pytest.raises(ValueError):
            repo.set_notes_integrity_mode(conn, run_id, "enforce-ish")
