from db import repository as repo
from db.schema import init_db
from notes import source_repository as srepo, source_write
from notes.source_models import SourceBlock
from notes.source_repair import repair_recorded_placements
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'provider', 'verification', 'uncertainty', 'geometry', 'written', 'busy', 'activation_rollback'])
async def test_source_recheck_preserves_old_source_and_requires_safe_activation(tmp_path, monkeypatch, failure):
    import hashlib
    from notes.source_recheck import recheck_source_block, activate_rechecked_source
    from notes.source_models import SourceNote, OwnerKind, Disposition
    pdf = tmp_path / 'uploaded.pdf'
    pdf.write_bytes(b'original source')
    db = tmp_path / 'recheck.sqlite'
    init_db(db)
    old = '<table><tr><td>Receivables</td><td></td><td>8,282</td></tr></table>'
    new = old.replace('<td></td>', '<td>-</td>')
    with repo.db_session(db) as conn:
        run = repo.create_run(conn, pdf.name, session_id='s', output_dir=str(tmp_path))
        gen = srepo.begin_generation(conn, run, input_kind='prepared_document',
            source_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(), pages_expected=1)
        srepo.write_blocks(conn, gen, [SourceBlock('table', 'table', 1, old, page=1, source_note_id='n6'),
            SourceBlock('footer', 'paragraph', 2, '<p>1</p>', page=1,
                        owner_kind=OwnerKind.FURNITURE, capture_confidence=0.8)])
        srepo.write_notes(conn, gen, [SourceNote('n6', '6', 'Receivables')])
        srepo.activate_generation(conn, gen, pages_processed=1)
        srepo.record_disposition(conn, run, gen, 'footer', Disposition.EXCLUDED, reason_code='PAGE_NUMBER')
    monkeypatch.setattr('tools.pdf_viewer.render_page_png', lambda *args: b'original-image')
    calls = []
    async def model(_model, stage, images, context):
        assert images == [b'original-image']
        assert context['previous_html'] == old
        calls.append(stage)
        if failure == 'provider':
            raise RuntimeError('provider unavailable')
        if stage == 'recapturing_block':
            return {'complete': True, 'html': new if failure != 'geometry' else '<p>Changed structure</p>'}
        return {'complete': True, 'verified': failure != 'verification',
                'uncertainties': ['unclear sign'] if failure == 'uncertainty' else []}
    if failure in ('provider', 'geometry', 'verification', 'uncertainty'):
        with pytest.raises(RuntimeError if failure == 'provider' else ValueError):
            await recheck_source_block(db_path=db, run_id=run, generation_id=gen,
                block_id='table', pdf_path=pdf, model=None, reason='Check every table cell', _caller=model)
        with repo.db_session(db) as conn:
            assert srepo.active_generation(conn, run)['id'] == gen
        import json
        trace = json.loads(next(tmp_path.glob('source_recheck_*.json')).read_text(encoding='utf-8'))
        if failure == 'provider':
            assert trace['calls'][0]['status'] == 'failed'
            assert trace['calls'][0]['error_type'] == 'RuntimeError'
        else:
            assert trace['calls'][0]['receipt']['complete']
        return
    result = await recheck_source_block(db_path=db, run_id=run, generation_id=gen,
        block_id='table', pdf_path=pdf, model=None, reason='Check every table cell', _caller=model)
    candidate = result['candidate_generation_id']
    with repo.db_session(db) as conn:
        assert srepo.active_generation(conn, run)['id'] == gen
        assert srepo.fetch_blocks(conn, gen)[0]['canonical_html'] == old
        assert srepo.fetch_blocks(conn, candidate)[0]['canonical_html'] == new
        footer = next(b for b in srepo.fetch_blocks(conn, candidate) if b['block_id'] == 'footer')
        assert footer['owner_kind'] == 'furniture'
        assert footer['capture_confidence'] == 0.8
        assert calls == ['recapturing_block', 'verifying_block']
        if failure == 'busy':
            import asyncio
            import task_registry
            released = asyncio.Event()
            task = asyncio.create_task(released.wait())
            task_registry.register('s', 'notes:ACC_POLICIES', task)
            try:
                with pytest.raises(ValueError, match='idle extraction'):
                    activate_rechecked_source(conn, run_id=run, parent_generation_id=gen, candidate_generation_id=candidate)
            finally:
                released.set()
                await task
                task_registry.unregister('s', 'notes:ACC_POLICIES')
        elif failure == 'written':
            source_write.write_cell_from_blocks(conn, run_id=run, generation_id=gen,
                sheet='Notes', row=3, block_ids=['table'])
            with pytest.raises(ValueError, match='pre-write'):
                activate_rechecked_source(conn, run_id=run, parent_generation_id=gen, candidate_generation_id=candidate)
        else:
            activate_rechecked_source(conn, run_id=run, parent_generation_id=gen, candidate_generation_id=candidate)
        if failure == 'activation_rollback':
            conn.rollback()
        assert srepo.active_generation(conn, run)['id'] == (candidate if failure is None else gen)
        if failure is None:
            assert srepo.fetch_usages(conn, candidate)[0]['reason_code'] == 'PAGE_NUMBER'


