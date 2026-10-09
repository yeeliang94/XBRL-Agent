"""GET /api/runs/{id}/notes-coverage — wire contract
(docs/PLAN-notes-coverage-and-routing.md Phase 7 Step 10).

Asserts: shape + nesting of sub-refs, the banner states, the summary counts,
404 on a missing run, and the pre_feature response for a legacy run with no
coverage rows.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db import repository as repo
from db.schema import init_db


@pytest.fixture()
def client_and_run(tmp_path: Path, monkeypatch) -> tuple[TestClient, int]:
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    init_db(server_module.AUDIT_DB_PATH)
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "sample.pdf", session_id="s", output_dir=str(tmp_path))
    return TestClient(server_module.app), run_id


def _persist(server_module, run_id, rows):
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.replace_notes_coverage_for_run(conn, run_id, rows)


def test_404_on_missing_run(client_and_run):
    client, _ = client_and_run
    assert client.get("/api/runs/999999/notes-coverage").status_code == 404


def test_pre_feature_when_no_rows(client_and_run):
    client, run_id = client_and_run
    body = client.get(f"/api/runs/{run_id}/notes-coverage").json()
    assert body["banner"] == "pre_feature"
    assert body["rows"] == []
    assert body["summary"]["total"] == 0


def test_shape_nesting_and_summary(client_and_run):
    import server as server_module
    client, run_id = client_and_run
    _persist(server_module, run_id, [
        {"note_num": server_module.COVERAGE_META_NOTE, "subnote_ref": None,
         "status": "reviewed"},
        {"note_num": 1, "subnote_ref": None, "status": "placed",
         "title": "Corporate information",
         "placements": [{"sheet": "Notes-CI", "row": 6, "row_label": "x",
                         "kind": "primary"}]},
        {"note_num": 1, "subnote_ref": "(a)", "title": "Accounting policy", "status": "cited",
         "page_lo": 12, "page_hi": 12},
        {"note_num": 1, "subnote_ref": "(b)", "status": "not_verified"},
        {"note_num": 5, "subnote_ref": None, "status": "missing",
         "title": "Investment properties"},
        {"note_num": 13, "subnote_ref": None, "status": "suspected_gap",
         "reason": "numbering jumps 12 → 14",
         "reviewer_verdict": "confirmed_absent"},
    ])
    body = client.get(f"/api/runs/{run_id}/notes-coverage").json()
    assert body["banner"] == "reviewed"
    assert body["inventory_available"] is True

    rows = {r["note_num"]: r for r in body["rows"]}
    # Meta row is NOT in the content rows.
    assert server_module.COVERAGE_META_NOTE not in rows
    # Sub-refs nest under their parent.
    assert [s["subnote_ref"] for s in rows[1]["subnotes"]] == ["(a)", "(b)"]
    assert rows[1]["subnotes"][0]["title"] == "Accounting policy"
    assert rows[1]["subnotes"][0]["page_lo"] == 12
    assert rows[1]["subnotes"][0]["page_hi"] == 12
    assert rows[1]["placements"][0]["sheet"] == "Notes-CI"
    # A confirmed_absent suspected gap is NOT unresolved.
    assert rows[13]["reviewer_verdict"] == "confirmed_absent"

    summary = body["summary"]
    assert summary["placed"] == 1
    assert summary["missing"] == 1
    assert summary["suspected_gap"] == 1
    # Only the missing note (5) is unresolved; note 13 is resolved.
    assert summary["unresolved"] == 1


def test_legacy_run_uses_saved_scout_subnote_page_when_child_row_lacks_it(client_and_run):
    import server as server_module
    client, run_id = client_and_run
    _persist(server_module, run_id, [
        {"note_num": 2, "status": "placed", "title": "Accounting policies",
         "page_lo": 10, "page_hi": 12},
        {"note_num": 2, "subnote_ref": "2.10", "status": "cited"},
    ])
    (server_module.OUTPUT_DIR / "infopack.json").write_text(json.dumps({
        "notes_inventory": [{
            "note_num": 2, "page_range": [10, 12],
            "subnotes": [{"subnote_ref": "2.10", "page_range": [12, 12]}],
        }],
    }))

    child = client.get(f"/api/runs/{run_id}/notes-coverage").json()["rows"][0]["subnotes"][0]
    assert (child["page_lo"], child["page_hi"]) == (12, 12)


def test_inventory_unavailable_banner(client_and_run):
    import server as server_module
    client, run_id = client_and_run
    _persist(server_module, run_id, [
        {"note_num": server_module.COVERAGE_META_NOTE, "subnote_ref": None,
         "status": "inventory_unavailable"},
    ])
    body = client.get(f"/api/runs/{run_id}/notes-coverage").json()
    assert body["banner"] == "inventory_unavailable"
    assert body["inventory_available"] is False
    assert body["rows"] == []


def test_not_reviewed_banner_and_subnote_missing_unresolved(client_and_run):
    import server as server_module
    client, run_id = client_and_run
    _persist(server_module, run_id, [
        {"note_num": server_module.COVERAGE_META_NOTE, "subnote_ref": None,
         "status": "not_reviewed"},
        {"note_num": 9, "subnote_ref": None, "status": "placed",
         "title": "Investment properties"},
        {"note_num": 9, "subnote_ref": "(a)", "status": "missing"},
    ])
    body = client.get(f"/api/runs/{run_id}/notes-coverage").json()
    assert body["banner"] == "not_reviewed"
    # A confirmed-missing sub-ref makes the placed parent unresolved.
    assert body["summary"]["unresolved"] == 1


@pytest.mark.parametrize("prepared", [False, True])
def test_unresolved_parent_recovers_live_destination_without_clearing_review(client_and_run, prepared):
    import server as server_module
    client, run_id = client_and_run
    _persist(server_module, run_id, [
        {"note_num": 4, "status": "missing", "title": "Revenue"},
    ])
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.upsert_notes_cell(conn, run_id=run_id, sheet="Notes-Listofnotes",
                               row=20, label="Revenue", html="<p>Revenue</p>")
        if prepared:
            from notes import source_repository as srepo
            from notes.source_models import SourceBlock, SourceNote
            gen = srepo.begin_generation(conn, run_id, input_kind="prepared_document")
            srepo.write_blocks(conn, gen, [SourceBlock(block_id="b4", block_kind="paragraph",
                reading_order=0, canonical_html="<p>Revenue</p>", source_note_id="n4")])
            srepo.write_notes(conn, gen, [SourceNote(source_note_id="n4", top_note_num="4")])
            srepo.activate_generation(conn, gen)
            srepo.set_cell_placements(conn, run_id, gen, "Notes-Listofnotes", 20, ["b4"])
        else:
            repo.upsert_notes_provenance(conn, run_id=run_id, sheet="Notes-Listofnotes",
                                        row=20, row_label="Revenue", source_note_refs=["4"])
        # A stale provenance coordinate must never be offered as a destination.
        repo.upsert_notes_provenance(conn, run_id=run_id, sheet="Notes-CI",
                                    row=99, row_label="Deleted", source_note_refs=["4"])
    body = client.get(f"/api/runs/{run_id}/notes-coverage").json()
    row = body["rows"][0]
    assert [(p["sheet"], p["row"]) for p in row["placements"]] == [("Notes-Listofnotes", 20)]
    assert row["status"] == "missing"
    assert body["summary"]["unresolved"] == 1
