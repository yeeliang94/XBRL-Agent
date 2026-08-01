"""Step 7.2's targeted retry — the consumer peer review found missing.

`missing_block_ids` was computed, returned, and read by nothing. The step's
tests only proved the list could be built, which is not the same as the retry
existing. This file tests the retry.

The retry is deterministic rather than another agent turn: every unplaced
block already belongs to a note, and the render is code. So it re-renders the
affected cell from the blocks that note owns. There is exactly ONE — an
unrepairable gap goes to review rather than round the loop again.
"""
from __future__ import annotations

import pytest

import server as server_module
from db import repository as repo
from db.schema import init_db
from notes import source_write
from notes import source_repository as srepo
from notes.source_models import (
    Disposition,
    IntegrityMode,
    OwnerKind,
    SourceBlock,
    SourceNote,
)

BLOCKS = [
    SourceBlock(block_id="b1", block_kind="paragraph", reading_order=0,
                canonical_html="<p>one</p>", source_note_id="n5",
                owner_kind=OwnerKind.NOTE),
    SourceBlock(block_id="b2", block_kind="paragraph", reading_order=1,
                canonical_html="<p>two</p>", source_note_id="n5",
                owner_kind=OwnerKind.NOTE),
]


@pytest.fixture()
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "AUDIT_DB_PATH", tmp_path / "audit.sqlite")
    init_db(server_module.AUDIT_DB_PATH)
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
        gen = srepo.begin_generation(conn, run_id, input_kind="docx_html")
        srepo.write_blocks(conn, gen, BLOCKS)
        srepo.write_notes(conn, gen, [
            SourceNote(source_note_id="n5", top_note_num="5"),
        ])
        srepo.activate_generation(conn, gen)
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=10, label="L", html=""
        )
    return run_id, gen


def _check(run_id, gen):
    return server_module._run_notes_integrity_check(
        run_id, gen, IntegrityMode.ENFORCE, None
    )


def test_the_retry_repairs_a_relinked_away_block(run):
    """The reproduction the retry is for: a cell was relinked from b1+b2 to
    b1, so b2 is unplaced. Its note owns both, so re-rendering the cell from
    the note's blocks closes the gap without asking a model anything."""
    run_id, gen = run
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["b1", "b2"], label="L",
        )
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["b1"], label="L",
        )

    before = _check(run_id, gen)
    assert before["requires_review"] is True
    assert before["missing_block_ids"] == ["b2"]

    after = server_module._retry_missing_source_blocks(
        run_id, gen, IntegrityMode.ENFORCE, before, None
    )
    assert after["retry_repaired_cells"] == 1
    assert after["requires_review"] is False
    assert after["missing_block_ids"] == []


def test_the_retry_appends_an_attempt_two_verdict(run):
    """Attempt 1 must survive — the point of a retry is that somebody can see
    what it changed."""
    run_id, gen = run
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["b1", "b2"], label="L",
        )
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["b1"], label="L",
        )
    before = _check(run_id, gen)
    server_module._retry_missing_source_blocks(
        run_id, gen, IntegrityMode.ENFORCE, before, None
    )
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT attempt, status FROM notes_integrity_runs "
            "WHERE run_id = ? ORDER BY attempt", (run_id,),
        ).fetchall()
    assert [r["attempt"] for r in rows] == [1, 2]
    assert [r["status"] for r in rows] == ["needs_review", "complete"]


def test_a_note_that_was_never_placed_is_left_for_review(run):
    """A retry cannot guess where a note belongs, so it does not try."""
    run_id, gen = run
    before = _check(run_id, gen)
    assert before["requires_review"] is True
    after = server_module._retry_missing_source_blocks(
        run_id, gen, IntegrityMode.ENFORCE, before, None
    )
    assert after["requires_review"] is True
    assert "retry_repaired_cells" not in after


def test_there_is_no_second_retry(run):
    """Bounded by construction: the function is called once from the pipeline
    and does not recurse."""
    import inspect

    src = inspect.getsource(server_module._retry_missing_source_blocks)
    assert "_retry_missing_source_blocks" not in src.split("\n", 1)[1], (
        "the retry must not call itself"
    )
    pipeline = inspect.getsource(server_module.run_multi_agent_stream)
    assert pipeline.count("_retry_missing_source_blocks") == 1


def test_a_clean_run_skips_the_retry_entirely(run):
    run_id, gen = run
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["b1", "b2"], label="L",
        )
    before = _check(run_id, gen)
    assert before["requires_review"] is False
    after = server_module._retry_missing_source_blocks(
        run_id, gen, IntegrityMode.ENFORCE, before, None
    )
    assert after is before, "no work, no new verdict row"


def test_off_mode_never_retries(run):
    run_id, gen = run
    outcome = {"missing_block_ids": ["b2"]}
    assert server_module._retry_missing_source_blocks(
        run_id, gen, IntegrityMode.OFF, outcome, None
    ) is outcome


def test_an_excluded_block_is_not_dragged_back_in(run):
    """A block a person deliberately excluded is settled, so it is not in the
    missing list and the retry leaves it alone."""
    run_id, gen = run
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["b1"], label="L",
        )
        srepo.record_disposition(
            conn, run_id, gen, "b2", Disposition.EXCLUDED,
            reason_code="PAGE_FOOTER", actor="human",
        )
    before = _check(run_id, gen)
    assert before["requires_review"] is False
    assert before["missing_block_ids"] == []
