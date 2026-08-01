"""The false greens peer review reproduced, 2026-08-01 — one test each.

Every case below produced a CLEAN verdict over content that was missing. They
are collected in one file on purpose: this is the failure mode the whole
feature exists to prevent, so the set of ways it has actually happened is
worth reading in one place.

The reproductions, in the reviewer's words:

1. a source write followed by the legacy clobber — zero cells remained, yet
   integrity reported clean;
2. relinking a cell from b1+b2 to b1 left b2 marked included at that cell;
3. a `routed` block with no destination resolved itself;
4. one block in two cells could not be observed at all;
5. a write to `Ghost` row 999 succeeded and received a clean verdict;
6. editing a cell back to its exact source HTML never cleared divergence.
"""
from __future__ import annotations

import pytest

from db import repository as repo
from db.schema import init_db
from notes import integrity, integrity_runner, lineage, source_write
from notes import source_repository as srepo
from notes.source_models import (
    Disposition,
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
def run(tmp_path):
    db = tmp_path / "audit.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
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
        yield conn, run_id, gen, db


def _verdict(conn, run_id, gen):
    return integrity.run_checks(
        integrity_runner.build_input(conn, run_id, gen, scout_available=True)
    )


def _write(conn, run_id, gen, block_ids, row=10):
    return source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=row,
        block_ids=block_ids, label="L",
    )


def test_a_complete_write_verifies_clean(run):
    """The control. Without this, every test below could pass by the checks
    simply never going green."""
    conn, run_id, gen, _db = run
    _write(conn, run_id, gen, ["b1", "b2"])
    assert _verdict(conn, run_id, gen).findings == []


# 1 --------------------------------------------------------------------------

def test_a_clobbered_sheet_does_not_verify_clean(run):
    """Reproduction 1: the legacy persistence path deletes every cell on the
    sheet. The dispositions still said `included`, so the run reported no
    findings over zero cells."""
    conn, run_id, gen, _db = run
    _write(conn, run_id, gen, ["b1", "b2"])
    repo.delete_notes_cells_for_run_sheet(conn, run_id=run_id, sheet="Notes")
    conn.commit()

    result = _verdict(conn, run_id, gen)
    assert result.requires_review is True
    assert {f.check for f in result.findings} >= {"placement"}
    assert {b for f in result.findings for b in f.block_ids} == {"b1", "b2"}


def test_a_rewrite_that_keeps_the_cell_keeps_its_lineage(run):
    """The other half of reproduction 1: `persist_notes_cells` clobbers and
    re-inserts, which used to drop the provenance columns with the row."""
    from notes.persistence import persist_notes_cells

    conn, run_id, gen, db = run
    _write(conn, run_id, gen, ["b1", "b2"])
    html = conn.execute(
        "SELECT html FROM notes_cells WHERE run_id = ? AND row = 10", (run_id,)
    ).fetchone()["html"]
    conn.commit()

    persist_notes_cells(
        db_path=str(db), run_id=run_id, sheet_name="Notes",
        cells_written=[{"sheet": "Notes", "row": 10, "label": "L", "html": html}],
    )

    with repo.db_session(db) as c2:
        state = lineage.read_lineage(c2, run_id, "Notes", 10)
        assert state.source_rendered_sha256, "lineage survived the rewrite"
        assert _verdict(c2, run_id, gen).findings == []


def test_a_rewrite_that_drops_a_cell_retires_its_placements(run):
    from notes.persistence import persist_notes_cells

    conn, run_id, gen, db = run
    _write(conn, run_id, gen, ["b1", "b2"])
    conn.commit()

    persist_notes_cells(
        db_path=str(db), run_id=run_id, sheet_name="Notes", cells_written=[],
    )
    with repo.db_session(db) as c2:
        assert srepo.active_placements(c2, gen) == []
        assert _verdict(c2, run_id, gen).requires_review is True


# 2 --------------------------------------------------------------------------

def test_a_relink_leaves_the_dropped_block_unaccounted(run):
    """Reproduction 2. `notes_block_usages` is one row per block, so b2 kept
    saying `included at Notes:10` after it had been relinked out."""
    conn, run_id, gen, _db = run
    _write(conn, run_id, gen, ["b1", "b2"])
    _write(conn, run_id, gen, ["b1"])

    result = _verdict(conn, run_id, gen)
    assert result.requires_review is True
    placement = [f for f in result.findings if f.check == "placement"]
    assert placement and placement[0].block_ids == ["b2"]


