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


def test_a_validated_source_write_stamps_the_registry_identity(conn_gen):
    conn, run_id, gen = conn_gen
    conn.execute(
        "INSERT INTO notes_nodes(node_uuid, template_id, sheet, row, label, kind, "
        "slot_role) VALUES ('registry-11', 'mfrs-company-notes-v1', "
        "'Notes', 11, 'Receivables', 'LEAF', 'INPUT')"
    )
    source_write.write_cell_from_blocks(
        conn,
        run_id=run_id,
        generation_id=gen,
        sheet="Notes",
        row=11,
        block_ids=["b1", "b2"],
        template_prefix="mfrs-company-",
    )

    stored = conn.execute(
        "SELECT concept_uuid FROM notes_cells WHERE run_id = ? AND row = 11",
        (run_id,),
    ).fetchone()
    assert stored["concept_uuid"] == "registry-11"


def test_naming_half_a_split_table_pulls_in_the_rest(conn_gen):
    """Rendering half a table and calling the note complete is exactly what
    the table group exists to prevent."""
    conn, run_id, gen = conn_gen
    out = source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b3"],
    )
    assert out.block_ids == ["b3", "b4"]
    assert any("verified continuation" in w for w in out.warnings)


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


def test_substantive_source_blocks_cannot_be_placed_in_two_rows(conn_gen):
    """The Amgen reproduction: one prepared-source disclosure was accepted
    for two policy rows and exported twice. The second write must fail before
    it changes the destination cell or placement ledger."""
    conn, run_id, gen = conn_gen
    repo.upsert_notes_cell(
        conn, run_id=run_id, sheet="Notes", row=11, label="Other policy",
        html="", evidence=None, source_pages=[],
    )
    source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b1", "b2"],
    )

    with pytest.raises(source_write.SourcePlacementConflict) as caught:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=11,
            block_ids=["b1", "b2"],
        )

    conflict = caught.value
    assert conflict.target == source_write.PlacementCandidate("Notes", 11, "")
    assert conflict.existing == (
        source_write.PlacementCandidate("Notes", 10, "", 2),
    )
    assert "provisional" in str(conflict)

    destination = conn.execute(
        "SELECT html FROM notes_cells WHERE run_id=? AND sheet='Notes' AND row=11",
        (run_id,),
    ).fetchone()
    assert destination["html"] == ""
    assert {
        (p["sheet"], p["row"])
        for p in srepo.active_placements(conn, gen)
    } == {("Notes", 10)}


def test_identical_source_render_from_distinct_blocks_is_refused(conn_gen):
    """Block identity alone is insufficient: duplicated prepared-source
    capture can assign different ids to the same substantive render."""
    conn, run_id, gen = conn_gen
    repeated = "<p>The same substantive accounting policy.</p>"
    srepo.write_blocks(conn, gen, [
        SourceBlock("copy-1", "paragraph", 1, repeated),
        SourceBlock("copy-2", "paragraph", 2, repeated),
    ])
    repo.upsert_notes_cell(
        conn, run_id=run_id, sheet="Notes", row=11, label="Other policy",
        html="", evidence=None, source_pages=[],
    )
    source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["copy-1"],
    )

    with pytest.raises(source_write.SourcePlacementConflict) as caught:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=11,
            block_ids=["copy-2"],
        )
    assert caught.value.match_kind == "same_render"


def test_heading_context_may_repeat_across_rows(conn_gen):
    """A shared ancestor heading is context, not duplicated disclosure
    content. Different policy paragraphs beneath it remain independently
    placeable."""
    conn, run_id, gen = conn_gen
    srepo.write_blocks(conn, gen, [
        SourceBlock("heading", "heading", 1, "<h3>Material policies</h3>"),
        SourceBlock(
            "tax", "paragraph", 2, "<p>Income tax policy.</p>",
            locator={"heading_ancestor_ids": ["heading"]},
        ),
        SourceBlock(
            "benefits", "paragraph", 3, "<p>Employee benefits policy.</p>",
            locator={"heading_ancestor_ids": ["heading"]},
        ),
    ])
    repo.upsert_notes_cell(
        conn, run_id=run_id, sheet="Notes", row=11, label="Other policy",
        html="", evidence=None, source_pages=[],
    )

    first = source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["tax"],
    )
    second = source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=11,
        block_ids=["benefits"],
    )

    assert first.block_ids == ["heading", "tax"]
    assert second.block_ids == ["heading", "benefits"]


