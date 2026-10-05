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


def test_prepared_source_remains_clean_after_formatter_patch(run):
    from notes.format_patch import apply_sheet_patch

    conn, run_id, gen, _ = run
    conn.execute("UPDATE notes_source_generations SET input_kind='prepared_document' WHERE id=?", (gen,))
    _write(conn, run_id, gen, ["b1", "b2"])
    before = conn.execute("SELECT html FROM notes_cells WHERE run_id=?", (run_id,)).fetchone()[0]
    formatted = apply_sheet_patch({10: before}, {"cells": [{"row": 10, "operations": [
        {"target": {"blocks": "all"}, "style": {"bold": True}},
    ]}]}).rows[10]
    assert formatted != before
    assert repo.cas_update_notes_cell_html(conn, run_id=run_id, sheet="Notes", row=10,
        expected_html=before, new_html=formatted, style_source="formatter")
    assert _verdict(conn, run_id, gen).findings == []

    # A stale cached digest must still never conceal actual content loss.
    conn.execute("UPDATE notes_cells SET html='<p>one</p>' WHERE run_id=?", (run_id,))
    assert any(f.check == "render_match" for f in _verdict(conn, run_id, gen).findings)


def test_sheet_persistence_keeps_source_lineage_after_style_only_patch(run):
    from notes.format_patch import apply_sheet_patch
    from notes.persistence import persist_notes_cells

    conn, run_id, gen, db = run
    conn.execute("UPDATE notes_source_generations SET input_kind='prepared_document' WHERE id=?", (gen,))
    _write(conn, run_id, gen, ["b1", "b2"])
    before = conn.execute("SELECT html FROM notes_cells WHERE run_id=?", (run_id,)).fetchone()[0]
    styled = apply_sheet_patch({10: before}, {"cells": [{"row": 10, "operations": [
        {"target": {"blocks": "all"}, "style": {"bold": True}},
    ]}]}).rows[10]
    conn.commit()
    persist_notes_cells(db_path=str(db), run_id=run_id, sheet_name="Notes",
                        cells_written=[{"sheet": "Notes", "row": 10,
                                        "label": "L", "html": styled}])
    cell = conn.execute("SELECT source_generation_id,source_rendered_sha256 "
                        "FROM notes_cells WHERE run_id=?", (run_id,)).fetchone()
    assert cell[0] == gen and cell[1]
    assert _verdict(conn, run_id, gen).findings == []


@pytest.mark.parametrize(("source", "changed"), [
    ("<p>First. Second.</p><p>Third.</p>", "<p>First.</p><p>Second. Third.</p>"),
    ("<h3>Revenue recognition</h3><p>Policy details.</p>",
     "<h3>Revenue</h3><p>recognition Policy details.</p>"),
    ("<p>First<br>Second</p>", "<p>First Second</p>"),
    ("<table><tr><td>A | B</td><td>C</td></tr></table>",
     "<table><tr><td>A</td><td>B | C</td></tr></table>"),
])
def test_prepared_source_structure_changes_cannot_verify_clean(run, source, changed):
    conn, run_id, gen, _ = run
    conn.execute("UPDATE notes_source_generations SET input_kind='prepared_document' WHERE id=?", (gen,))
    conn.execute("UPDATE notes_source_blocks SET canonical_html=? WHERE generation_id=? AND block_id='b1'",
                 (source, gen))
    _write(conn, run_id, gen, ["b1", "b2"])
    assert _verdict(conn, run_id, gen).findings == []
    conn.execute("UPDATE notes_cells SET html=? WHERE run_id=?", (changed + "<p>two</p>", run_id))
    result = _verdict(conn, run_id, gen)
    assert result.requires_review
    assert any(f.check == "render_match" for f in result.findings)


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


def test_a_routed_block_with_a_live_destination_settles(run):
    conn, run_id, gen, _db = run
    _write(conn, run_id, gen, ["b1"])
    srepo.record_disposition(
        conn, run_id, gen, "b2", Disposition.ROUTED,
        sheet="Policies", row=4,
    )
    source_write.write_cell_from_blocks(conn, run_id=run_id, generation_id=gen,
        sheet="Policies", row=4, block_ids=["b2"], disposition=Disposition.ROUTED)
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


def test_routed_receipt_cannot_replace_actual_policy_content(run):
    conn, run_id, gen, _ = run
    _write(conn, run_id, gen, ["b1"])
    srepo.record_disposition(conn, run_id, gen, "b2", Disposition.ROUTED,
                             sheet="Policies", row=4)
    assert _verdict(conn, run_id, gen).requires_review