def test_relinking_the_block_back_clears_the_finding(run):
    conn, run_id, gen, _db = run
    _write(conn, run_id, gen, ["b1", "b2"])
    _write(conn, run_id, gen, ["b1"])
    _write(conn, run_id, gen, ["b1", "b2"])
    assert _verdict(conn, run_id, gen).findings == []


# 3 --------------------------------------------------------------------------

def test_a_routed_block_with_no_destination_does_not_settle(run):
    """Reproduction 3: `routed` resolved on its own, so recording it was a way
    to make a block disappear from the count."""
    conn, run_id, gen, _db = run
    _write(conn, run_id, gen, ["b1"])
    srepo.record_disposition(conn, run_id, gen, "b2", Disposition.ROUTED)

    result = _verdict(conn, run_id, gen)
    assert result.requires_review is True
    assert any("no destination" in f.message for f in result.findings)


def test_a_routed_block_with_a_destination_settles(run):
    conn, run_id, gen, _db = run
    _write(conn, run_id, gen, ["b1"])
    srepo.record_disposition(
        conn, run_id, gen, "b2", Disposition.ROUTED,
        sheet="Policies", row=4,
    )
    assert _verdict(conn, run_id, gen).findings == []


# 4 --------------------------------------------------------------------------

def test_one_block_in_two_cells_is_now_observable(run):
    """Reproduction 4: the duplicate check could not fire, because the shape it
    looked for was unrepresentable."""
    conn, run_id, gen, _db = run
    repo.upsert_notes_cell(
        conn, run_id=run_id, sheet="Notes", row=20, label="L2", html=""
    )
    _write(conn, run_id, gen, ["b1", "b2"], row=10)
    srepo.set_cell_placements(conn, run_id, gen, "Notes", 20, ["b1"])
    conn.commit()

    result = _verdict(conn, run_id, gen)
    duplicates = [f for f in result.findings if f.check == "approved_duplicate"]
    assert duplicates and duplicates[0].block_ids == ["b1"]


# 5 --------------------------------------------------------------------------

def test_a_write_to_a_row_that_does_not_exist_is_refused(run):
    """Reproduction 5: `Ghost` row 999 wrote successfully and verified clean."""
    conn, run_id, gen, _db = run
    with pytest.raises(source_write.SourceWriteError) as exc:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Ghost", row=999,
            block_ids=["b1"], template_prefix="mfrs-company-",
        )
    assert "not a row of this filing" in str(exc.value)


def test_an_agent_may_not_write_another_sheet(run):
    conn, run_id, gen, _db = run
    with pytest.raises(source_write.SourceWriteError) as exc:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Policies", row=4,
            block_ids=["b1"], template_prefix="mfrs-company-",
            allowed_sheets=["Notes"],
        )
    assert "not a sheet you may write" in str(exc.value)


# 6 --------------------------------------------------------------------------

def test_editing_a_cell_back_to_its_source_clears_the_divergence(run):
    """Reproduction 6: source renders hashed `version + html` while human edits
    hashed plain html, so equality was impossible and the mark never cleared.
    This goes through the REAL path — render, edit away, edit back."""
    conn, run_id, gen, _db = run
    _write(conn, run_id, gen, ["b1", "b2"])
    rendered = conn.execute(
        "SELECT html FROM notes_cells WHERE run_id = ? AND row = 10", (run_id,)
    ).fetchone()["html"]

    away = lineage.mark_human_edit(conn, run_id, "Notes", 10, "<p>changed</p>")
    assert away.diverged is True

    back = lineage.mark_human_edit(conn, run_id, "Notes", 10, rendered)
    assert back.diverged is False
    assert back.content_origin == "source_exact"
    assert back.source_diverged_at is None


def test_the_render_version_is_recorded_separately(run):
    """It has to stay visible — a render-shape change is still worth knowing
    about; it just must not corrupt the content comparison."""
    conn, run_id, gen, _db = run
    _write(conn, run_id, gen, ["b1"])
    stored = conn.execute(
        "SELECT source_render_version FROM notes_cells "
        "WHERE run_id = ? AND row = 10", (run_id,),
    ).fetchone()["source_render_version"]
    from notes.source_render import RENDER_VERSION

    assert stored == RENDER_VERSION
