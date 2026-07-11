"""Pinning tests for advisory write-collision detection (Harness Item 5).

`persist_notes_cells` fingerprints each batch entry; two writers landing
DIFFERENT content on the same (sheet, row) in one batch logs a loud
advisory warning — the outcome (last write wins) is unchanged. Identical
re-sends (the Sheet-12 sink's replace case) never warn. ``XBRL_WRITE_FRESHNESS=off``
silences detection. Enforcement is deliberately NOT implemented (plan F.4).
"""

import logging

import pytest

from notes.fingerprint import content_fingerprint, freshness_mode
from notes.persistence import persist_notes_cells


@pytest.fixture()
def notes_db(tmp_path):
    from db.schema import init_db

    db = str(tmp_path / "t.db")
    init_db(db)
    from db import repository as repo

    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, session_id="s1", status="running")
    return db, run_id


def _cell(row, html, sheet="LIST_OF_NOTES"):
    return {"sheet": sheet, "row": row, "label": f"Note row {row}", "html": html}


def test_fingerprint_is_stable_and_short():
    fp = content_fingerprint("<p>hello</p>")
    assert fp == content_fingerprint("<p>hello</p>")
    assert len(fp) == 12
    assert fp != content_fingerprint("<p>other</p>")


def test_collision_logs_advisory_and_last_write_wins(notes_db, caplog):
    db, run_id = notes_db
    with caplog.at_level(logging.WARNING):
        n = persist_notes_cells(
            db_path=db,
            run_id=run_id,
            sheet_name="LIST_OF_NOTES",
            cells_written=[
                _cell(10, "<p>from sub-agent A</p>"),
                _cell(10, "<p>from sub-agent B</p>"),  # same row, different content
            ],
        )
    assert n == 2
    warnings = [r for r in caplog.records if "notes write collision" in r.getMessage()]
    assert len(warnings) == 1
    # Behavior unchanged: last write wins in the DB.
    from db import repository as repo

    with repo.db_session(db) as conn:
        html = conn.execute(
            "SELECT html FROM notes_cells WHERE run_id=? AND sheet='LIST_OF_NOTES' AND row=10",
            (run_id,),
        ).fetchone()[0]
    assert "sub-agent B" in html


def test_identical_resend_never_warns(notes_db, caplog):
    db, run_id = notes_db
    with caplog.at_level(logging.WARNING):
        persist_notes_cells(
            db_path=db,
            run_id=run_id,
            sheet_name="LIST_OF_NOTES",
            cells_written=[
                _cell(10, "<p>same</p>"),
                _cell(10, "<p>same</p>"),
            ],
        )
    assert not [r for r in caplog.records if "collision" in r.getMessage()]


def test_distinct_rows_never_warn(notes_db, caplog):
    db, run_id = notes_db
    with caplog.at_level(logging.WARNING):
        persist_notes_cells(
            db_path=db,
            run_id=run_id,
            sheet_name="LIST_OF_NOTES",
            cells_written=[_cell(10, "<p>a</p>"), _cell(11, "<p>b</p>")],
        )
    assert not [r for r in caplog.records if "collision" in r.getMessage()]


def test_off_switch_silences(notes_db, caplog, monkeypatch):
    monkeypatch.setenv("XBRL_WRITE_FRESHNESS", "off")
    assert freshness_mode() == "off"
    db, run_id = notes_db
    with caplog.at_level(logging.WARNING):
        persist_notes_cells(
            db_path=db,
            run_id=run_id,
            sheet_name="LIST_OF_NOTES",
            cells_written=[_cell(10, "<p>x</p>"), _cell(10, "<p>y</p>")],
        )
    assert not [r for r in caplog.records if "collision" in r.getMessage()]
