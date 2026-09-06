"""Human moves are atomic, versioned and preserve the canonical source records."""
import pytest
from db import repository as repo
from tests.test_server_notes_cells_api import client_and_run  # shared isolated API fixture


def payload(client, run_id):
    rows = client.get(f"/api/runs/{run_id}/notes_cells").json()["sheets"][0]["rows"]
    blank = next(row for row in rows if not row["html"] and row.get("node_uuid"))
    return dict(destination_sheet="Notes-CI", destination_row=blank["row"],
                expected_revision=1, destination_revision=None)


def test_move_preserves_content_and_leaves_export_tombstone(client_and_run):
    client, run_id = client_and_run
    import server
    with repo.db_session(server.AUDIT_DB_PATH) as conn:
        conn.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
    import server
    with repo.db_session(server.AUDIT_DB_PATH) as conn:
        repo.replace_notes_coverage_for_run(conn, run_id, [dict(note_num=1, status="suspected_gap", placements=[dict(sheet="Notes-CI", row=5, row_label="CI", kind="primary")])])
        conn.execute("UPDATE notes_cells SET source_rendered_sha256='original', current_html_sha256='edited', content_origin='human_modified', source_diverged_at='earlier' WHERE run_id=? AND sheet='Notes-CI' AND row=5", (run_id,))
    destination = payload(client, run_id)["destination_row"]
    response = client.post(f"/api/runs/{run_id}/notes_cells/Notes-CI/5/move", json=payload(client, run_id))
    assert response.status_code == 200, response.text
    with repo.db_session(server.AUDIT_DB_PATH) as conn:
        moved = conn.execute("SELECT * FROM notes_cells WHERE run_id=? AND sheet='Notes-CI' AND row=?", (run_id, destination)).fetchone()
        assert moved['html'] == '<p>CI 5</p>'
        assert moved['source_pages'] == '[3]'
        assert moved['content_revision'] == 2
        assert moved['source_diverged_at'] == 'earlier'
        assert moved['source_rendered_sha256'] == 'original'
        assert ('Notes-CI', 5) in repo.fetch_notes_tombstones(conn, run_id)
        coverage = repo.fetch_notes_coverage(conn, run_id)[0]
        assert coverage['status'] == 'suspected_gap'
        assert coverage['placements'][0]['row'] == destination
    rows = client.get(f"/api/runs/{run_id}/notes_cells").json()['sheets'][0]['rows']
    assert next(row for row in rows if row['row'] == 5)['html'] == ''


@pytest.mark.parametrize('change,status', [
    ({'destination_row': 7, 'destination_revision': 1}, 409),
    ({'destination_row': 999}, 400),
    ({'destination_sheet': 'Ghost'}, 400),
    ({'expected_revision': 99}, 409),
    ({'destination_revision': 99}, 409),
    ({'destination_row': 5, 'destination_revision': 1}, 400),
])
def test_move_refuses_changed_occupied_and_invalid_destinations(client_and_run, change, status):
    client, run_id = client_and_run
    import server
    with repo.db_session(server.AUDIT_DB_PATH) as conn:
        conn.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
    before = client.get(f"/api/runs/{run_id}/notes_cells").json()
    response = client.post(f"/api/runs/{run_id}/notes_cells/Notes-CI/5/move", json=payload(client, run_id) | change)
    assert response.status_code == status, response.text
    assert client.get(f"/api/runs/{run_id}/notes_cells").json() == before


def test_move_preserves_active_placements_and_does_not_relocate_stale_decisions(client_and_run):
    from notes import source_repository as sources
    from notes.source_models import SourceBlock, Disposition
    import server
    client, run_id = client_and_run
    destination = payload(client, run_id)['destination_row']
    with repo.db_session(server.AUDIT_DB_PATH) as conn:
        conn.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
        generation = sources.begin_generation(conn, run_id, input_kind='docx_html')
        sources.write_blocks(conn, generation, [SourceBlock(block_id='b1', block_kind='paragraph', reading_order=0, canonical_html='<p>CI 5</p>'), SourceBlock(block_id='b2', block_kind='paragraph', reading_order=1, canonical_html='<p>Elsewhere</p>')])
        sources.activate_generation(conn, generation)
        sources.set_cell_placements(conn, run_id, generation, 'Notes-CI', 5, ['b1'], render_sha256='digest')
        for block in ('b1', 'b2'):
            sources.record_disposition_in_txn(conn, run_id, generation, block, Disposition.INCLUDED, sheet='Notes-CI', row=5)
    response = client.post(f"/api/runs/{run_id}/notes_cells/Notes-CI/5/move", json=payload(client, run_id))
    assert response.status_code == 200, response.text
    with repo.db_session(server.AUDIT_DB_PATH) as conn:
        active = sources.active_placements(conn, generation)
        assert [(item['block_id'], item['row']) for item in active] == [('b1', destination)]
        usages = {item['block_id']: item['row'] for item in sources.fetch_usages(conn, generation)}
        assert usages == {'b1': destination, 'b2': 5}
        assert conn.execute("SELECT active FROM notes_block_placements WHERE block_id='b1' AND row=5").fetchone()[0] == 0


@pytest.mark.parametrize('ledger', ['provenance', 'coverage', 'usage'])
def test_move_refuses_blank_destinations_with_existing_source_records(client_and_run, ledger):
    from notes import source_repository as sources
    from notes.source_models import SourceBlock, Disposition
    import server
    client, run_id = client_and_run
    body = payload(client, run_id)
    destination = body['destination_row']
    with repo.db_session(server.AUDIT_DB_PATH) as conn:
        conn.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
        if ledger == 'provenance':
            conn.execute("INSERT INTO notes_cell_provenance(run_id,sheet,row,row_label,source_note_refs,content_preview) VALUES(?, 'Notes-CI', ?, 'existing', '[]', '')", (run_id, destination))
        elif ledger == 'coverage':
            repo.replace_notes_coverage_for_run(conn, run_id, [dict(note_num=1, status='suspected_gap', placements=[dict(sheet='Notes-CI', row=destination)])])
        else:
            generation = sources.begin_generation(conn, run_id, input_kind='docx_html')
            sources.write_blocks(conn, generation, [SourceBlock(block_id='b1', block_kind='paragraph', reading_order=0, canonical_html='<p>Existing</p>')])
            sources.record_disposition_in_txn(conn, run_id, generation, 'b1', Disposition.INCLUDED, sheet='Notes-CI', row=destination)
    response = client.post(f"/api/runs/{run_id}/notes_cells/Notes-CI/5/move", json=body)
    assert response.status_code == 409, response.text
    with repo.db_session(server.AUDIT_DB_PATH) as conn:
        assert conn.execute("SELECT html FROM notes_cells WHERE run_id=? AND sheet='Notes-CI' AND row=5", (run_id,)).fetchone()[0] == '<p>CI 5</p>'
        assert repo.fetch_notes_tombstones(conn, run_id) == []
