"""Cell lineage on the edit path — plan Phase 5, Steps 5.2 / 5.3.

Peer review called the reviewer and the editor blockers: both wrote cell
bodies without touching lineage, so the moment either ran, content and lineage
diverged and the completeness figure described a document nobody had.

The tests here fix the two properties that closes:

* an edit records its divergence **in the same transaction** as the text, so a
  crash cannot leave a cell that looks source-exact and is not;
* a concurrent edit is refused rather than silently overwritten, because an
  edit now decides whether a cell counts as accounted for.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from db import repository as repo
from db.schema import init_db
from notes import lineage
from notes import source_repository as srepo
from notes.source_models import ContentOrigin, Disposition, SourceBlock

SOURCE_HTML = "<p>Trade receivables are stated at cost.</p>"


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "audit.sqlite"
    init_db(path)
    return path


@pytest.fixture()
def conn_run(db):
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir="/tmp/s"
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=10, label="Receivables",
            html=SOURCE_HTML, evidence="PDF page 12", source_pages=[12],
        )
        yield conn, run_id


# --------------------------------------------------------------------------
# Step 5.2 — the write records its own divergence
# --------------------------------------------------------------------------

def test_a_cell_with_no_source_lineage_is_not_reported_as_diverged(conn_run):
    conn, run_id = conn_run
    state = lineage.read_lineage(conn, run_id, "Notes", 10)
    assert state is not None
    assert state.diverged is False


def test_marking_a_source_render_records_the_hash_and_clears_divergence(conn_run):
    conn, run_id = conn_run
    lineage.mark_source_render(
        conn, run_id, "Notes", 10, generation_id=7,
        rendered_sha256=lineage.content_sha256(SOURCE_HTML),
    )
    state = lineage.read_lineage(conn, run_id, "Notes", 10)
    assert state.source_generation_id == 7
    assert state.content_origin == ContentOrigin.SOURCE_EXACT.value
    assert state.diverged is False
    assert state.source_diverged_at is None


def test_an_edit_marks_the_cell_diverged(conn_run):
    conn, run_id = conn_run
    lineage.mark_source_render(
        conn, run_id, "Notes", 10, generation_id=7,
        rendered_sha256=lineage.content_sha256(SOURCE_HTML),
    )
    state = lineage.mark_human_edit(
        conn, run_id, "Notes", 10, "<p>Reworded by the reviewer.</p>"
    )
    assert state.diverged is True
    assert state.content_origin == ContentOrigin.HUMAN_MODIFIED.value
    assert state.source_diverged_at


def test_editing_back_to_the_source_text_clears_the_divergence(conn_run):
    """A permanent mark for an edit that was undone would make the flag
    useless — people stop reading a signal that never goes out."""
    conn, run_id = conn_run
    digest = lineage.content_sha256(SOURCE_HTML)
    lineage.mark_source_render(
        conn, run_id, "Notes", 10, generation_id=7, rendered_sha256=digest
    )
    lineage.mark_human_edit(conn, run_id, "Notes", 10, "<p>changed</p>")
    state = lineage.mark_human_edit(conn, run_id, "Notes", 10, SOURCE_HTML)
    assert state.diverged is False
    assert state.source_diverged_at is None
    assert state.content_origin == ContentOrigin.SOURCE_EXACT.value


def test_the_first_divergence_timestamp_is_kept_across_later_edits(conn_run):
    conn, run_id = conn_run
    lineage.mark_source_render(
        conn, run_id, "Notes", 10, generation_id=7,
        rendered_sha256=lineage.content_sha256(SOURCE_HTML),
    )
    first = lineage.mark_human_edit(conn, run_id, "Notes", 10, "<p>a</p>")
    second = lineage.mark_human_edit(conn, run_id, "Notes", 10, "<p>b</p>")
    assert second.source_diverged_at == first.source_diverged_at


def test_an_authored_cell_is_marked_human_without_inventing_a_divergence(conn_run):
    conn, run_id = conn_run
    state = lineage.mark_human_edit(conn, run_id, "Notes", 10, "<p>authored</p>")
    assert state.content_origin == ContentOrigin.HUMAN_MODIFIED.value
    assert state.source_diverged_at is None, "nothing to diverge from"


def test_diverged_cells_lists_only_real_divergences(conn_run):
    conn, run_id = conn_run
    repo.upsert_notes_cell(
        conn, run_id=run_id, sheet="Notes", row=20, label="Other",
        html="<p>x</p>",
    )
    lineage.mark_source_render(
        conn, run_id, "Notes", 10, generation_id=7,
        rendered_sha256=lineage.content_sha256(SOURCE_HTML),
    )
    lineage.mark_source_render(
        conn, run_id, "Notes", 20, generation_id=7,
        rendered_sha256=lineage.content_sha256("<p>x</p>"),
    )
    lineage.mark_human_edit(conn, run_id, "Notes", 10, "<p>edited</p>")
    conn.commit()
    diverged = lineage.diverged_cells(conn, run_id)
    assert [d["row"] for d in diverged] == [10]


# --------------------------------------------------------------------------
# optimistic concurrency
# --------------------------------------------------------------------------

def test_a_matching_version_passes(conn_run):
    conn, run_id = conn_run
    r = conn.execute(
        "SELECT updated_at FROM notes_cells WHERE run_id = ? AND row = 10",
        (run_id,),
    ).fetchone()
    lineage.check_version(conn, run_id, "Notes", 10, r["updated_at"])


def test_a_stale_version_is_refused(conn_run):
    conn, run_id = conn_run
    with pytest.raises(lineage.StaleCellError):
        lineage.check_version(conn, run_id, "Notes", 10, "1999-01-01T00:00:00")


def test_sending_no_version_opts_out(conn_run):
    """The agent write path has no reader to be stale against."""
    conn, run_id = conn_run
    lineage.check_version(conn, run_id, "Notes", 10, None)


# --------------------------------------------------------------------------
# through the API
# --------------------------------------------------------------------------

@pytest.fixture()
def client_run(tmp_path):
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "api.sqlite"
    init_db(server_module.AUDIT_DB_PATH)
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=10, label="Receivables",
            html=SOURCE_HTML, evidence="PDF page 12", source_pages=[12],
        )
    return TestClient(server_module.app), run_id, server_module.AUDIT_DB_PATH


def _seed_source(db_path, run_id: int) -> int:
    with repo.db_session(db_path) as conn:
        gen = srepo.begin_generation(conn, run_id, input_kind="docx_html")
        srepo.write_blocks(conn, gen, [
            SourceBlock(block_id="b1", block_kind="paragraph", reading_order=0,
                        canonical_html=SOURCE_HTML),
        ])
        srepo.activate_generation(conn, gen)
        srepo.record_disposition(
            conn, run_id, gen, "b1", Disposition.INCLUDED,
            sheet="Notes", row=10,
        )
        from notes.source_render import render_blocks
        rendered = render_blocks(
            [SourceBlock(block_id="b1", block_kind="paragraph",
                         reading_order=0, canonical_html=SOURCE_HTML)],
            ["b1"],
        )
        conn.execute(
            "UPDATE notes_cells SET html = ? WHERE run_id = ? AND row = 10",
            (rendered.html, run_id),
        )
        lineage.mark_source_render(
            conn, run_id, "Notes", 10, generation_id=gen,
            rendered_sha256=rendered.source_rendered_sha256,
        )
        conn.commit()
    return gen


def test_patch_records_divergence_in_the_same_write(client_run):
    client, run_id, db_path = client_run
    _seed_source(db_path, run_id)

    r = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes/10",
        json={"html": "<p>Reworded.</p>"},
    )
    assert r.status_code == 200
    with repo.db_session(db_path) as conn:
        state = lineage.read_lineage(conn, run_id, "Notes", 10)
    assert state.content_origin == ContentOrigin.HUMAN_MODIFIED.value
    assert state.diverged is True


def test_patch_with_a_stale_version_returns_409(client_run):
    client, run_id, _db = client_run
    r = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes/10",
        json={"html": "<p>x</p>", "expected_updated_at": "1999-01-01T00:00:00"},
    )
    assert r.status_code == 409
    assert "reload" in r.json()["detail"].lower()


def test_patch_with_the_current_version_succeeds(client_run):
    client, run_id, db_path = client_run
    with repo.db_session(db_path) as conn:
        current = conn.execute(
            "SELECT updated_at FROM notes_cells WHERE run_id = ? AND row = 10",
            (run_id,),
        ).fetchone()["updated_at"]
    r = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes/10",
        json={"html": "<p>x</p>", "expected_updated_at": current},
    )
    assert r.status_code == 200
    assert r.json()["updated_at"] != ""


def test_the_version_token_a_patch_returns_is_usable_for_the_next_edit(client_run):
    """The round-trip the editor actually performs — otherwise a second edit
    in the same session would always 409."""
    client, run_id, _db = client_run
    first = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes/10", json={"html": "<p>a</p>"}
    ).json()
    second = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes/10",
        json={"html": "<p>b</p>", "expected_updated_at": first["updated_at"]},
    )
    assert second.status_code == 200


def test_source_compare_shows_both_versions(client_run):
    client, run_id, db_path = client_run
    _seed_source(db_path, run_id)
    client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes/10",
        json={"html": "<p>Reworded.</p>"},
    )
    body = client.get(
        f"/api/runs/{run_id}/notes_cells/Notes/10/source-compare"
    ).json()
    assert body["diverged"] is True
    assert "Reworded" in body["current_html"]
    assert "stated at cost" in body["source_html"]
    assert body["restorable"] is True


def test_source_compare_on_a_cell_with_no_lineage_says_so(client_run):
    """An empty diff would read as 'identical'. Null is the honest answer."""
    client, run_id, _db = client_run
    body = client.get(
        f"/api/runs/{run_id}/notes_cells/Notes/10/source-compare"
    ).json()
    assert body["source_html"] is None
    assert body["restorable"] is False


def test_restore_puts_the_source_version_back(client_run):
    client, run_id, db_path = client_run
    _seed_source(db_path, run_id)
    client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes/10",
        json={"html": "<p>Reworded.</p>"},
    )
    r = client.post(f"/api/runs/{run_id}/notes_cells/Notes/10/restore-source")
    assert r.status_code == 200
    assert "stated at cost" in r.json()["html"]
    with repo.db_session(db_path) as conn:
        state = lineage.read_lineage(conn, run_id, "Notes", 10)
    assert state.diverged is False
    assert state.content_origin == ContentOrigin.SOURCE_EXACT.value


def test_restore_keeps_the_audit_history(client_run):
    """Restoring is an addition to the record, not an erasure of it."""
    client, run_id, db_path = client_run
    gen = _seed_source(db_path, run_id)
    client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes/10",
        json={"html": "<p>Reworded.</p>"},
    )
    client.post(f"/api/runs/{run_id}/notes_cells/Notes/10/restore-source")
    with repo.db_session(db_path) as conn:
        events = conn.execute(
            "SELECT to_disposition FROM notes_disposition_events "
            "WHERE run_id = ? AND generation_id = ?", (run_id, gen),
        ).fetchall()
    assert len(events) >= 1


def test_restore_refuses_when_there_is_nothing_to_restore_to(client_run):
    client, run_id, _db = client_run
    r = client.post(f"/api/runs/{run_id}/notes_cells/Notes/10/restore-source")
    assert r.status_code == 409
    assert "not built from the source" in r.json()["detail"]


def test_restore_with_a_stale_version_returns_409(client_run):
    client, run_id, db_path = client_run
    _seed_source(db_path, run_id)
    r = client.post(
        f"/api/runs/{run_id}/notes_cells/Notes/10/restore-source",
        json={"expected_updated_at": "1999-01-01T00:00:00"},
    )
    assert r.status_code == 409