def test_direct_html_overwrite_cannot_hide_behind_stored_digest(run):
    conn, run_id, gen, _ = run
    _write(conn, run_id, gen, ["b1", "b2"])
    conn.execute("UPDATE notes_cells SET html = '<p>shortened</p>' WHERE run_id = ?", (run_id,))
    assert any(f.check == "render_match" for f in _verdict(conn, run_id, gen).findings)


def test_verified_repeated_page_heading_is_a_supported_exclusion():
    """A broad note range may contain an actual running header on many pages."""
    blocks = [
        SourceBlock(f"h{page}", "paragraph", page,
                    "<p>Notes to the financial statements</p>", page=page,
                    source_note_id="n1", owner_kind=OwnerKind.NOTE,
                    locator={"reason": "PAGE_HEADER", "region_kind": "page_header"})
        for page in (1, 2, 3)
    ]
    inp = integrity.IntegrityInput(
        blocks=blocks,
        notes=[SourceNote("n1", "1", block_ids=[b.block_id for b in blocks])],
        usages={b.block_id: {"disposition": "excluded", "reason_code": "PAGE_HEADER"}
                for b in blocks},
        verified_inventory=True,
    )
    assert integrity.check_dispositions(inp) == []
    assert integrity.check_prose_note_coverage(inp) == []


def test_one_off_note_heading_cannot_be_hidden_as_page_furniture():
    block = SourceBlock(
        "heading", "paragraph", 1, "<p>Material accounting policy</p>",
        page=1, source_note_id="n1", owner_kind=OwnerKind.NOTE,
    )
    inp = integrity.IntegrityInput(
        blocks=[block], notes=[SourceNote("n1", "1", block_ids=["heading"])],
        usages={"heading": {"disposition": "excluded", "reason_code": "PAGE_HEADER"}},
        verified_inventory=True,
    )
    assert any(f.block_ids == ["heading"] for f in integrity.check_dispositions(inp))
    assert any(f.block_ids == ["heading"] for f in integrity.check_prose_note_coverage(inp))


def test_repeated_edge_caption_with_table_relationship_remains_unresolved():
    blocks = [
        SourceBlock(f"caption{page}", "paragraph", page * 10,
                    "<p>RM'000</p>", page=page, source_note_id="n1",
                    owner_kind=OwnerKind.NOTE,
                    locator={"reason": "PAGE_HEADER", "region_kind": "table_caption",
                             "required_related_block_ids": [f"table{page}"]})
        for page in (1, 2, 3)
    ]
    inp = integrity.IntegrityInput(
        blocks=blocks,
        notes=[SourceNote("n1", "1", block_ids=[b.block_id for b in blocks])],
        usages={b.block_id: {"disposition": "excluded", "reason_code": "PAGE_HEADER"}
                for b in blocks},
        verified_inventory=True,
    )
    assert {bid for finding in integrity.check_prose_note_coverage(inp)
            for bid in finding.block_ids} == {b.block_id for b in blocks}


def test_repeated_substantive_block_in_middle_of_seven_is_not_furniture():
    blocks = []
    for page in (1, 2, 3):
        for index in range(7):
            target = index == 3
            blocks.append(SourceBlock(
                f"p{page}-{index}", "paragraph", page * 10 + index,
                "<p>Recurring disclosure amount</p>" if target else f"<p>Content {page}-{index}</p>",
                page=page, source_note_id="n1" if target else None,
                owner_kind=OwnerKind.NOTE if target else OwnerKind.FURNITURE,
                locator={"reason": "PAGE_HEADER", "region_kind": "page_header"} if target else {},
            ))
    omitted = [b for b in blocks if b.owner_kind is OwnerKind.NOTE]
    inp = integrity.IntegrityInput(
        blocks=blocks, notes=[SourceNote("n1", "1", block_ids=[b.block_id for b in omitted])],
        usages={b.block_id: {"disposition": "excluded", "reason_code": "PAGE_HEADER"}
                for b in omitted}, verified_inventory=True,
    )
    assert {bid for finding in integrity.check_prose_note_coverage(inp)
            for bid in finding.block_ids} == {b.block_id for b in omitted}


