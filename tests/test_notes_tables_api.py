"""GET /api/runs/{id}/notes_tables — plan Phase 2, Steps 2.1 / 2.4 / 1.5.

The wire contract the table-review surface talks to. Two things it must not
overclaim:

* per-table ``style_state`` is DERIVED, because the DB stores one
  ``style_source`` per cell;
* ``source_pages`` is the CELL's evidence, so it ships under ``cell_evidence``
  with ``kind: "cell"`` rather than being presented as this table's origin.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db import repository as repo
from db.schema import init_db

_SOURCE_TABLE = (
    '<h3>3 Trade receivables</h3>'
    '<table data-source-styled="true">'
    "<tr><td>Trade receivables</td><td>1,595</td></tr>"
    "<tr><td>Total</td><td>1,595</td></tr>"
    "</table>"
)
_PLAIN_AND_STYLED = (
    "<table><tr><td>a</td><td>1</td></tr><tr><td>b</td><td>2</td></tr></table>"
    '<table><tr><td style="border-bottom:1px solid #000">c</td>'
    "<td>3</td></tr><tr><td>d</td><td>4</td></tr></table>"
)
_PROSE_ONLY = "<p>No tables in this note.</p>"


@pytest.fixture()
def client_and_run(tmp_path: Path) -> tuple[TestClient, int]:
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    init_db(server_module.AUDIT_DB_PATH)

    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "sample.pdf",
            session_id="sess-tables", output_dir=str(tmp_path / "sess-tables"),
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=10,
            label="Trade receivables", html=_SOURCE_TABLE,
            evidence="PDF page 12", source_pages=[12], style_source="source",
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=20,
            label="Two tables", html=_PLAIN_AND_STYLED,
            evidence="PDF page 13", source_pages=[13, 14], style_source="unstyled",
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=30,
            label="Prose only", html=_PROSE_ONLY,
            evidence="PDF page 15", source_pages=[15], style_source="unstyled",
        )
    return TestClient(server_module.app), run_id


def test_unknown_run_is_404(client_and_run):
    client, _ = client_and_run
    assert client.get("/api/runs/999999/notes_tables").status_code == 404


def test_lists_every_table_and_skips_prose_only_cells(client_and_run):
    client, run_id = client_and_run
    body = client.get(f"/api/runs/{run_id}/notes_tables").json()
    assert body["summary"]["tables"] == 3          # 1 + 2 + 0
    assert body["summary"]["cells_with_tables"] == 2
    assert all(t["row"] != 30 for t in body["tables"]), "prose-only cell skipped"


def test_table_id_is_stable_and_unique(client_and_run):
    client, run_id = client_and_run
    ids = [t["table_id"] for t in client.get(
        f"/api/runs/{run_id}/notes_tables").json()["tables"]]
    assert len(ids) == len(set(ids))
    assert "Notes:10:0" in ids
    assert {"Notes:20:0", "Notes:20:1"} <= set(ids)


def test_two_tables_in_one_cell_report_their_own_style(client_and_run):
    """The cell's single style_source says 'unstyled' for both; the derived
    per-table state must not repeat that lie."""
    client, run_id = client_and_run
    tables = client.get(f"/api/runs/{run_id}/notes_tables").json()["tables"]
    row20 = {t["table_index"]: t for t in tables if t["row"] == 20}
    assert row20[0]["style_state"] == "plain"
    assert row20[1]["style_state"] == "styled"
    assert row20[0]["cell_style_source"] == "unstyled"
    assert row20[1]["cell_style_source"] == "unstyled"


def test_source_styled_table_is_reported_as_source(client_and_run):
    client, run_id = client_and_run
    tables = client.get(f"/api/runs/{run_id}/notes_tables").json()["tables"]
    t = next(t for t in tables if t["row"] == 10)
    assert t["style_state"] == "source"
    assert t["source_styled"] is True


def test_pages_are_labelled_as_cell_evidence_not_table_provenance(client_and_run):
    client, run_id = client_and_run
    tables = client.get(f"/api/runs/{run_id}/notes_tables").json()["tables"]
    for t in (t for t in tables if t["row"] == 20):
        assert t["cell_evidence"]["kind"] == "cell"
        assert t["cell_evidence"]["source_pages"] == [13, 14]
    assert not any("source_pages" in t for t in tables), (
        "a bare source_pages key would read as this table's own provenance"
    )


def test_geometry_is_reported(client_and_run):
    client, run_id = client_and_run
    tables = client.get(f"/api/runs/{run_id}/notes_tables").json()["tables"]
    t = next(t for t in tables if t["row"] == 10)
    assert (t["rows"], t["cols"], t["cells"]) == (2, 2, 4)
    assert t["chars"] > 0


def test_summary_carries_the_unstyled_count(client_and_run):
    """Step 1.5 rides on this endpoint rather than a second one — the walk
    already computes it, and one source of the number cannot drift."""
    client, run_id = client_and_run
    summary = client.get(f"/api/runs/{run_id}/notes_tables").json()["summary"]
    assert summary["plain"] == 1
    assert summary["styled"] == 1
    assert summary["source"] == 1
    assert summary["plain"] + summary["styled"] + summary["source"] == summary["tables"]


def test_flags_are_reported(client_and_run):
    client, run_id = client_and_run
    body = client.get(f"/api/runs/{run_id}/notes_tables").json()
    assert "flagged" in body["summary"]
    for t in body["tables"]:
        assert isinstance(t["flags"], list)


def test_run_with_no_notes_returns_empty_not_error(tmp_path):
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit2.sqlite"
    init_db(server_module.AUDIT_DB_PATH)
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "x.pdf", session_id="s", output_dir=str(tmp_path / "s"),
        )
    body = TestClient(server_module.app).get(
        f"/api/runs/{run_id}/notes_tables").json()
    assert body["tables"] == []
    assert body["summary"]["tables"] == 0


def test_cell_and_table_counts_are_both_reported(client_and_run):
    """Peer-review finding: Step 1.5 specifies a CELL count; `plain` is a
    TABLE count. Both are useful and they differ — row 20 holds two tables in
    one cell marked `unstyled`, so it contributes 2 tables and 1 cell."""
    client, run_id = client_and_run
    s = client.get(f"/api/runs/{run_id}/notes_tables").json()["summary"]
    assert s["tables"] == 3
    assert s["cells_with_tables"] == 2
    # Rows 10 (style_source='source') and 20 ('unstyled'); only row 20 counts.
    assert s["cells_unstyled"] == 1
    assert s["plain"] == 1, "the table-level count is unchanged"


def test_a_cell_with_several_plain_tables_counts_once_as_a_cell(tmp_path):
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit3.sqlite"
    init_db(server_module.AUDIT_DB_PATH)
    three_plain = "".join(
        f"<table><tr><td>a{i}</td><td>{i}</td></tr>"
        f"<tr><td>b{i}</td><td>{i}</td></tr></table>"
        for i in range(3)
    )
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "x.pdf", session_id="s3", output_dir=str(tmp_path / "s3"),
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=5, label="Three tables",
            html=three_plain, evidence=None, source_pages=[7],
            style_source="unstyled",
        )
    s = TestClient(server_module.app).get(
        f"/api/runs/{run_id}/notes_tables").json()["summary"]
    assert s["plain"] == 3
    assert s["cells_unstyled"] == 1
