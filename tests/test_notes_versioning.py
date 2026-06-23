"""Notes-reviewer snapshot / revert (docs/PLAN.md Step 3).

Revert must be a FULL-SET replace so a reviewer-authored (previously-blank) row
is removed on revert, a cleared row comes back, and an edit is undone — the
tombstone semantics peer-review #2 required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from db import repository as repo
from db.schema import init_db
from notes.persistence import persist_notes_cells
from notes.versioning import (
    compute_notes_review_diff,
    ensure_notes_snapshot,
    has_notes_snapshot,
    revert_notes_to_original,
    snapshot_notes_cells,
)

_SHEET = "Notes-Listofnotes"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


def _seed_run(db_path: Path) -> int:
    with repo.db_session(db_path) as conn:
        return repo.create_run(
            conn, "x.pdf", session_id="s", output_dir="/tmp/s",
        )


def _cell(row: int, html: str) -> dict:
    return {
        "sheet": _SHEET, "row": row, "label": f"Row {row}",
        "html": html, "evidence": f"Page {row}", "source_pages": [row],
    }


def _live_rows(db_path: Path, run_id: int) -> dict[int, str]:
    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    return {c.row: c.html for c in cells}


def test_snapshot_then_revert_restores_exact_original(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    # Original extraction: rows 10 (edited later), 11 (cleared later).
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id, sheet_name=_SHEET,
        cells_written=[_cell(10, "<p>orig 10</p>"), _cell(11, "<p>orig 11</p>")],
    )
    assert ensure_notes_snapshot(str(db_path), run_id) is True
    assert has_notes_snapshot(str(db_path), run_id) is True
    # A second ensure is a no-op (snapshot stays the original).
    assert ensure_notes_snapshot(str(db_path), run_id) is False

    # Reviewer mutates: edit 10, clear 11, author a brand-new 12.
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_SHEET, row=10,
            label="Row 10", html="<p>EDITED 10</p>",
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_SHEET, row=12,
            label="Row 12", html="<p>AUTHORED 12</p>",
        )
        conn.execute(
            "DELETE FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = 11",
            (run_id, _SHEET),
        )
    assert _live_rows(db_path, run_id) == {10: "<p>EDITED 10</p>", 12: "<p>AUTHORED 12</p>"}

    # Revert: authored 12 gone, cleared 11 back, edited 10 undone.
    out = revert_notes_to_original(str(db_path), run_id)
    assert out["reverted"] is True
    assert out["cells_restored"] == 2
    assert _live_rows(db_path, run_id) == {10: "<p>orig 10</p>", 11: "<p>orig 11</p>"}


def test_empty_original_snapshot_then_authored_cell_reverts_to_empty(
    db_path: Path,
) -> None:
    """A run whose prose was EMPTY at snapshot time still has a valid (zero-row)
    snapshot; reverting must wipe a reviewer-authored cell back to empty. This
    is the case row-count-based existence checks get wrong."""
    run_id = _seed_run(db_path)
    # No prose yet — snapshot captures zero rows but is still "taken".
    assert ensure_notes_snapshot(str(db_path), run_id) is True
    assert has_notes_snapshot(str(db_path), run_id) is True

    # Reviewer authors the first cell.
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=_SHEET, row=50,
                               label="Row 50", html="<p>AUTHORED</p>")
    assert _live_rows(db_path, run_id) == {50: "<p>AUTHORED</p>"}

    out = revert_notes_to_original(str(db_path), run_id)
    assert out["reverted"] is True
    assert out["cells_restored"] == 0
    assert _live_rows(db_path, run_id) == {}  # authored cell removed


def test_revert_without_snapshot_is_a_no_op(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id, sheet_name=_SHEET,
        cells_written=[_cell(10, "<p>live</p>")],
    )
    out = revert_notes_to_original(str(db_path), run_id)
    assert out["reverted"] is False
    # Live prose untouched — never wiped without a backup.
    assert _live_rows(db_path, run_id) == {10: "<p>live</p>"}


def test_diff_shows_authored_cell_after_empty_original(db_path: Path) -> None:
    """Peer-review #3: a zero-row snapshot (empty original) is still a real
    snapshot — an authored cell after it must appear in the diff as 'authored'."""
    run_id = _seed_run(db_path)
    assert ensure_notes_snapshot(str(db_path), run_id) is True  # 0 rows captured
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=_SHEET, row=50,
                               label="Row 50", html="<p>AUTHORED</p>")
    diff = compute_notes_review_diff(str(db_path), run_id)
    assert [(d["row"], d["change"]) for d in diff] == [(50, "authored")]


def test_diff_reports_authored_edited_cleared(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id, sheet_name=_SHEET,
        cells_written=[_cell(10, "<p>orig 10</p>"), _cell(11, "<p>orig 11</p>")],
    )
    snapshot_notes_cells(str(db_path), run_id)
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=_SHEET, row=10,
                               label="Row 10", html="<p>EDITED</p>")
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=_SHEET, row=12,
                               label="Row 12", html="<p>NEW</p>")
        conn.execute(
            "DELETE FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = 11",
            (run_id, _SHEET),
        )
    diff = {d["row"]: d["change"] for d in compute_notes_review_diff(str(db_path), run_id)}
    assert diff == {10: "edited", 11: "cleared", 12: "authored"}
