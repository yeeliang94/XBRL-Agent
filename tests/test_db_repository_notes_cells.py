"""Repository helpers for the `notes_cells` table.

Step 2 of docs/PLAN-NOTES-RICH-EDITOR.md. The server and the notes
coordinator both round-trip HTML payloads through these helpers —
raw SQL stays confined to db/repository.py the same way the v2 history
helpers are structured.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db import repository as repo
from db.schema import init_db


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


def _seed_run(conn: sqlite3.Connection) -> int:
    return repo.create_run(
        conn, "sample.pdf",
        session_id="sess-a", output_dir="/tmp/sess-a",
    )


def test_upsert_notes_cell_inserts_then_updates(db_path: Path) -> None:
    """First call inserts; second call on the same (run, sheet, row)
    overwrites html + evidence + source_pages + updated_at but keeps
    the id stable."""
    with repo.db_session(db_path) as conn:
        run_id = _seed_run(conn)
        cell_id_1 = repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-CI", row=4,
            label="Corporate info", html="<p>v1</p>",
            evidence="Page 3", source_pages=[3],
        )
        # Second write on the same coordinates should overwrite.
        cell_id_2 = repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-CI", row=4,
            label="Corporate info", html="<p>v2</p>",
            evidence="Page 3; Page 4", source_pages=[3, 4],
        )
    assert cell_id_1 == cell_id_2

    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert len(cells) == 1
    assert cells[0].html == "<p>v2</p>"
    assert cells[0].evidence == "Page 3; Page 4"
    assert cells[0].source_pages == [3, 4]


def test_list_notes_cells_for_run_returns_in_sheet_row_order(db_path: Path) -> None:
    """Result order must be (sheet, row) so the editor surfaces cells
    in the natural template order regardless of insertion order."""
    with repo.db_session(db_path) as conn:
        run_id = _seed_run(conn)
        # Insert out of order intentionally.
        repo.upsert_notes_cell(conn, run_id=run_id,
                               sheet="Notes-RelatedPartytran", row=5,
                               label="RP 5", html="<p>rp5</p>")
        repo.upsert_notes_cell(conn, run_id=run_id, sheet="Notes-CI",
                               row=12, label="CI 12", html="<p>ci12</p>")
        repo.upsert_notes_cell(conn, run_id=run_id, sheet="Notes-CI",
                               row=4, label="CI 4", html="<p>ci4</p>")

    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)

    ordered = [(c.sheet, c.row) for c in cells]
    assert ordered == [
        ("Notes-CI", 4),
        ("Notes-CI", 12),
        ("Notes-RelatedPartytran", 5),
    ]


def test_delete_notes_cells_for_run_sheet_clears_all_rows_for_one_sheet(
    db_path: Path,
) -> None:
    """Clobber semantics: a rerun deletes all cells for the run+sheet
    pair so the newly-emitted cells become the full replacement set."""
    with repo.db_session(db_path) as conn:
        run_id = _seed_run(conn)
        for row in (4, 5, 6):
            repo.upsert_notes_cell(conn, run_id=run_id, sheet="Notes-CI",
                                   row=row, label=f"CI {row}",
                                   html=f"<p>{row}</p>")
        repo.upsert_notes_cell(conn, run_id=run_id,
                               sheet="Notes-Issuedcapital", row=3,
                               label="IC 3", html="<p>ic3</p>")

    with repo.db_session(db_path) as conn:
        repo.delete_notes_cells_for_run_sheet(
            conn, run_id=run_id, sheet="Notes-CI",
        )

    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert len(cells) == 1
    assert cells[0].sheet == "Notes-Issuedcapital"


def test_upsert_preserves_source_pages_round_trip(db_path: Path) -> None:
    """source_pages is stored as JSON — round-trip parity on the list
    shape is the contract the editor and download paths rely on."""
    with repo.db_session(db_path) as conn:
        run_id = _seed_run(conn)
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-CI", row=4,
            label="L", html="<p>x</p>", source_pages=[10, 11, 12],
        )

    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert cells[0].source_pages == [10, 11, 12]


def test_list_notes_cells_tolerates_malformed_source_pages(db_path: Path) -> None:
    """Peer-review [MEDIUM] #5: list_notes_cells_for_run currently decodes
    `source_pages` JSON defensively but then calls `int(p)` on every
    element, which raises ValueError on strings/None and tanks the whole
    editor listing.

    Contract: a row with JSON like `[1, "abc", null, 3]` should load
    with `source_pages = [1, 3]` — invalid elements are filtered, not
    thrown. One corrupt row cannot block the rest of the run's cells.
    """
    with repo.db_session(db_path) as conn:
        run_id = _seed_run(conn)
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-CI", row=4,
            label="OK", html="<p>clean</p>", source_pages=[1, 2],
        )
    # Bypass the upsert helper's type checks to insert a deliberately
    # malformed source_pages blob — simulating a row written by a future
    # buggy writer or an ad-hoc DB migration.
    with sqlite3.connect(str(db_path)) as raw:
        raw.execute(
            "INSERT INTO notes_cells "
            "(run_id, sheet, row, label, html, evidence, source_pages, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, "Notes-CI", 5, "Bad", "<p>malformed</p>",
             None, '[1, "abc", null, 3]', "2026-04-24T00:00:00Z"),
        )
        raw.commit()

    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)

    # Two rows — the clean one and the malformed one, both loaded.
    by_row = {c.row: c for c in cells}
    assert by_row[4].source_pages == [1, 2]
    assert by_row[5].source_pages == [1, 3]  # "abc" and null filtered


def test_list_notes_cells_handles_non_list_source_pages(db_path: Path) -> None:
    """Additional defensive case: if the JSON decodes to a non-list
    (e.g. a bare number from a buggy writer), source_pages should
    degrade to an empty list rather than iterating over a scalar."""
    with repo.db_session(db_path) as conn:
        run_id = _seed_run(conn)

    with sqlite3.connect(str(db_path)) as raw:
        raw.execute(
            "INSERT INTO notes_cells "
            "(run_id, sheet, row, label, html, evidence, source_pages, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, "Notes-CI", 4, "Bad", "<p>scalar pages</p>",
             None, "42", "2026-04-24T00:00:00Z"),
        )
        raw.commit()

    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    assert len(cells) == 1
    assert cells[0].source_pages == []