def test_repeated_middle_page_note_text_cannot_be_hidden_as_header():
    blocks = [
        SourceBlock(f"before{page}", "paragraph", page * 10,
                    "<p>Opening content</p>", page=page,
                    owner_kind=OwnerKind.FURNITURE)
        for page in (1, 2, 3)
    ] + [
        SourceBlock(f"h{page}", "paragraph", page * 10 + 5,
                    "<p>Repeated disclosure</p>", page=page,
                    source_note_id="n1", owner_kind=OwnerKind.NOTE)
        for page in (1, 2, 3)
    ]
    for page in (1, 2, 3):
        blocks.extend(
            SourceBlock(f"lead{page}-{index}", "paragraph", page * 10 + index,
                        f"<p>Line {index}</p>", page=page,
                        owner_kind=OwnerKind.FURNITURE)
            for index in range(1, 5)
        )
    inp = integrity.IntegrityInput(
        blocks=blocks,
        notes=[SourceNote("n1", "1", block_ids=[f"h{page}" for page in (1, 2, 3)])],
        usages={f"h{page}": {"disposition": "excluded", "reason_code": "PAGE_HEADER"}
                for page in (1, 2, 3)},
        verified_inventory=True,
    )
    unresolved = {bid for finding in integrity.check_dispositions(inp)
                  for bid in finding.block_ids}
    assert {"h1", "h2", "h3"} <= unresolved


def test_integrity_snapshot_keeps_page_for_furniture_verification(run):
    conn, run_id, gen, _ = run
    conn.execute("UPDATE notes_source_blocks SET page=7 WHERE generation_id=? AND block_id='b1'",
                 (gen,))
    inp = integrity_runner.build_input(conn, run_id, gen, scout_available=True)
    assert next(block.page for block in inp.blocks if block.block_id == "b1") == 7


def test_forged_placement_without_rendered_content_cannot_settle_block(run):
    conn, run_id, gen, _ = run
    _write(conn, run_id, gen, ["b1"])
    srepo.record_disposition(conn, run_id, gen, "b2", Disposition.INCLUDED, sheet="Notes", row=10)
    srepo.set_cell_placements(conn, run_id, gen, "Notes", 10, ["b1", "b2"])
    conn.commit()
    assert any(f.check == "render_match" for f in _verdict(conn, run_id, gen).findings)


def test_prepared_note_prose_cannot_be_dismissed_as_page_furniture(run):
    conn, run_id, gen, _ = run
    conn.execute("UPDATE notes_source_generations SET input_kind='prepared_document' WHERE id=?", (gen,))
    conn.commit()
    _write(conn, run_id, gen, ["b1"])
    srepo.record_disposition(conn, run_id, gen, "b2", Disposition.EXCLUDED, reason_code="PAGE_FOOTER")
    result = _verdict(conn, run_id, gen)
    assert result.requires_review
    assert any("cannot be settled" in f.message for f in result.findings)


def test_approved_banner_cleanup_cannot_conceal_later_content_loss(run):
    from notes.cleanup_patch import Patch, Removal
    from notes.cleanup_repository import save_cleanup
    conn,rid,gen,_=run
    conn.execute("UPDATE notes_source_generations SET input_kind='prepared_document' WHERE id=?",(gen,))
    conn.execute("UPDATE notes_source_blocks SET canonical_html='<h1>COMPANY SDN. BHD.</h1>',block_kind='heading' WHERE generation_id=? AND block_id='b1'",(gen,))
    _write(conn,rid,gen,['b1','b2'])
    cells={'Notes:10':dict(conn.execute('SELECT * FROM notes_cells WHERE run_id=?',(rid,)).fetchone())}
    original_blocks=[tuple(r) for r in conn.execute('SELECT * FROM notes_source_blocks WHERE generation_id=?',(gen,))]
    patch=Patch(inspected_cells=['Notes:10'],removals=[Removal(cell='Notes:10',block=0,expected_text='COMPANY SDN. BHD.',
                reason='page_banner',evidence_page=1,justification='Printed company page banner above the note.')])
    conn.commit()
    save_cleanup(conn,run_id=rid,cells=cells,patch=patch,viewed_pages={1},model='test')
    assert _verdict(conn,rid,gen).findings==[]
    assert [tuple(r) for r in conn.execute('SELECT * FROM notes_source_blocks WHERE generation_id=?',(gen,))]==original_blocks
    # Stored baseline hashes and an approved receipt cannot bless unrelated loss.
    conn.execute("UPDATE notes_cells SET html='<p>missing disclosure</p>' WHERE run_id=?",(rid,))
    assert any(f.check=='render_match' for f in _verdict(conn,rid,gen).findings)
    # Tampering with only the saved after HTML does not bypass patch replay.
    conn.execute("UPDATE notes_cleanup_receipts SET after_html='<p>missing disclosure</p>' WHERE run_id=?",(rid,))
    assert any(f.check=='render_match' for f in _verdict(conn,rid,gen).findings)


