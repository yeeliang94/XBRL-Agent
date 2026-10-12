"""Last-update migration preserves historical activity and tracks saved work."""
import sqlite3
import pytest

from db import repository as repo
from db.schema import CURRENT_SCHEMA_VERSION, init_db


@pytest.mark.parametrize("resolution", [None, "resolved", "dismissed"])
def test_v52_migration_backfills_latest_activity_and_is_idempotent(tmp_path, resolution):
    path = tmp_path / "legacy.db"
    init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO runs(created_at, pdf_filename, status, ended_at) "
                     "VALUES ('2026-04-01T10:00:00Z', 'old.pdf', 'completed', '2026-04-02T10:00:00Z')")
        repo.upsert_notes_cell(conn, run_id=1, sheet="Notes", row=10,
                               label="Note", html="<p>Saved edit.</p>")
        conn.execute("UPDATE notes_cells SET updated_at = '2026-04-03T10:00:00Z'")
        if resolution:
            conn.execute("INSERT INTO run_concept_conflicts(run_id, concept_uuid, period, entity_scope, kind, "
                         "status, created_at, resolved_at) VALUES (1, 'fact', 'CY', 'Company', "
                         "'cross_check_failure', ?, '2026-04-02T10:00:00Z', '2026-04-05T10:00:00Z')", (resolution,))
        conn.execute("INSERT INTO runs(created_at, pdf_filename, status) "
                     "VALUES ('2026-04-04T10:00:00Z', 'newer.pdf', 'completed')")
        # Recreate the actual v52 boundary, before activity triggers existed.
        triggers = conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
        for (name,) in triggers:
            conn.execute(f'DROP TRIGGER "{name}"')
        conn.execute("DROP INDEX ix_runs_updated_at")
        conn.execute("ALTER TABLE runs DROP COLUMN updated_at")
        conn.execute("UPDATE schema_version SET version = 52")
    init_db(path)
    init_db(path)
    with repo.db_session(path) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        runs = repo.list_runs(conn)
        summary = next(run for run in runs if run.pdf_filename == "old.pdf")
        assert summary.updated_at == ("2026-04-05T10:00:00.000Z" if resolution else "2026-04-03T10:00:00.000Z")
        assert runs[0].pdf_filename == ("old.pdf" if resolution else "newer.pdf")
        assert summary.status == "completed"
        assert conn.execute("SELECT html FROM notes_cells").fetchone()[0] == "<p>Saved edit.</p>"


def test_document_updates_track_setup_progress_and_completion_without_read_touches(tmp_path):
    path = tmp_path / "activity.db"
    init_db(path)
    with repo.db_session(path) as conn:
        conn.execute("PRAGMA recursive_triggers = ON")
        rid = repo.create_run(conn, "draft.pdf", status="draft")
        old = "2026-04-01T10:00:00.000Z"
        for sql, params in [
            ("UPDATE runs SET run_config_json = ? WHERE id = ?", ('{"filing_standard":"mpers"}', rid)),
            ("INSERT INTO run_events(run_id, ts, event_type) VALUES (?, ?, ?)", (rid, old, "pipeline_stage")),
            ("UPDATE runs SET status = ?, ended_at = ? WHERE id = ?", ("completed", old, rid)),
        ]:
            conn.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (old, rid))
            conn.execute(sql, params)
            summary = repo.list_runs(conn)[0]
            assert summary.updated_at > old
            repo.get_run_detail(conn, rid)
            assert repo.list_runs(conn)[0].updated_at == summary.updated_at


def test_every_document_owned_table_has_an_activity_policy(tmp_path):
    """Schema coverage is the boundary: future writers must choose a policy."""
    from db.schema import _RUN_ACTIVITY_TABLES, _RUN_ACTIVITY_EXCLUDED_TABLES

    path = tmp_path / "policy.db"
    init_db(path)
    with sqlite3.connect(path) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        owned = set()
        for table in tables:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            foreign_keys = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            if "run_id" in columns or any(row[2] == "runs" for row in foreign_keys):
                owned.add(table)
        watched = set(_RUN_ACTIVITY_TABLES)
        excluded = set(_RUN_ACTIVITY_EXCLUDED_TABLES)
        assert not watched & excluded
        assert owned == watched | excluded
        assert all(_RUN_ACTIVITY_EXCLUDED_TABLES.values())
