"""Independent bounded recapture into a new, immutable source generation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import replace
from pathlib import Path

from bs4 import BeautifulSoup
from db import repository as repo
from notes import source_repository as srepo, source_write
from notes.source_models import SourceNote, OwnerKind


async def recheck_source_block(*, db_path, run_id, generation_id, block_id,
                               pdf_path, model, reason, _caller=None):
    """Publish a verified candidate; never swap source under active writers."""
    from ingest.document_preparation import _request_model, _digest, _atomic_text
    from tools.pdf_viewer import render_page_png

    if not reason.strip():
        raise ValueError("Explain the source concern")
    with repo.db_session(db_path) as conn:
        generation = srepo.active_generation(conn, run_id)
        if generation is None or generation['id'] != generation_id:
            raise ValueError("Source generation changed; reload before rechecking")
        generation = dict(generation)
        output_dir = repo.fetch_run(conn, run_id).output_dir
        if generation['input_kind'] != 'prepared_document':
            raise ValueError('Recheck only captured PDF source; native Word text remains authoritative')
        raw = {b['block_id']: b for b in srepo.fetch_blocks(conn, generation_id)}
        blocks = [replace(b, owner_kind=OwnerKind(raw[b.block_id]['owner_kind']),
                          capture_confidence=raw[b.block_id]['capture_confidence'])
                  for b in source_write.load_blocks(conn, generation_id)]
        notes = [dict(n) for n in srepo.fetch_notes(conn, generation_id)]
    selected = next((b for b in blocks if b.block_id == block_id), None)
    if selected is None or selected.page is None:
        raise ValueError("Choose an existing source block with a PDF page")
    source_hash = await asyncio.to_thread(_digest, Path(pdf_path))
    if source_hash != generation['source_sha256']:
        raise ValueError("Original PDF does not match this source generation")
    image = await asyncio.to_thread(render_page_png, pdf_path, selected.page)
    calls = []
    trace_path = Path(output_dir) / f'source_recheck_{uuid.uuid4().hex}.json'
    async def caller(model, stage, images, context):
        usage = {}
        record = {'stage': stage, 'context': context, 'status': 'started', 'usage': usage}
        calls.append(record)
        def save():
            try:
                _atomic_text(trace_path, json.dumps({'run_id': run_id, 'parent_generation_id': generation_id,
                    'block_id': block_id, 'calls': calls}, ensure_ascii=False, indent=2))
            except OSError:
                logging.getLogger(__name__).warning('Could not save source-recheck trace', exc_info=True)
        save()
        try:
            receipt = await (_caller(model, stage, images, context) if _caller
                             else _request_model(model, stage, images, context, usage_out=usage))
            record.update(status='completed', receipt=receipt)
            if _caller:
                usage.update(receipt.get('usage', {}))
            return receipt
        except BaseException as exc:
            record.update(status='failed', error_type=type(exc).__name__)
            raise
        finally:
            save()
    context = {'page': selected.page, 'block_id': block_id,
               'previous_html': selected.canonical_html, 'reason': reason}
    captured = await asyncio.wait_for(caller(model, 'recapturing_block', [image], context), 180)
    candidate = captured.get('html', '')
    old = BeautifulSoup(selected.canonical_html, 'html.parser')
    new = BeautifulSoup(candidate, 'html.parser')
    def topology(soup):
        return [(t.name, t.get('rowspan'), t.get('colspan'))
                for t in soup.find_all(['table', 'tr', 'td', 'th'])]
    if (not captured.get('complete') or not candidate.strip()
            or new.find(['script', 'style'])
            or any(k.lower().startswith('on') or k.lower() == 'style'
                   or (isinstance(v, str) and v.lower().startswith('javascript:'))
                   for tag in new.find_all() for k, v in tag.attrs.items())
            or topology(old) != topology(new)
            or [t.name for t in old.contents if getattr(t, 'name', None)]
               != [t.name for t in new.contents if getattr(t, 'name', None)]):
        raise ValueError("Recheck changed block structure or returned incomplete content")
    checked = await asyncio.wait_for(caller(model, 'verifying_block', [image],
        {**context, 'html': candidate}), 180)
    if not checked.get('verified') or not checked.get('complete') or checked.get('uncertainties'):
        raise ValueError("Independent verification did not approve the correction")
    if await asyncio.to_thread(_digest, Path(pdf_path)) != source_hash:
        raise ValueError("Original PDF changed during recheck")
    locator = {**(selected.locator or {}), 'recheck_parent_generation': generation_id,
               'recheck_original_sha256': selected.content_sha256 or hashlib.sha256(selected.canonical_html.encode()).hexdigest(),
               'recheck_reason': reason, 'recheck_capture': captured,
               'recheck_verification': checked}
    revised = [replace(b, canonical_html=candidate,
                       content_sha256=hashlib.sha256(candidate.encode()).hexdigest(),
                       locator=locator) if b.block_id == block_id else b for b in blocks]
    with repo.db_session(db_path) as conn:
        active = srepo.active_generation(conn, run_id)
        if active is None or active['id'] != generation_id:
            raise ValueError("Source changed during recheck; candidate was not published")
        new_id = srepo.begin_generation(conn, run_id, input_kind=generation['input_kind'],
            source_sha256=source_hash, extractor_version=f"source-recheck:{generation_id}",
            pages_expected=generation['pages_expected'])
        try:
            srepo.write_blocks(conn, new_id, revised)
            updated_notes = []
            for note in notes:
                members = [b for b in revised if b.source_note_id == note['source_note_id']]
                updated_notes.append(SourceNote(
                    source_note_id=note['source_note_id'], top_note_num=note['top_note_num'],
                    title=note['title'], page_lo=note['page_lo'], page_hi=note['page_hi'],
                    boundary_confidence=note['boundary_confidence'], status=note['status'],
                    content_sha256=(hashlib.sha256(''.join(b.canonical_html for b in members).encode()).hexdigest()
                                    if note['source_note_id'] == selected.source_note_id else note['content_sha256'])))
            srepo.write_notes(conn, new_id, updated_notes)
        except BaseException:
            srepo.fail_generation(conn, new_id, failure_code='recheck_publication_failed')
            raise
    return {'candidate_generation_id': new_id, 'parent_generation_id': generation_id,
            'block_id': block_id, 'page': selected.page,
            'changed': candidate != selected.canonical_html, 'verified': True}


def activate_rechecked_source(conn, *, run_id, parent_generation_id, candidate_generation_id):
    """Only a pre-write coordinator may activate; old source stays available."""
    conn.execute('BEGIN IMMEDIATE')
    import task_registry
    from notes_types import NotesTemplateType
    run = repo.fetch_run(conn, run_id)
    if run is None:
        raise ValueError('Run no longer exists')
    tasks = [task_registry.get_task(run.session_id, f'notes:{kind.value}') for kind in NotesTemplateType]
    if any(task is not None and not task.done() for task in tasks) or any(
        conn.execute(f'SELECT 1 FROM {table} WHERE run_id=? AND status=? LIMIT 1',
                     (run_id, 'running')).fetchone()
        for table in ('notes_review_tasks', 'notes_format_tasks', 'notes_integrity_tasks')
    ):
        raise ValueError('Source correction requires idle extraction and review')
    active = srepo.active_generation(conn, run_id)
    candidate = srepo.fetch_generation(conn, candidate_generation_id)
    if (active is None or active['id'] != parent_generation_id or candidate is None
            or candidate['run_id'] != run_id or candidate['status'] != 'building'
            or candidate['extractor_version'] != f'source-recheck:{parent_generation_id}'):
        raise ValueError('Recheck candidate or parent changed')
    if conn.execute('SELECT 1 FROM notes_cells WHERE run_id=? LIMIT 1', (run_id,)).fetchone() or srepo.active_placements(conn, parent_generation_id):
        raise ValueError('Source correction requires an idle pre-write boundary; existing cells remain unchanged')
    # Activation and inherited exclusions share the caller's transaction.
    srepo.activate_generation(conn, candidate_generation_id, pages_processed=active['pages_processed'])
    for usage in srepo.fetch_usages(conn, parent_generation_id):
        if usage['disposition'] == 'excluded':
            from notes.source_models import Disposition
            srepo.record_disposition(conn, run_id, candidate_generation_id, usage['block_id'],
                Disposition.EXCLUDED, reason_code=usage['reason_code'], actor='system')
