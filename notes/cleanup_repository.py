"""Durable evidence for intentional page-furniture omissions, never a hash bypass."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from notes.cleanup_patch import Patch, apply_patch
from notes.format_verify import verify_format_only
from notes.lineage import content_sha256


def _receipt_steps(receipt):
    payload = json.loads(receipt['patch_json'])
    if isinstance(payload, list):
        if not payload:
            raise ValueError('Empty cleanup receipt history')
        return payload
    # Existing single-pass receipts remain replayable.
    return [{'before_html': receipt['before_html'], 'after_html': receipt['after_html'],
             'patch': payload, 'viewed_pages': json.loads(receipt['viewed_pages_json'])}]


def receipt_matches(conn, run_id, sheet, row, generation_id, source_html, current_html):
    receipt = conn.execute(
        'SELECT * FROM notes_cleanup_receipts WHERE run_id=? AND sheet=? AND row=? AND generation_id IS ?',
        (run_id, sheet, row, generation_id),
    ).fetchone()
    if not receipt or not verify_format_only(source_html, receipt['before_html']).ok:
        return False
    try:
        cid = f'{sheet}:{row}'
        replay = receipt['before_html']
        for step in _receipt_steps(receipt):
            if not verify_format_only(replay, step['before_html']).ok:
                return False
            patch = Patch.model_validate(step['patch'])
            replay = apply_patch({cid: {'html': step['before_html']}}, patch,
                                 set(step['viewed_pages']))[cid]['html']
            if replay != step['after_html']:
                return False
        return (replay == receipt['after_html']
                and verify_format_only(replay, current_html).ok)
    except (ValueError, TypeError, KeyError):
        return False


def save_cleanup(conn, *, run_id, cells, patch, viewed_pages, model):
    """Compare-and-swap each changed cell, retaining source and removal evidence."""
    result = apply_patch(cells, patch, viewed_pages)
    changed = 0
    skipped = []
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        for cid, cell in result.items():
            if not cell['removals']:
                continue
            before = cells[cid]
            current = conn.execute(
                'SELECT html,content_revision,source_generation_id,content_origin FROM notes_cells '
                'WHERE run_id=? AND sheet=? AND row=?',
                (run_id, before['sheet'], before['row']),
            ).fetchone()
            if (not current or current['html'] != before['html']
                or current['content_revision'] != before['content_revision']
                or current['source_generation_id'] != before['source_generation_id']
                or current['content_origin'] == 'human_modified'):
                skipped.append(cid)
                continue
            local_patch = Patch(inspected_cells=[cid], removals=[o for o in patch.removals if o.cell == cid])
            previous = conn.execute(
                'SELECT * FROM notes_cleanup_receipts WHERE run_id=? AND sheet=? AND row=? AND generation_id IS ?',
                (run_id, before['sheet'], before['row'], before['source_generation_id']),
            ).fetchone()
            history = []
            original_html = before['html']
            evidence_pages = set(viewed_pages)
            if previous:
                matches = receipt_matches(conn, run_id, before['sheet'], before['row'],
                                          before['source_generation_id'], previous['before_html'], before['html'])
                restored = verify_format_only(previous['before_html'], before['html']).ok
                if not matches and not restored and before['source_generation_id'] is not None:
                    raise ValueError(f'Previous cleanup receipt no longer matches {cid}')
                if matches:
                    history = _receipt_steps(previous)
                    original_html = previous['before_html']
                    evidence_pages.update(json.loads(previous['viewed_pages_json']))
            history.append({'before_html': before['html'], 'after_html': cell['html'],
                            'patch': local_patch.model_dump(), 'viewed_pages': sorted(viewed_pages),
                            'model': model, 'created_at': now})
            conn.execute(
                'INSERT INTO notes_cleanup_receipts(run_id,sheet,row,generation_id,before_html,after_html,'
                'patch_json,viewed_pages_json,model,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(run_id,sheet,row) DO UPDATE SET generation_id=excluded.generation_id,'
                'before_html=excluded.before_html,after_html=excluded.after_html,patch_json=excluded.patch_json,'
                'viewed_pages_json=excluded.viewed_pages_json,model=excluded.model,created_at=excluded.created_at',
                (run_id,before['sheet'],before['row'],before['source_generation_id'],original_html,cell['html'],
                 json.dumps(history),json.dumps(sorted(evidence_pages)),model,now),
            )
            # Revert formatting must remain style-only after content cleanup.
            snapshot = conn.execute(
                'SELECT html FROM notes_format_snapshots WHERE run_id=? AND sheet=? AND row=?',
                (run_id,before['sheet'],before['row']),
            ).fetchone()
            if snapshot:
                clean_snapshot = apply_patch({cid:{'html':snapshot['html']}}, local_patch, viewed_pages)[cid]['html']
                conn.execute('UPDATE notes_format_snapshots SET html=? WHERE run_id=? AND sheet=? AND row=?',
                             (clean_snapshot,run_id,before['sheet'],before['row']))
            digest = content_sha256(cell['html'])
            # Keep original source blocks and placements; the receipt proves
            # the exact permitted omissions when integrity reconstructs them.
            conn.execute(
                'UPDATE notes_cells SET html=?,updated_at=?,current_html_sha256=?,source_rendered_sha256=CASE WHEN source_generation_id IS NOT NULL THEN ? ELSE source_rendered_sha256 END,'
                'content_revision=COALESCE(content_revision,0)+1 WHERE run_id=? AND sheet=? AND row=?',
                (cell['html'],now,digest,digest,run_id,before['sheet'],before['row']),
            )
            changed += 1
    return {'changed_rows':changed,'skipped_cells':skipped,'removed_blocks':sum(len(c['removals']) for cid,c in result.items() if cid not in skipped)}