def test_stale_placement_for_a_deleted_cell_does_not_block_write(conn_gen):
    conn, run_id, gen = conn_gen
    source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b2"],
    )
    conn.execute(
        "DELETE FROM notes_cells WHERE run_id=? AND sheet='Notes' AND row=10",
        (run_id,),
    )
    repo.upsert_notes_cell(
        conn, run_id=run_id, sheet="Notes", row=11, label="Replacement",
        html="", evidence=None, source_pages=[],
    )

    outcome = source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=11,
        block_ids=["b2"],
    )

    assert outcome.block_ids == ["b2"]


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


def test_continuation_selection_preserves_all_pages_and_heading_context(conn_gen):
    conn, run_id, gen = conn_gen
    blocks = [
        SourceBlock("h", "heading", 10, "<h3>Policies</h3>"),
        SourceBlock("p1", "paragraph", 11, "<p>First part</p>",
                    locator={"heading_ancestor_ids": ["h"]}),
        SourceBlock("p2", "paragraph", 12, "<p>second part</p>", continues_block_id="p1"),
        SourceBlock("p3", "paragraph", 13, "<p>last part</p>", continues_block_id="p2"),
    ]
    srepo.write_blocks(conn, gen, blocks)
    result = source_write.write_cell_from_blocks(conn, run_id=run_id, generation_id=gen,
        sheet="Notes", row=10, block_ids=["p2"])
    assert result.block_ids == ["h", "p1", "p2", "p3"]


def test_missing_verified_continuation_is_refused(conn_gen):
    conn, run_id, gen = conn_gen
    srepo.write_blocks(conn, gen, [SourceBlock("tail", "paragraph", 9,
        "<p>tail</p>", continues_block_id="missing")])
    with pytest.raises(source_write.SourceWriteError, match="unknown block"):
        source_write.write_cell_from_blocks(conn, run_id=run_id, generation_id=gen,
            sheet="Notes", row=10, block_ids=["tail"])


@pytest.mark.parametrize("selected", ["list", "next_item"])
def test_cross_kind_page_relationship_keeps_new_item_outside_previous_list_item(conn_gen, selected):
    from bs4 import BeautifulSoup

    conn, run_id, gen = conn_gen
    blocks = [
        SourceBlock("list", "list", 10,
                    "<ol><li>At the beginning:<ol><li>First finding</li>"
                    "<li>Second finding</li></ol></li></ol>",
                    locator={"required_related_block_ids": ["next_item"]}),
        SourceBlock("next_item", "paragraph", 11,
                    "<p>(b) At date of this report <strong>directors</strong> confirm.</p>",
                    locator={"required_related_block_ids": ["list"]}),
    ]
    srepo.write_blocks(conn, gen, blocks)
    result = source_write.write_cell_from_blocks(conn, run_id=run_id, generation_id=gen,
        sheet="Notes", row=10, block_ids=[selected])
    assert result.block_ids == ["list", "next_item"]
    row = conn.execute("SELECT html FROM notes_cells WHERE run_id = ? AND row = 10", (run_id,)).fetchone()
    soup = BeautifulSoup(row["html"], "html.parser")
    assert len(soup.find_all("li")) == 3
    paragraph = soup.find("p")
    assert paragraph.get_text() == "(b) At date of this report directors confirm."
    assert paragraph.find_parent("li") is None
    assert paragraph.strong.get_text() == "directors"


def test_automatic_relink_does_not_overwrite_human_edit(conn_gen):
    conn, run_id, gen = conn_gen
    source_write.write_cell_from_blocks(conn, run_id=run_id, generation_id=gen,
        sheet="Notes", row=10, block_ids=["b2"])
    lineage.mark_human_edit(conn, run_id, "Notes", 10, "<p>Human correction</p>")
    with pytest.raises(source_write.SourceWriteError, match="human edit"):
        source_write.write_cell_from_blocks(conn, run_id=run_id, generation_id=gen,
            sheet="Notes", row=10, block_ids=["b1", "b2"])


def test_manual_source_attachment_can_replace_the_users_edit(conn_gen):
    conn, run_id, gen = conn_gen
    source_write.write_cell_from_blocks(conn, run_id=run_id, generation_id=gen,
        sheet="Notes", row=10, block_ids=["b2"])
    lineage.mark_human_edit(conn, run_id, "Notes", 10, "<p>Human correction</p>")
    outcome = source_write.write_cell_from_blocks(conn, run_id=run_id, generation_id=gen,
        sheet="Notes", row=10, block_ids=["b1", "b2"], actor="human")
    assert outcome.block_ids == ["b1", "b2"]
    assert lineage.read_lineage(conn, run_id, "Notes", 10).diverged is False
