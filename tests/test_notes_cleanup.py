"""Final notes cleanup preserves disclosure content and rejects unsafe proposals."""
import pytest
from notes.cleanup_patch import Patch, Removal, apply_patch, enumerate_blocks

def op(cell='x',block=0,text='COMPANY SDN. BHD.',reason='page_banner',retained=None,page=1):
    return Removal(cell=cell,block=block,expected_text=text,reason=reason,evidence_page=page,
                   justification='Printed page header, separate from the disclosure.',retained_block=retained)

def test_banner_removed_but_every_disclosure_style_and_table_survives():
    html='<h1>COMPANY SDN. BHD.</h1><h3>13. Inventories</h3><p><b>Cost RM1,000</b></p><table style="width:100%"><tr><td>2025</td><td>1,000</td></tr></table>'
    result=apply_patch({'x':{'html':html}},Patch(inspected_cells=['x'],removals=[op()]),{1})['x']
    assert result['html']=='<h3>13. Inventories</h3><p><b>Cost RM1,000</b></p><table style="width:100%"><tr><td>2025</td><td>1,000</td></tr></table>'
    assert result['original_html']==html

@pytest.mark.parametrize('bad',[
    op(block=99),op(text='Changed'),op(page=2),
    op(block=1,text='2025 1,000'),
    op(reason='continuation_heading',retained=None),
])
def test_invalid_targets_leave_original_untouched(bad):
    cells={'x':{'html':'<h1>COMPANY SDN. BHD.</h1><table><tr><td>2025</td><td>1,000</td></tr></table>'}}
    before=cells['x']['html']
    with pytest.raises(ValueError):
        apply_patch(cells,Patch(inspected_cells=['x'],removals=[bad]),{1})
    assert cells['x']['html']==before

def test_continuation_with_different_heading_level_keeps_first_and_body():
    html="<h2>2.8 Income taxes</h2><p>Current tax.</p><h1>2.8 Income taxes (cont'd)</h1><p>Deferred tax.</p>"
    result=apply_patch({'x':{'html':html}},Patch(inspected_cells=['x'],removals=[op(block=2,text="2.8 Income taxes (cont'd)",reason='continuation_heading',retained=0)]),{1})['x']['html']
    assert result=='<h2>2.8 Income taxes</h2><p>Current tax.</p><p>Deferred tax.</p>'

def test_first_standalone_continued_heading_cannot_be_deleted_as_repetition():
    html="<h3>2.8 Income taxes (cont'd)</h3><p>Deferred tax.</p>"
    with pytest.raises(ValueError):
        apply_patch({'x':{'html':html}},Patch(inspected_cells=['x'],removals=[op(text="2.8 Income taxes (cont'd)",reason='continuation_heading',retained=0)]),{1})

def test_noop_is_byte_exact_and_incomplete_review_is_rejected():
    html='<p style=\'font-weight:bold\'>Disclosure.</p>'
    cells={'x':{'html':html}}
    assert apply_patch(cells,Patch(inspected_cells=['x'],removals=[]),set())['x']['html']==html
    with pytest.raises(ValueError):
        apply_patch(cells,Patch(inspected_cells=[],removals=[]),set())


def test_continuation_heading_captured_as_short_paragraph_and_unicode_spaces():
    html="<p>(a) As lessee</p><p>Disclosure body.</p><p>(a)\u3000As lessee (cont'd)</p>"
    result=apply_patch({'x':{'html':html}},Patch(inspected_cells=['x'],removals=[op(block=2,text="(a) As lessee (cont'd)",reason='continuation_heading',retained=0)]),{1})['x']['html']
    assert result=='<p>(a) As lessee</p><p>Disclosure body.</p>'


def test_persistence_keeps_newer_edits_and_updates_style_revert_snapshot(tmp_path):
    from db import repository as repo
    from db.schema import init_db
    from notes.cleanup_repository import save_cleanup
    path=tmp_path/'test.db'
    init_db(path)
    with repo.db_session(path) as conn:
        rid=repo.create_run(conn,'sample.pdf',session_id='s',output_dir=str(tmp_path))
        html='<h1>COMPANY SDN. BHD.</h1><p>Disclosure body.</p>'
        for row in [10,20]:
            repo.upsert_notes_cell(conn,run_id=rid,sheet='Notes',row=row,label='Disclosure',html=html)
        cells={f'Notes:{r["row"]}':dict(r) for r in conn.execute('SELECT * FROM notes_cells WHERE run_id=?',(rid,))}
        conn.execute('INSERT INTO notes_format_snapshots(run_id,sheet,row,html) VALUES(?,?,?,?)',(rid,'Notes',10,html))
        conn.commit()
        conn.execute("UPDATE notes_cells SET html='<p>New human edit.</p>',content_revision=content_revision+1 WHERE row=20")
        conn.commit()
        patch=Patch(inspected_cells=list(cells),removals=[op(cell='Notes:10'),op(cell='Notes:20')])
        result=save_cleanup(conn,run_id=rid,cells=cells,patch=patch,viewed_pages={1},model='test')
        assert result['changed_rows']==1 and result['skipped_cells']==['Notes:20']
        assert conn.execute('SELECT html FROM notes_cells WHERE row=20').fetchone()[0]=='<p>New human edit.</p>'
        assert conn.execute('SELECT html FROM notes_format_snapshots').fetchone()[0]=='<p>Disclosure body.</p>'
        receipt=conn.execute('SELECT before_html,after_html FROM notes_cleanup_receipts').fetchone()
        assert tuple(receipt)==(html,'<p>Disclosure body.</p>')


