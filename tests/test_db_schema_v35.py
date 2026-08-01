"""Schema v35 — notes source integrity (PLAN-notes-source-integrity-build Phase 3).

Six new tables plus five nullable columns on `notes_cells`. Everything here is
additive and inert until `XBRL_NOTES_SOURCE_INTEGRITY` is shadow/enforce, so a
rollback leaves the tables unused and old code ignores the columns.

The load-bearing shapes:

* one ACTIVE generation per run — the completeness count has to be a fact
  about one frozen reading of the document, not an average over several;
* one usage row per (generation, block) — a block with two dispositions is
  the ambiguity this feature exists to remove;
* `notes_disposition_events` is APPEND-ONLY history, because
  `notes_block_usages.created_by` is mutable and therefore not an audit trail
  (peer-review finding 11);
* no CHECK on `status` columns (gotcha #11).
"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import CURRENT_SCHEMA_VERSION, init_db

_NEW_TABLES = [
    "notes_source_generations",
    "notes_source_notes",
    "notes_source_blocks",
    "notes_block_usages",
    "notes_disposition_events",
    "notes_integrity_runs",
]

_NEW_CELL_COLUMNS = {
    "source_generation_id",
    "source_rendered_sha256",
    "current_html_sha256",
    "content_origin",
    "source_diverged_at",
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def _seed_run(conn: sqlite3.Connection) -> int:
    conn.execute(
        "INSERT INTO runs(pdf_filename, status, created_at) "
        "VALUES ('x.pdf','draft','')"
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _seed_generation(conn: sqlite3.Connection, run_id: int, no: int = 1) -> int:
    conn.execute(
        "INSERT INTO notes_source_generations(run_id, generation_no, status) "
        "VALUES (?, ?, 'building')",
        (run_id, no),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


# --------------------------------------------------------------------------
# fresh init
# --------------------------------------------------------------------------

def test_fresh_init_has_every_new_table(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert set(_NEW_TABLES) <= _tables(conn)
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION >= 35
    finally:
        conn.close()


def test_fresh_init_has_notes_cells_provenance_columns(tmp_path):
    db = tmp_path / "fresh2.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert _NEW_CELL_COLUMNS <= _columns(conn, "notes_cells")
    finally:
        conn.close()


def test_content_origin_is_separate_from_style_source(tmp_path):
    """Where the TEXT came from and how the FORMATTING was decided are
    different facts. Conflating them loses the distinction between a cell
    copied verbatim from Word and one an agent composed."""
    db = tmp_path / "fresh3.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        cols = _columns(conn, "notes_cells")
        assert {"content_origin", "style_source"} <= cols
    finally:
        conn.close()


# --------------------------------------------------------------------------
# walk-forward from v34
# --------------------------------------------------------------------------

def test_v34_db_walks_forward(tmp_path):
    db = tmp_path / "v34.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        for t in _NEW_TABLES:
            conn.execute(f"DROP TABLE {t}")
        conn.execute("UPDATE schema_version SET version = 34")
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert set(_NEW_TABLES) <= _tables(conn)
        assert _NEW_CELL_COLUMNS <= _columns(conn, "notes_cells")
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_migration_is_idempotent(tmp_path):
    """Repeated init_db must not duplicate columns or advance past current."""
    db = tmp_path / "idem.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        before = _columns(conn, "notes_cells")
        before_tables = _tables(conn)
    finally:
        conn.close()

    init_db(db)
    init_db(db)

    conn = sqlite3.connect(str(db))
    try:
        assert _schema_version(conn) == CURRENT_SCHEMA_VERSION
        assert _columns(conn, "notes_cells") == before
        assert _tables(conn) == before_tables
    finally:
        conn.close()


def test_old_rows_survive_the_migration_with_null_provenance(tmp_path):
    """A pre-feature cell keeps its content and simply has no lineage."""
    db = tmp_path / "old.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        run_id = _seed_run(conn)
        conn.execute(
            "INSERT INTO notes_cells(run_id, sheet, row, label, html, updated_at) "
            "VALUES (?, 'Notes', 10, 'Trade receivables', '<p>x</p>', '')",
            (run_id,),
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT html, content_origin, source_generation_id FROM notes_cells"
        ).fetchone()
        assert row[0] == "<p>x</p>"
        assert row[1] is None, "no origin claimed for a pre-feature cell"
        assert row[2] is None
    finally:
        conn.close()


# --------------------------------------------------------------------------
# constraints that carry meaning
# --------------------------------------------------------------------------

def test_generation_number_is_unique_per_run(tmp_path):
    db = tmp_path / "gen.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        run_id = _seed_run(conn)
        _seed_generation(conn, run_id, 1)
        with pytest.raises(sqlite3.IntegrityError):
            _seed_generation(conn, run_id, 1)
    finally:
        conn.close()


def test_block_id_is_unique_within_a_generation(tmp_path):
    db = tmp_path / "blocks.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        run_id = _seed_run(conn)
        gen = _seed_generation(conn, run_id)
        conn.execute(
            "INSERT INTO notes_source_blocks(generation_id, block_id) VALUES (?, 'b1')",
            (gen,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO notes_source_blocks(generation_id, block_id) "
                "VALUES (?, 'b1')",
                (gen,),
            )
    finally:
        conn.close()


def test_the_same_block_id_may_exist_in_two_generations(tmp_path):
    """A rerun mints a fresh generation with stable block ids; the two must
    coexist so the previous reading stays intact until the new one activates."""
    db = tmp_path / "twogen.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        run_id = _seed_run(conn)
        g1 = _seed_generation(conn, run_id, 1)
        g2 = _seed_generation(conn, run_id, 2)
        conn.execute(
            "INSERT INTO notes_source_blocks(generation_id, block_id) VALUES (?, 'b1')",
            (g1,),
        )
        conn.execute(
            "INSERT INTO notes_source_blocks(generation_id, block_id) VALUES (?, 'b1')",
            (g2,),
        )
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM notes_source_blocks"
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_one_usage_per_block_per_generation(tmp_path):
    """Two dispositions for one block is exactly the ambiguity this feature
    removes, so the database refuses it rather than leaving it to a check."""
    db = tmp_path / "usage.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        run_id = _seed_run(conn)
        gen = _seed_generation(conn, run_id)
        conn.execute(
            "INSERT INTO notes_block_usages(run_id, generation_id, block_id, "
            "disposition) VALUES (?, ?, 'b1', 'included')",
            (run_id, gen),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO notes_block_usages(run_id, generation_id, block_id, "
                "disposition) VALUES (?, ?, 'b1', 'excluded')",
                (run_id, gen),
            )
    finally:
        conn.close()


def test_disposition_events_accept_many_rows_per_block(tmp_path):
    """Append-only history: the same block changing hands three times must
    leave three rows, not one overwritten one."""
    db = tmp_path / "events.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        run_id = _seed_run(conn)
        gen = _seed_generation(conn, run_id)
        for i, (frm, to) in enumerate(
            [(None, "unresolved"), ("unresolved", "included"), ("included", "routed")]
        ):
            conn.execute(
                "INSERT INTO notes_disposition_events(run_id, generation_id, "
                "block_id, from_disposition, to_disposition, actor) "
                "VALUES (?, ?, 'b1', ?, ?, 'human')",
                (run_id, gen, frm, to),
            )
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM notes_disposition_events WHERE block_id='b1'"
        ).fetchone()[0] == 3
    finally:
        conn.close()


def test_status_columns_have_no_check_constraint(tmp_path):
    """Gotcha #11: a new status value must not need a full-table migration."""
    db = tmp_path / "status.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        run_id = _seed_run(conn)
        conn.execute(
            "INSERT INTO notes_source_generations(run_id, generation_no, status) "
            "VALUES (?, 9, 'a-status-invented-next-year')",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO notes_integrity_runs(run_id, generation_id, status, mode) "
            "VALUES (?, 1, 'brand-new-verdict', 'brand-new-mode')",
            (run_id,),
        )
        conn.commit()
    finally:
        conn.close()


def test_deleting_a_run_sweeps_its_source_rows(tmp_path):
    db = tmp_path / "cascade.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        run_id = _seed_run(conn)
        gen = _seed_generation(conn, run_id)
        conn.execute(
            "INSERT INTO notes_source_blocks(generation_id, block_id) VALUES (?, 'b1')",
            (gen,),
        )
        conn.execute(
            "INSERT INTO notes_block_usages(run_id, generation_id, block_id) "
            "VALUES (?, ?, 'b1')",
            (run_id, gen),
        )
        conn.commit()
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        for t in ("notes_source_generations", "notes_source_blocks",
                  "notes_block_usages"):
            assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
    finally:
        conn.close()
