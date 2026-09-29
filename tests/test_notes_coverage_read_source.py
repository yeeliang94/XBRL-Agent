"""Which table the Notes coverage view actually reads — plan Step 3.4.

This pins the fact that made revision 1 of the plan's rollback wrong. That
draft promised the Notes tab would "fall back to `run_notes_inventory` and
`notes_cell_provenance`" when the feature was switched off. It does not: the
coverage endpoint reads **`notes_coverage_rows`**.

The consequence is the whole point of this test. When the source-integrity
writer lands (Phase 4+), it must keep populating `notes_coverage_rows`, or
every run created while the feature was enabled shows an empty Notes tab the
moment someone rolls back — the exact failure the rollback plan existed to
prevent.

If a future change moves the read to a different table, this test fails, and
whoever moves it has to update the dual-write requirement in the same commit.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db import repository as repo
from db.schema import init_db


@pytest.fixture()
def client_and_run(tmp_path: Path):
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    init_db(server_module.AUDIT_DB_PATH)
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "sample.pdf", session_id="s", output_dir=str(tmp_path / "s"),
        )
    return TestClient(server_module.app), run_id, server_module


def _insert_coverage_row(db_path, run_id: int, note_num: int, status: str) -> None:
    with repo.db_session(db_path) as conn:
        # A top-level row: `subnote_ref` NULL. Sub-refs are child ROWS, not a
        # JSON column (gotcha #27).
        conn.execute(
            "INSERT INTO notes_coverage_rows("
            "  run_id, note_num, subnote_ref, title, status, reason,"
            "  placements_json, reviewer_added, reviewer_verdict, updated_at"
            ") VALUES (?,?,NULL,?,?,'','[]',0,NULL,'')",
            (run_id, note_num, f"Note {note_num}", status),
        )
        conn.commit()


def test_coverage_comes_from_notes_coverage_rows(client_and_run):
    client, run_id, server_module = client_and_run
    _insert_coverage_row(server_module.AUDIT_DB_PATH, run_id, 7, "placed")

    body = client.get(f"/api/runs/{run_id}/notes-coverage").json()
    nums = [r["note_num"] for r in body["rows"]]
    assert 7 in nums, (
        "coverage is projected from notes_coverage_rows — if this moved, the "
        "dual-write requirement in the build plan must move with it"
    )


def test_coverage_exposes_child_destinations(client_and_run):
    client, run_id, server_module = client_and_run
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.replace_notes_coverage_for_run(conn, run_id, [
            {"note_num": 2, "title": "Accounting policies", "status": "placed", "placements": []},
            {"note_num": 2, "subnote_ref": "2.1", "title": "Revenue", "status": "cited",
             "placements": [{"sheet": "Notes-SummaryofAccPol", "row": 7, "row_label": "Revenue", "kind": "fan_out"}]},
        ])
    row = client.get(f"/api/runs/{run_id}/notes-coverage").json()["rows"][0]
    assert row["subnotes"][0]["placements"] == [
        {"sheet": "Notes-SummaryofAccPol", "row": 7, "row_label": "Revenue", "kind": "fan_out"},
    ]


def test_existing_coverage_uses_durable_subnote_provenance(client_and_run):
    client, run_id, server_module = client_and_run
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.replace_notes_coverage_for_run(conn, run_id, [
            {"note_num": 2, "title": "Accounting policies", "status": "placed",
             "placements": [{"sheet": "Notes-SummaryofAccPol", "row": 7,
                             "row_label": "Revenue", "kind": "fan_out"}]},
            {"note_num": 2, "subnote_ref": "2.1", "title": "Revenue", "status": "cited"},
        ])
        conn.execute(
            "INSERT INTO notes_cell_provenance(run_id, sheet, row, row_label, source_note_refs) "
            "VALUES (?, 'Notes-SummaryofAccPol', 7, 'Revenue', '[\"2.1\"]')",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO notes_cells(run_id, sheet, row, label, html, updated_at) "
            "VALUES (?, 'Notes-SummaryofAccPol', 7, 'Revenue', '<p>Revenue policy</p>', '')",
            (run_id,),
        )
    row = client.get(f"/api/runs/{run_id}/notes-coverage").json()["rows"][0]
    assert row["subnotes"][0]["placements"] == [
        {"sheet": "Notes-SummaryofAccPol", "row": 7, "row_label": "Revenue", "kind": "primary"},
    ]
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        conn.execute(
            "DELETE FROM notes_cells WHERE run_id = ? AND sheet = 'Notes-SummaryofAccPol' AND row = 7",
            (run_id,),
        )
    row = client.get(f"/api/runs/{run_id}/notes-coverage").json()["rows"][0]
    assert row["subnotes"][0]["placements"] == []


def test_existing_coverage_classifies_recovered_policy_fan_out(client_and_run):
    client, run_id, server_module = client_and_run
    sheet = "Notes-SummaryofAccPol"
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.replace_notes_coverage_for_run(conn, run_id, [
            {"note_num": 2, "title": "Accounting policies", "status": "placed"},
            {"note_num": 2, "subnote_ref": "2.1", "title": "Revenue", "status": "cited"},
        ])
        for row, label in ((7, "Revenue"), (8, "Revenue recognition")):
            conn.execute(
                "INSERT INTO notes_cell_provenance(run_id, sheet, row, row_label, source_note_refs) "
                "VALUES (?, ?, ?, ?, '[\"2.1\"]')",
                (run_id, sheet, row, label),
            )
            conn.execute(
                "INSERT INTO notes_cells(run_id, sheet, row, label, html, updated_at) "
                "VALUES (?, ?, ?, ?, '<p>Policy text</p>', '')",
                (run_id, sheet, row, label),
            )
    row = client.get(f"/api/runs/{run_id}/notes-coverage").json()["rows"][0]
    assert [p["kind"] for p in row["subnotes"][0]["placements"]] == ["fan_out", "fan_out"]


def test_existing_prepared_source_locates_subnote_blocks(client_and_run):
    from notes import source_repository as source_repo
    from notes.source_models import SourceBlock, SourceNote

    client, run_id, server_module = client_and_run
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.replace_notes_coverage_for_run(conn, run_id, [
            {"note_num": 2, "title": "Accounting policies", "status": "placed",
             "placements": [{"sheet": "Notes-SummaryofAccPol", "row": 7,
                             "row_label": "Revenue", "kind": "primary"}]},
            {"note_num": 2, "subnote_ref": "2.1", "title": "Revenue", "status": "verified"},
        ])
        generation_id = source_repo.begin_generation(conn, run_id, input_kind="pdf")
        source_repo.write_blocks(conn, generation_id, [
            SourceBlock("heading", "heading", 1, canonical_html="<h2>2.1 Revenue</h2>", source_note_id="note-2"),
            SourceBlock("body", "paragraph", 2, canonical_html="<p>Revenue policy</p>", source_note_id="note-2"),
        ])
        source_repo.write_notes(conn, generation_id, [SourceNote("note-2", "2", "Accounting policies")])
        source_repo.activate_generation(conn, generation_id)
        conn.execute(
            "INSERT INTO notes_cells(run_id, sheet, row, label, html, updated_at) "
            "VALUES (?, 'Notes-SummaryofAccPol', 7, 'Revenue', '<p>Revenue policy</p>', '')",
            (run_id,),
        )
        conn.executemany(
            "INSERT INTO notes_block_placements(run_id, generation_id, block_id, sheet, row) "
            "VALUES (?, ?, ?, 'Notes-SummaryofAccPol', 7)",
            [(run_id, generation_id, block_id) for block_id in ("heading", "body")],
        )
    row = client.get(f"/api/runs/{run_id}/notes-coverage").json()["rows"][0]
    assert row["subnotes"][0]["placements"] == [
        {"sheet": "Notes-SummaryofAccPol", "row": 7, "row_label": "Revenue", "kind": "primary"},
    ]


def test_provenance_and_inventory_alone_do_not_produce_coverage(client_and_run):
    """Rows in the two legacy tables are NOT enough. This is the concrete
    reason the rollback plan had to change."""
    client, run_id, server_module = client_and_run
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO run_notes_inventory(run_id, note_num, title, subnote_refs) "
            "VALUES (?, 4, 'Property, plant and equipment', '[]')",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO notes_cell_provenance("
            "  run_id, sheet, row, row_label, source_note_refs"
            ") VALUES (?, 'Notes', 12, 'PPE', '[\"4\"]')",
            (run_id,),
        )
        conn.commit()

    body = client.get(f"/api/runs/{run_id}/notes-coverage").json()
    assert body["rows"] == [], (
        "the endpoint does not reconstruct coverage from the legacy tables"
    )


def test_a_run_with_no_coverage_rows_reports_the_pre_feature_banner(client_and_run):
    client, run_id, _ = client_and_run
    body = client.get(f"/api/runs/{run_id}/notes-coverage").json()
    assert body["rows"] == []
    assert body["banner"] in {"pre_feature", "not_reviewed", "inventory_unavailable"}
