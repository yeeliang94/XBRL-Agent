"""The link-only write contract — plan Phase 6, Step 6.2, and Step 5.1.

One function owns "build a cell from source parts" because three callers need
identical guarantees. The tests below are the guarantees:

* a cell written from blocks records BOTH its lineage and a disposition per
  block, in the same transaction — a write that skipped the dispositions would
  report a gap the run does not have;
* a fabricated block id is refused, so no cell traces to nothing;
* naming half a split table pulls in the other half rather than rendering half
  a disclosure;
* an oversized note is refused with an instruction, never truncated.
"""
from __future__ import annotations

import pytest

from db import repository as repo
from db.schema import init_db
from notes import lineage, source_write
from notes import source_repository as srepo
from notes.source_models import ContentOrigin, Disposition, SourceBlock

BLOCKS = [
    SourceBlock(block_id="b1", block_kind="heading", reading_order=0,
                canonical_html="<h3>5. Receivables</h3>"),
    SourceBlock(block_id="b2", block_kind="paragraph", reading_order=1,
                canonical_html="<p>Stated at cost.</p>"),
    SourceBlock(block_id="b3", block_kind="table", reading_order=2,
                canonical_html="<table><tr><td>a</td><td>1</td></tr></table>",
                table_group_id="tg1"),
    SourceBlock(block_id="b4", block_kind="table", reading_order=3,
                canonical_html="<table><tr><td>b</td><td>2</td></tr></table>",
                table_group_id="tg1"),
]


@pytest.fixture()
def conn_gen(tmp_path):
    db = tmp_path / "audit.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
        gen = srepo.begin_generation(conn, run_id, input_kind="docx_html")
        srepo.write_blocks(conn, gen, BLOCKS)
        srepo.activate_generation(conn, gen)
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=10, label="Receivables",
            html="", evidence=None, source_pages=[],
        )
        yield conn, run_id, gen


def test_a_write_records_lineage_and_a_disposition_per_block(conn_gen):
    conn, run_id, gen = conn_gen
    out = source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b1", "b2"], label="Receivables",
    )
    assert out.block_ids == ["b1", "b2"]

    state = lineage.read_lineage(conn, run_id, "Notes", 10)
    assert state.source_generation_id == gen
    assert state.content_origin == ContentOrigin.SOURCE_EXACT.value
    assert state.diverged is False

    usages = {u["block_id"]: u for u in srepo.fetch_usages(conn, gen)}
    assert set(usages) == {"b1", "b2"}
    for u in usages.values():
        assert u["disposition"] == Disposition.INCLUDED.value
        assert (u["sheet"], u["row"]) == ("Notes", 10)


def test_the_written_cell_holds_the_rendered_source(conn_gen):
    conn, run_id, gen = conn_gen
    source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b1", "b2"],
    )
    html = conn.execute(
        "SELECT html FROM notes_cells WHERE run_id = ? AND row = 10", (run_id,)
    ).fetchone()["html"]
    assert "Receivables" in html and "Stated at cost" in html


def test_naming_half_a_split_table_pulls_in_the_rest(conn_gen):
    """Rendering half a table and calling the note complete is exactly what
    the table group exists to prevent."""
    conn, run_id, gen = conn_gen
    out = source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b3"],
    )
    assert out.block_ids == ["b3", "b4"]
    assert any("rest of a table" in w for w in out.warnings)


def test_the_agent_is_told_what_was_added(conn_gen):
    conn, run_id, gen = conn_gen
    out = source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b3"],
    )
    assert "b4" in out.as_message()


def test_a_fabricated_block_id_is_refused(conn_gen):
    conn, run_id, gen = conn_gen
    with pytest.raises(source_write.SourceWriteError) as exc:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["b1", "b999"],
        )
    assert "b999" in str(exc.value)


def test_a_refused_write_leaves_no_cell_and_no_dispositions(conn_gen):
    conn, run_id, gen = conn_gen
    with pytest.raises(source_write.SourceWriteError):
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["b999"],
        )
    assert srepo.fetch_usages(conn, gen) == []
    assert conn.execute(
        "SELECT html FROM notes_cells WHERE run_id = ? AND row = 10", (run_id,)
    ).fetchone()["html"] == ""


def test_an_empty_selection_is_refused(conn_gen):
    conn, run_id, gen = conn_gen
    with pytest.raises(source_write.SourceWriteError):
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=[],
        )


def test_an_oversized_note_is_refused_with_an_instruction(conn_gen):
    """Step 0.6's decision: cap the new path and flag, never cut short."""
    conn, run_id, gen = conn_gen
    srepo.write_blocks(conn, gen, BLOCKS + [
        SourceBlock(block_id="big", block_kind="paragraph", reading_order=9,
                    canonical_html="<p>" + ("word " * 9000) + "</p>"),
    ])
    with pytest.raises(source_write.SourceWriteError) as exc:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["big"],
        )
    message = str(exc.value)
    assert "never cut short" in message
    assert "Split the note" in message


def test_rewriting_the_same_cell_replaces_rather_than_accumulates(conn_gen):
    conn, run_id, gen = conn_gen
    source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b1", "b2"],
    )
    source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b1"],
    )
    html = conn.execute(
        "SELECT html FROM notes_cells WHERE run_id = ? AND row = 10", (run_id,)
    ).fetchone()["html"]
    assert "Stated at cost" not in html


def test_the_previous_blocks_keep_their_disposition_after_a_relink(conn_gen):
    """A relink does not silently un-use the parts it dropped — they stay
    recorded, and the integrity pass surfaces them as used somewhere they no
    longer are, which is a question for a person."""
    conn, run_id, gen = conn_gen
    source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b1", "b2"],
    )
    source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b1"],
    )
    usages = {u["block_id"] for u in srepo.fetch_usages(conn, gen)}
    assert usages == {"b1", "b2"}


def test_expand_table_groups_is_a_no_op_without_a_group():
    assert source_write.expand_table_groups(BLOCKS, ["b1", "b2"]) == ["b1", "b2"]


def test_expand_table_groups_returns_reading_order():
    assert source_write.expand_table_groups(BLOCKS, ["b4"]) == ["b3", "b4"]
