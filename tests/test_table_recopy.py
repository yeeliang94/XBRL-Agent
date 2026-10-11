"""A wrongly copied source table is re-copied from the PDF, never patched in place.

Preparation re-reads the page with the review finding; only that table's
structure may change, an independent check must confirm it, and the source
block plus every note built from it are corrected together.
"""
import hashlib
import json

import fitz
from pathlib import Path
import pytest

from ingest.document_preparation import (
    PREPARATION_NAME, PreparationError, recapture_table, record_table_correction,
)

HEAD = "<p>Deferred tax</p>"
BAD = ("<table><tr><th></th><th>2025</th></tr><tr><td>Lease</td><td>2,898</td></tr>"
       "<tr><th></th><th>12,856</th></tr></table>")
GOOD = ("<table><tr><th></th><th>2025</th></tr><tr><td>Lease</td><td>2,898</td></tr>"
        "<tr><td></td><td>12,856</td></tr></table>")
PAGE = HEAD + BAD


def _pdf(tmp_path):
    path = tmp_path / "uploaded.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "Deferred tax")
        doc.save(path)
    return path


def _caller(page_html=HEAD + GOOD, verified=True, structure=True):
    calls = []

    async def call(stage, images, context):
        calls.append((stage, context))
        if stage == "capturing":
            return {"html": page_html, "complete": True, "readable": True}
        return {"verified": verified, "complete": True, "readable": True,
                "table_structure_verified": structure, "table_relationships": []}
    call.calls = calls
    return call


@pytest.mark.asyncio
async def test_recopy_returns_the_corrected_table_after_independent_check(tmp_path):
    call = _caller()
    result = await recapture_table(_pdf(tmp_path), page_number=1, page_html=PAGE, block_index=1,
                                   finding="Total row captured as heading cells.", model=None, _caller=call)
    assert result["html"] == GOOD
    stages = [stage for stage, _ in call.calls]
    assert stages == ["capturing", "verifying"]
    # The finding reaches both preparation prompts as the repair issue.
    assert "Total row captured" in call.calls[0][1]["issues"][0]
    assert call.calls[1][1]["previous_findings"] == call.calls[0][1]["issues"]


@pytest.mark.parametrize("page_html,verified,message", [
    ("<p>Deferred taxes</p>" + GOOD, True, "outside the named table"),
    (HEAD + GOOD.replace("12,856", "12,865"), True, "words or figures"),
    (PAGE, True, "did not change the table's structure"),
    (HEAD + GOOD.replace("<tr><td></td><td>12,856</td></tr>", "<tr><td>12,856</td></tr>"), True, "shape check"),
    (HEAD + GOOD, False, "did not confirm"),
])
@pytest.mark.asyncio
async def test_recopy_gates_refuse_anything_but_a_confirmed_structure_fix(
        tmp_path, page_html, verified, message):
    with pytest.raises(PreparationError, match=message):
        await recapture_table(_pdf(tmp_path), page_number=1, page_html=PAGE, block_index=1,
                              finding="x", model=None, _caller=_caller(page_html, verified))


def test_correction_is_written_to_the_prepared_document(tmp_path):
    html_file = tmp_path / "prepared-x.html"
    html_file.write_text("old")
    blocks = [{"block_id": "p1-b0", "page": 1, "canonical_html": HEAD, "locator": {"page_block": 0}},
              {"block_id": "p1-b1", "page": 1, "canonical_html": BAD, "locator": {"page_block": 1}}]
    data = {"html_file": html_file.name, "html_sha256": "stale", "blocks": blocks,
            "pages": [{"page": 1, "html": PAGE, "blocks": json.loads(json.dumps(blocks))}]}
    (tmp_path / PREPARATION_NAME).write_text(json.dumps(data))
    record_table_correction(tmp_path / PREPARATION_NAME, block_id="p1-b1", table_html=GOOD,
                            record={"finding": "x"})
    saved = json.loads((tmp_path / PREPARATION_NAME).read_text())
    assert saved["pages"][0]["html"] == HEAD + GOOD
    assert saved["blocks"][1]["canonical_html"] == GOOD == saved["pages"][0]["blocks"][1]["canonical_html"]
    assert GOOD in html_file.read_text()
    assert saved["html_sha256"] == hashlib.sha256(html_file.read_bytes()).hexdigest()
    assert saved["table_corrections"] == [{"block_id": "p1-b1", "finding": "x"}]


# --- notes side: correct the source block, rebuild every note built from it --

SHEET = "Notes-Listofnotes"


@pytest.fixture
def recopy_run(tmp_path):
    from db import repository as repo
    from db.schema import init_db
    from notes import source_repository as sources, source_write
    from notes.source_models import OwnerKind, SourceBlock, SourceNote

    db_path = tmp_path / "audit.sqlite"
    init_db(db_path)
    pdf_path = _pdf(tmp_path)
    blocks = [SourceBlock(block_id="p1-b0", block_kind="paragraph", reading_order=0, canonical_html=HEAD,
                          page=1, source_note_id="n71", owner_kind=OwnerKind.NOTE),
              SourceBlock(block_id="p1-b1", block_kind="table", reading_order=1, canonical_html=BAD,
                          page=1, source_note_id="n71", owner_kind=OwnerKind.NOTE)]
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "uploaded.pdf", session_id="s", output_dir=str(tmp_path))
        generation = sources.begin_generation(conn, run_id, input_kind="prepared_document")
        sources.write_blocks(conn, generation, blocks)
        sources.write_notes(conn, generation, [SourceNote(source_note_id="n71", top_note_num="71",
                                                          title="Deferred tax", block_ids=["p1-b0", "p1-b1"])])
        sources.activate_generation(conn, generation)
        source_write.write_cell_from_blocks(conn, run_id=run_id, generation_id=generation, sheet=SHEET,
                                            row=71, block_ids=["p1-b0", "p1-b1"], label="Deferred tax")
    prepared = [{"block_id": b.block_id, "page": 1, "canonical_html": b.canonical_html,
                 "locator": {"page_block": i}} for i, b in enumerate(blocks)]
    html_file = tmp_path / "prepared-x.html"
    html_file.write_text(PAGE)
    (tmp_path / PREPARATION_NAME).write_text(json.dumps({
        "html_file": html_file.name, "html_sha256": "", "blocks": prepared,
        "pages": [{"page": 1, "html": PAGE, "rotation": 0, "blocks": prepared}]}))
    return db_path, str(pdf_path), run_id


