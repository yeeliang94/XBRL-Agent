"""Schema v28 — notes_coverage_rows (durable holistic coverage checklist,
docs/PLAN-notes-coverage-and-routing.md Phase 4). Pure CREATE TABLE IF NOT
EXISTS walk-forward (new table, no ALTER)."""
from __future__ import annotations

import sqlite3

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


_EXPECTED_COLS = {
    "run_id", "note_num", "subnote_ref", "status", "reason",
    "placements_json", "reviewer_added", "reviewer_verdict", "title",
    "page_lo", "page_hi", "updated_at",
}


def test_fresh_init_has_v28_table_and_columns(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_coverage_rows" in _tables(conn)
        assert _EXPECTED_COLS <= _columns(conn, "notes_coverage_rows")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION >= 28
    finally:
        conn.close()


def test_v27_db_walks_forward(tmp_path):
    """A DB pinned at v27 (no notes_coverage_rows) walks forward to v28."""
    db = tmp_path / "v27.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE IF EXISTS notes_coverage_rows")
        conn.execute("UPDATE schema_version SET version = 27")
        conn.commit()
        assert "notes_coverage_rows" not in _tables(conn)
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert "notes_coverage_rows" in _tables(conn)
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_init_db_is_idempotent_at_v28(tmp_path):
    db = tmp_path / "twice.db"
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
        assert "notes_coverage_rows" in _tables(conn)
    finally:
        conn.close()


def test_unique_index_coalesces_null_subnote_ref(tmp_path):
    """The top-level row (subnote_ref NULL) and its children never collide, but
    two top-level rows for the same note do."""
    from db import repository as repo

    db = tmp_path / "uniq.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")
        # Top-level + a child for the same note — allowed.
        conn.execute(
            "INSERT INTO notes_coverage_rows(run_id, note_num, subnote_ref, "
            "status, updated_at) VALUES (?, 9, NULL, 'placed', '')", (run_id,))
        conn.execute(
            "INSERT INTO notes_coverage_rows(run_id, note_num, subnote_ref, "
            "status, updated_at) VALUES (?, 9, '(a)', 'cited', '')", (run_id,))
        # A second top-level row for note 9 must violate the unique index.
        try:
            conn.execute(
                "INSERT INTO notes_coverage_rows(run_id, note_num, subnote_ref, "
                "status, updated_at) VALUES (?, 9, NULL, 'missing', '')", (run_id,))
            raised = False
        except sqlite3.IntegrityError:
            raised = True
    assert raised


def test_coverage_rows_cascade_on_run_delete(tmp_path):
    from db import repository as repo

    db = tmp_path / "cascade.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")
        conn.execute(
            "INSERT INTO notes_coverage_rows(run_id, note_num, subnote_ref, "
            "status, updated_at) VALUES (?, 1, NULL, 'placed', '')", (run_id,))
    with repo.db_session(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        left = conn.execute(
            "SELECT COUNT(*) FROM notes_coverage_rows"
        ).fetchone()[0]
    assert left == 0


def test_replace_and_fetch_round_trip(tmp_path):
    from db import repository as repo

    db = tmp_path / "rt.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")
        repo.replace_notes_coverage_for_run(conn, run_id, [
            {"note_num": 1, "subnote_ref": None, "status": "placed",
             "title": "Corporate information", "page_lo": 8, "page_hi": 9,
             "placements": [{"sheet": "Notes-CI", "row": 6, "row_label": "x",
                             "kind": "primary"}]},
            {"note_num": 1, "subnote_ref": "(a)", "status": "cited"},
            {"note_num": 5, "subnote_ref": None, "status": "missing",
             "reviewer_verdict": "not_applicable", "reason": "not disclosed",
             "reviewer_added": True},
        ])
    with repo.db_session(db) as conn:
        rows = repo.fetch_notes_coverage(conn, run_id)
    assert [r["note_num"] for r in rows] == [1, 1, 5]
    top1 = rows[0]
    assert top1["subnote_ref"] is None
    assert top1["placements"][0]["sheet"] == "Notes-CI"
    assert top1["page_lo"] == 8
    child = rows[1]
    assert child["subnote_ref"] == "(a)" and child["status"] == "cited"
    note5 = rows[2]
    assert note5["reviewer_verdict"] == "not_applicable"
    assert note5["reviewer_added"] is True

    # Replace is wholesale — a second call with fewer rows drops the rest.
    with repo.db_session(db) as conn:
        repo.replace_notes_coverage_for_run(conn, run_id, [
            {"note_num": 1, "subnote_ref": None, "status": "placed"},
        ])
        rows2 = repo.fetch_notes_coverage(conn, run_id)
    assert [r["note_num"] for r in rows2] == [1]
