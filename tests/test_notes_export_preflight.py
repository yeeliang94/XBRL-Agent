"""Export preflight — plan Phase 9, Step 9.1.

Both exits from this system degrade quietly. The size ladder reports what it
dropped only during the fill, when the operator is already committed; and
neither exit says anything about a note whose source is half accounted for.

The tests fix what the preflight must say, and two things it must NOT do:
change any decorator (gotcha #16 keeps those in lock-step), or block the
export — partial output stays downloadable (Step 7.3).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db import repository as repo
from db.schema import init_db
from notes import export_preflight, source_write
from notes import source_repository as srepo
from notes.source_models import Disposition, OwnerKind, SourceBlock, SourceNote

BLOCKS = [
    SourceBlock(block_id="b1", block_kind="heading", reading_order=0,
                canonical_html="<h3>5. Receivables</h3>", source_note_id="n5",
                owner_kind=OwnerKind.NOTE),
    SourceBlock(block_id="b2", block_kind="paragraph", reading_order=1,
                canonical_html="<p>Stated at cost.</p>", source_note_id="n5",
                owner_kind=OwnerKind.NOTE),
]


@pytest.fixture()
def client_run(tmp_path):
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    init_db(server_module.AUDIT_DB_PATH)
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=10, label="Receivables",
            html="<p>Stated at cost.</p>",
        )
    return TestClient(server_module.app), run_id, server_module.AUDIT_DB_PATH


def _seed_source(db_path, run_id) -> int:
    with repo.db_session(db_path) as conn:
        gen = srepo.begin_generation(conn, run_id, input_kind="docx_html")
        srepo.write_blocks(conn, gen, BLOCKS)
        srepo.write_notes(conn, gen, [
            SourceNote(source_note_id="n5", top_note_num="5",
                       title="5. Receivables"),
        ])
        srepo.activate_generation(conn, gen)
    return gen


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------

def test_a_run_with_no_source_reading_reports_that_it_was_not_checked(client_run):
    """Silence and "checked, nothing found" are different answers."""
    client, run_id, _db = client_run
    body = client.get(f"/api/runs/{run_id}/export_preflight").json()
    assert body["coverage_checked"] is False


def test_an_unaccounted_note_is_reported_as_blocking(client_run):
    client, run_id, db = client_run
    _seed_source(db, run_id)
    body = client.get(f"/api/runs/{run_id}/export_preflight").json()
    assert body["coverage_checked"] is True
    coverage = [i for i in body["items"] if i["kind"] == "coverage"]
    assert len(coverage) == 1
    assert coverage[0]["severity"] == "blocking"
    assert "2 part(s) of note 5" in coverage[0]["message"]
    assert "will not show this" in coverage[0]["message"]


def test_a_fully_accounted_run_is_clean(client_run):
    client, run_id, db = client_run
    gen = _seed_source(db, run_id)
    with repo.db_session(db) as conn:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["b1", "b2"], label="Receivables",
        )
    body = client.get(f"/api/runs/{run_id}/export_preflight").json()
    assert [i for i in body["items"] if i["kind"] == "coverage"] == []


def test_a_settled_exclusion_does_not_count_against_the_export(client_run):
    client, run_id, db = client_run
    gen = _seed_source(db, run_id)
    with repo.db_session(db) as conn:
        for bid in ("b1", "b2"):
            srepo.record_disposition(
                conn, run_id, gen, bid, Disposition.EXCLUDED,
                reason_code="OUTSIDE_SELECTED_FILING_SCOPE",
            )
    body = client.get(f"/api/runs/{run_id}/export_preflight").json()
    assert [i for i in body["items"] if i["kind"] == "coverage"] == []


def test_an_unreadable_exclusion_still_counts(client_run):
    """Recording that a part could not be read must not clear the export
    warning — that would make the preflight another way to go green."""
    client, run_id, db = client_run
    gen = _seed_source(db, run_id)
    with repo.db_session(db) as conn:
        for bid in ("b1", "b2"):
            srepo.record_disposition(
                conn, run_id, gen, bid, Disposition.EXCLUDED,
                reason_code="UNREADABLE_NEEDS_REVIEW",
            )
    body = client.get(f"/api/runs/{run_id}/export_preflight").json()
    assert [i for i in body["items"] if i["kind"] == "coverage"]


# --------------------------------------------------------------------------
# size
# --------------------------------------------------------------------------

def test_a_note_too_long_even_plain_is_blocking_content_loss(client_run):
    client, run_id, db = client_run
    with repo.db_session(db) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=20, label="Huge note",
            html="<p>" + ("word " * 12_000) + "</p>",
        )
    body = client.get(f"/api/runs/{run_id}/export_preflight").json()
    content = [i for i in body["items"] if i["kind"] == "content"]
    assert content and content[0]["severity"] == "blocking"
    assert "Split its content" in content[0]["message"]
    assert "nothing is cut short" in content[0]["message"]


def test_formatting_loss_is_advisory_not_blocking(client_run):
    """The note still files; it files plain. That is a different fact from
    losing text, and conflating them trains people to ignore both."""
    items = [
        export_preflight.PreflightItem(
            "formatting", "advisory", "Notes", 10, "x", "files plain"
        ),
    ]
    p = export_preflight.Preflight(run_id=1, items=items)
    assert p.blocking == []
    assert p.as_dict()["advisory_count"] == 1


def test_the_size_check_can_be_skipped(client_run):
    client, run_id, db = client_run
    _seed_source(db, run_id)
    body = client.get(
        f"/api/runs/{run_id}/export_preflight?include_size=false"
    ).json()
    assert all(i["kind"] == "coverage" for i in body["items"])


def test_a_size_check_failure_is_reported_not_swallowed(client_run, monkeypatch):
    """Failure to assess export loss is NOT proof that there is none. This
    returned an empty list, so the whole preflight read `clean: true`."""
    client, run_id, db = client_run
    _seed_source(db, run_id)
    monkeypatch.setattr(
        "mtool.notes_exporter.build_notes_fill_doc",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    body = client.get(f"/api/runs/{run_id}/export_preflight").json()
    assert body["coverage_checked"] is True
    unavailable = [i for i in body["items"] if i["kind"] == "unavailable"]
    assert len(unavailable) == 1
    assert "not the same as nothing being dropped" in unavailable[0]["message"]


def test_a_size_check_failure_on_an_otherwise_clean_run_is_not_clean(
    client_run, monkeypatch,
):
    client, run_id, _db = client_run
    monkeypatch.setattr(
        "mtool.notes_exporter.build_notes_fill_doc",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    body = client.get(f"/api/runs/{run_id}/export_preflight").json()
    assert body["clean"] is False
    assert body["advisory_count"] == 1


# --------------------------------------------------------------------------
# what it must not do
# --------------------------------------------------------------------------

def test_the_preflight_is_advisory_and_never_blocks(client_run):
    """Partial output stays downloadable (Step 7.3). `blocking_count` means
    a person should look, not that a button is disabled."""
    client, run_id, db = client_run
    _seed_source(db, run_id)
    body = client.get(f"/api/runs/{run_id}/export_preflight").json()
    assert body["blocking_count"] > 0
    assert client.get(f"/api/runs/{run_id}/notes_integrity").status_code == 200


def test_the_preflight_reads_the_exporter_rather_than_decorating_anything():
    """Step 9.1 — do not move mtool/notes_decorate.py or clipboard.ts unless
    required, and they move together when they do (gotcha #16).

    The preflight reads what the exporter already produced. It must not call a
    decorator itself: a second decoration path is exactly how the mTool paste
    and the clipboard paste drift apart.
    """
    src = Path("notes/export_preflight.py").read_text(encoding="utf-8")
    for fn in ("decorate_notes_html", "decorateHtmlForClipboard",
               "_fill_undeclared_borders_white", "_fit_table_width"):
        assert fn not in src, f"{fn} is a decorator entry point"


def test_the_preflight_measures_the_theme_the_fill_will_use():
    """A new consumer resolves through firm_theme(), never by re-reading the
    env var — the mistake that made the formatter agent reason about a boxed
    grey grid over a ruled one (gotcha #16)."""
    src = Path("notes/export_preflight.py").read_text(encoding="utf-8")
    assert "firm_theme" in src
    assert "XBRL_NOTES_TABLE_STYLE" not in src


def test_an_unknown_run_is_404(client_run):
    client, _run_id, _db = client_run
    assert client.get("/api/runs/999999/export_preflight").status_code == 404


def test_a_clean_run_says_so(client_run):
    client, run_id, _db = client_run
    body = client.get(f"/api/runs/{run_id}/export_preflight").json()
    assert body["clean"] is True
    assert body["items"] == []