def test_sheet_persistence_keeps_approved_cleanup_lineage_after_style_change(run):
    from notes.cleanup_patch import Patch, Removal
    from notes.cleanup_repository import save_cleanup
    from notes.persistence import persist_notes_cells
    conn,rid,gen,db=run
    conn.execute("UPDATE notes_source_generations SET input_kind='prepared_document' WHERE id=?",(gen,))
    conn.execute("UPDATE notes_source_blocks SET canonical_html='<h1>COMPANY SDN. BHD.</h1>',block_kind='heading' WHERE generation_id=? AND block_id='b1'",(gen,))
    _write(conn,rid,gen,['b1','b2'])
    cells={'Notes:10':dict(conn.execute('SELECT * FROM notes_cells WHERE run_id=?',(rid,)).fetchone())}
    conn.commit()
    save_cleanup(conn,run_id=rid,cells=cells,patch=Patch(inspected_cells=['Notes:10'],removals=[Removal(cell='Notes:10',block=0,
        expected_text='COMPANY SDN. BHD.',reason='page_banner',evidence_page=1,justification='Printed running company banner.')]),viewed_pages={1},model='test')
    persist_notes_cells(db_path=str(db),run_id=rid,sheet_name='Notes',
                       cells_written=[{'sheet':'Notes','row':10,'label':'L','html':'<p style="font-size:12px">two</p>'}])
    assert conn.execute('SELECT source_generation_id FROM notes_cells WHERE run_id=?',(rid,)).fetchone()[0]==gen
    assert _verdict(conn,rid,gen).findings==[]


@pytest.mark.parametrize('legacy_first_pass', [False, True])
def test_repeated_cleanup_retains_original_source_evidence_across_formatting(run, legacy_first_pass):
    import json
    from notes.cleanup_patch import Patch, Removal
    from notes.cleanup_repository import save_cleanup
    from notes.persistence import persist_notes_cells

    conn, rid, gen, db = run
    conn.execute("UPDATE notes_source_generations SET input_kind='prepared_document' WHERE id=?", (gen,))
    original = '<h1>COMPANY SDN. BHD.</h1><h2>Notes to the financial statements</h2>'
    conn.execute("UPDATE notes_source_blocks SET canonical_html=?,block_kind='heading' "
                 "WHERE generation_id=? AND block_id='b1'", (original, gen))
    _write(conn, rid, gen, ['b1', 'b2'])
    conn.commit()

    for text in ['COMPANY SDN. BHD.', 'Notes to the financial statements']:
        cells = {'Notes:10': dict(conn.execute('SELECT * FROM notes_cells WHERE run_id=?', (rid,)).fetchone())}
        patch = Patch(inspected_cells=['Notes:10'], removals=[Removal(
            cell='Notes:10', block=0, expected_text=text, reason='page_banner',
            evidence_page=1, justification='Printed running page banner.')])
        save_cleanup(conn, run_id=rid, cells=cells, patch=patch, viewed_pages={1}, model='test')
        assert _verdict(conn, rid, gen).findings == []
        if text == 'COMPANY SDN. BHD.':
            if legacy_first_pass:
                history = conn.execute('SELECT patch_json FROM notes_cleanup_receipts WHERE run_id=?', (rid,)).fetchone()[0]
                conn.execute('UPDATE notes_cleanup_receipts SET patch_json=? WHERE run_id=?',
                             (json.dumps(json.loads(history)[0]['patch']), rid))
                conn.commit()
            persist_notes_cells(db_path=str(db), run_id=rid, sheet_name='Notes', cells_written=[{
                'sheet': 'Notes', 'row': 10, 'label': 'L',
                'html': '<h2 style="font-size:12px">Notes to the financial statements</h2><p>two</p>',
            }])

    receipt = conn.execute('SELECT before_html FROM notes_cleanup_receipts WHERE run_id=?', (rid,)).fetchone()
    assert receipt[0].startswith(original)
    conn.execute("UPDATE notes_cells SET html='<p>Lost disclosure.</p>' WHERE run_id=?", (rid,))
    assert _verdict(conn, rid, gen).findings