def test_repair_does_not_move_policy_content_into_other_note_destination(tmp_path):
    db = tmp_path / "db.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run = repo.create_run(conn, "source.pdf", session_id="s", output_dir=str(tmp_path))
        gen = srepo.begin_generation(conn, run, input_kind="prepared_document")
        srepo.write_blocks(conn, gen, [
            SourceBlock("policy", "paragraph", 1, "<p>Policy.</p>", source_note_id="n2"),
            SourceBlock("detail", "paragraph", 2, "<p>Details.</p>", source_note_id="n2"),
            SourceBlock("tail", "paragraph", 3, "<p>More details.</p>", source_note_id="n2"),
        ])
        srepo.activate_generation(conn, gen)
        def write(sheet, row, ids):
            source_write.write_cell_from_blocks(conn, run_id=run, generation_id=gen,
                sheet=sheet, row=row, block_ids=ids)
        write("Policies", 3, ["policy"])
        write("Notes", 4, ["detail", "tail"])
        write("Notes", 4, ["detail"])
        assert repair_recorded_placements(conn, run_id=run, generation_id=gen,
                                         missing_block_ids=["tail"]) == 1
        rows = conn.execute("SELECT sheet,html FROM notes_cells WHERE run_id=?", (run,)).fetchall()
        cells = {r["sheet"]: r["html"] for r in rows}
        assert "Policy." in cells["Policies"] and "details" not in cells["Policies"]
        assert "More details." in cells["Notes"] and "Policy." not in cells["Notes"]


def test_corrupted_source_cell_repairs_from_existing_placements(tmp_path):
    from notes.integrity import run_checks, missing_block_ids
    from notes.integrity_runner import build_input

    db = tmp_path / "db.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run = repo.create_run(conn, "source.pdf", session_id="s", output_dir=str(tmp_path))
        gen = srepo.begin_generation(conn, run, input_kind="prepared_document")
        srepo.write_blocks(conn, gen, [SourceBlock("a", "paragraph", 1, "<p>Complete text.</p>")])
        srepo.activate_generation(conn, gen)
        source_write.write_cell_from_blocks(conn, run_id=run, generation_id=gen,
            sheet="Notes", row=3, block_ids=["a"])
        conn.execute("UPDATE notes_cells SET html='<p>Short</p>' WHERE run_id=?", (run,))
        conn.commit()
        before = run_checks(build_input(conn, run, gen))
        ids = missing_block_ids(before)
        assert "a" in ids
        assert repair_recorded_placements(conn, run_id=run, generation_id=gen,
                                         missing_block_ids=ids) == 1
        html = conn.execute("SELECT html FROM notes_cells WHERE run_id=?", (run,)).fetchone()[0]
        assert html == "<p>Complete text.</p>"


def test_corrupted_human_cell_is_never_automatically_repaired(tmp_path):
    from notes import lineage
    db = tmp_path / "db.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run = repo.create_run(conn, "source.pdf", session_id="s", output_dir=str(tmp_path))
        gen = srepo.begin_generation(conn, run, input_kind="prepared_document")
        srepo.write_blocks(conn, gen, [SourceBlock("a", "paragraph", 1, "<p>Source.</p>")])
        srepo.activate_generation(conn, gen)
        source_write.write_cell_from_blocks(conn, run_id=run, generation_id=gen,
            sheet="Notes", row=3, block_ids=["a"])
        lineage.mark_human_edit(conn, run, "Notes", 3, "<p>Human.</p>")
        assert repair_recorded_placements(conn, run_id=run, generation_id=gen,
                                         missing_block_ids=["a"]) == 0