@pytest.mark.asyncio
async def test_cleanup_lifecycle_records_success_failure_and_cancellation(tmp_path,monkeypatch):
    import asyncio
    from db import repository as repo
    from db.schema import init_db
    from notes import cleanup_agent
    db=tmp_path/'test.db';init_db(db)
    pdf=tmp_path/'uploaded.pdf';pdf.write_bytes(b'%PDF')
    with repo.db_session(db) as conn:
        rid=repo.create_run(conn,'sample.pdf',session_id='s',output_dir=str(tmp_path))
        repo.upsert_notes_cell(conn,run_id=rid,sheet='Notes',row=10,label='Note',html='<h1>COMPANY SDN. BHD.</h1><p>Disclosure.</p>')
    async def proposed(**kwargs):
        assert list(kwargs['cells'])==['Notes:10']
        return Patch(inspected_cells=['Notes:10'],removals=[op(cell='Notes:10')]),{1}
    monkeypatch.setattr(cleanup_agent,'propose_cleanup',proposed)
    progress=[]
    args=dict(run_id=rid,db_path=str(db),pdf_path=str(pdf),sheets=['Notes'],model_name='test',
              model_factory=lambda:'test',output_dir=str(tmp_path),on_progress=lambda *x:progress.append(x))
    result=await cleanup_agent.run_notes_cleanup(**args)
    assert result['ok'] and result['removed_blocks']==1
    assert progress==[(0,1,None),(1,1,1)]
    with repo.db_session(db) as conn:
        assert conn.execute('SELECT status FROM run_agents').fetchone()[0]=='completed'
        # A new extraction, not an unchanged cleanup receipt.
        repo.upsert_notes_cell(conn,run_id=rid,sheet='Notes',row=10,label='Note',html='<h1>COMPANY SDN. BHD.</h1><p>New disclosure.</p>')
    async def failure(**kwargs):raise ValueError('Bad proposal')
    monkeypatch.setattr(cleanup_agent,'propose_cleanup',failure)
    with pytest.raises(ValueError,match='Bad proposal'):await cleanup_agent.run_notes_cleanup(**args)
    async def cancelled(**kwargs):raise asyncio.CancelledError
    monkeypatch.setattr(cleanup_agent,'propose_cleanup',cancelled)
    with pytest.raises(asyncio.CancelledError):await cleanup_agent.run_notes_cleanup(**args)
    with repo.db_session(db) as conn:
        assert [r[0] for r in conn.execute('SELECT status FROM run_agents ORDER BY id')]==['completed','failed','cancelled']
        assert conn.execute('SELECT html FROM notes_cells').fetchone()[0]=='<h1>COMPANY SDN. BHD.</h1><p>New disclosure.</p>'


def test_leaf_container_banner_does_not_remove_a_note_wrapper_or_its_table():
    html='<div class="note-section"><div>COMPANY SDN. BHD.</div><h3>Inventories</h3><p>Disclosure.</p><table><tr><td>1,000</td></tr></table></div>'
    result=apply_patch({'x':{'html':html}},Patch(inspected_cells=['x'],removals=[op()]),{1})['x']['html']
    assert result=='<div class="note-section"><h3>Inventories</h3><p>Disclosure.</p><table><tr><td>1,000</td></tr></table></div>'


@pytest.mark.asyncio
async def test_model_loop_returns_structured_assessment_and_saves_trace(tmp_path):
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage
    from notes.cleanup_agent import propose_cleanup
    model=TestModel(call_tools=[],custom_output_args={'inspected_cells':['Notes:10'],'removals':[]})
    usage=RunUsage()
    patch,viewed=await propose_cleanup(cells={'Notes:10':{'html':'<p>Disclosure.</p>','label':'Note','source_pages':'[1]'}},
                                       pdf_path=str(tmp_path/'unused.pdf'),model=model,usage=usage,output_dir=str(tmp_path))
    assert patch.inspected_cells==['Notes:10'] and patch.removals==[] and viewed==set()
    assert usage.requests==1
    assert (tmp_path/'NOTES_CLEANUP_conversation_trace.json').is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad,feedback', [
    (op(cell='Notes:10', block=99), 'Protected or missing block'),
    (op(cell='Notes:10', page=2), 'Unviewed source page'),
])
async def test_model_corrects_invalid_cleanup_with_validation_feedback(tmp_path, monkeypatch, bad, feedback):
    from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RunUsage
    from notes import cleanup_agent

    monkeypatch.setattr(cleanup_agent, 'count_pdf_pages', lambda _: 2)
    monkeypatch.setattr(cleanup_agent, 'render_pages_to_png_bytes', lambda *args: [b'image'])
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart('view_pdf_pages', {'pages': [1]})])
        if calls == 3:
            retries = [p for m in messages for p in m.parts if isinstance(p, RetryPromptPart)]
            assert retries and feedback in str(retries[-1].content)
        removal = bad if calls == 2 else op(cell='Notes:10')
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
            Patch(inspected_cells=['Notes:10'], removals=[removal]).model_dump())])

    cells = {'Notes:10': {'html': '<h1>COMPANY SDN. BHD.</h1><p>Disclosure.</p>',
                         'label': 'Note', 'source_pages': '[1]'}}
    usage = RunUsage()
    patch, viewed = await cleanup_agent.propose_cleanup(
        cells=cells, pdf_path='unused.pdf', model=FunctionModel(respond),
        usage=usage, output_dir=str(tmp_path))
    assert calls == usage.requests == 3
    assert apply_patch(cells, patch, viewed)['Notes:10']['html'] == '<p>Disclosure.</p>'
    assert (tmp_path / 'NOTES_CLEANUP_conversation_trace.json').is_file()