async def _fake_recapture(pdf_path, **kwargs):
    assert kwargs["block_index"] == 1 and kwargs["page_html"] == PAGE
    return {"html": GOOD, "verification": {"verified": True}, "usage": {"prompt_tokens": 7}}


@pytest.mark.asyncio
async def test_recopy_corrects_the_source_and_rebuilds_the_note(recopy_run):
    from db import repository as repo
    from notes.integrity_runner import build_input
    from notes.table_recopy import recopy_tables

    db_path, pdf_path, run_id = recopy_run
    result = await recopy_tables(run_id=run_id, db_path=str(db_path), pdf_path=pdf_path, sheet=SHEET,
                                 findings={71: [(0, "Total captured as heading cells.")]}, model=None,
                                 _recapture=_fake_recapture)
    assert result["rebuilt_rows"] == [71] and not result["errors"]
    assert result["usage"]["prompt_tokens"] == 7
    with repo.db_session(db_path) as conn:
        cell = conn.execute("SELECT * FROM notes_cells WHERE row=71").fetchone()
        block = conn.execute("SELECT canonical_html FROM notes_source_blocks WHERE block_id='p1-b1'").fetchone()
        revision = conn.execute("SELECT * FROM notes_source_block_revisions").fetchone()
        assert block[0] == GOOD and "<td>12,856</td>" in cell["html"]
        assert cell["style_source"] == "unstyled"
        assert revision["before_html"] == BAD and revision["after_html"] == GOOD
        # The rebuilt note still matches its (corrected) source exactly.
        generation = conn.execute("SELECT id FROM notes_source_generations").fetchone()[0]
        source_cell = next(c for c in build_input(conn, run_id, generation).cells if c.row == 71)
        assert source_cell.selection_matches_content is True
    saved = json.loads((Path(pdf_path).parent / PREPARATION_NAME).read_text())
    assert saved["blocks"][1]["canonical_html"] == GOOD


@pytest.mark.parametrize("setup,message", [
    ("UPDATE notes_cells SET style_source='human'", "edited after it was copied"),
    ("UPDATE notes_cells SET html=html||'<p>Reviewer addition.</p>'", "no longer matches its source"),
    ("UPDATE notes_source_blocks SET table_group_id='g1' WHERE block_id='p1-b1'", "continued across pages"),
    ("INSERT INTO notes_block_placements(run_id, generation_id, block_id, sheet, row, active, created_at) "
     "SELECT run_id, generation_id, block_id, 'Notes-Issuedcapital', 5, 1, created_at "
     "FROM notes_block_placements WHERE block_id='p1-b1'", "used on another notes sheet"),
])
@pytest.mark.asyncio
async def test_ineligible_tables_stay_flagged_and_unchanged(recopy_run, setup, message):
    from db import repository as repo
    from notes.table_recopy import recopy_tables

    db_path, pdf_path, run_id = recopy_run
    with repo.db_session(db_path) as conn:
        conn.execute(setup)
        conn.commit()
        before = conn.execute("SELECT html FROM notes_cells WHERE row=71").fetchone()[0]

    async def never(*_args, **_kwargs):
        raise AssertionError("an ineligible table must not be re-copied")

    result = await recopy_tables(run_id=run_id, db_path=str(db_path), pdf_path=pdf_path, sheet=SHEET,
                                 findings={71: [(0, "x")]}, model=None, _recapture=never)
    assert result["rebuilt_rows"] == [] and message in result["errors"][71]
    with repo.db_session(db_path) as conn:
        assert conn.execute("SELECT html FROM notes_cells WHERE row=71").fetchone()[0] == before
        assert conn.execute("SELECT canonical_html FROM notes_source_blocks WHERE block_id='p1-b1'").fetchone()[0] == BAD


@pytest.mark.asyncio
async def test_a_concurrent_edit_cancels_the_whole_correction(recopy_run):
    from db import repository as repo
    from notes.table_recopy import recopy_tables

    db_path, pdf_path, run_id = recopy_run

    async def edit_during(pdf, **kwargs):
        with repo.db_session(db_path) as conn:
            conn.execute("UPDATE notes_cells SET content_revision=content_revision+1 WHERE row=71")
            conn.commit()
        return await _fake_recapture(pdf, **kwargs)

    result = await recopy_tables(run_id=run_id, db_path=str(db_path), pdf_path=pdf_path, sheet=SHEET,
                                 findings={71: [(0, "x")]}, model=None, _recapture=edit_during)
    assert result["rebuilt_rows"] == [] and "changed during" in result["errors"][71]
    with repo.db_session(db_path) as conn:
        assert conn.execute("SELECT canonical_html FROM notes_source_blocks WHERE block_id='p1-b1'").fetchone()[0] == BAD
        assert conn.execute("SELECT COUNT(*) FROM notes_source_block_revisions").fetchone()[0] == 0
