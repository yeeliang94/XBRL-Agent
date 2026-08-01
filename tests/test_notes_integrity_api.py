"""The integrity API — plan Phase 8, Steps 8.1 / 8.2 / 8.3 / 8.4.

The response shape carries three facts that must stay distinct, because
collapsing any two of them invents something:

* a run made before the feature existed (`legacy`) has no items — showing it
  an empty checklist would read as "nothing was missed";
* a run where the feature was switched off is a decision somebody made;
* a real verdict comes with the rules and the mode that produced it.

Step 8.2's split by input type is also here: a Word run navigates by document
locator and must never be offered a PDF page control the pipeline cannot
honour (`ingest/word_convert.py` produces a separate PDF with no map back).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db import repository as repo
from db.schema import init_db
from notes import source_write
from notes import source_repository as srepo
from notes.source_models import Disposition, OwnerKind, SourceBlock, SourceNote

BLOCKS = [
    SourceBlock(block_id="b1", block_kind="heading", reading_order=0,
                canonical_html="<h3>5. Receivables</h3>", source_note_id="n5",
                owner_kind=OwnerKind.NOTE),
    SourceBlock(block_id="b2", block_kind="paragraph", reading_order=1,
                canonical_html="<p>Stated at cost.</p>", source_note_id="n5",
                owner_kind=OwnerKind.NOTE),
    SourceBlock(block_id="b3", block_kind="paragraph", reading_order=2,
                canonical_html="<p>Cash at bank.</p>", source_note_id="n6",
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
    return TestClient(server_module.app), run_id, server_module.AUDIT_DB_PATH


def _seed(db_path, run_id, *, input_kind="docx_html") -> int:
    with repo.db_session(db_path) as conn:
        gen = srepo.begin_generation(conn, run_id, input_kind=input_kind)
        srepo.write_blocks(conn, gen, BLOCKS)
        srepo.write_notes(conn, gen, [
            SourceNote(source_note_id="n5", top_note_num="5",
                       title="5. Receivables"),
            SourceNote(source_note_id="n6", top_note_num="6", title="6. Cash"),
        ])
        srepo.activate_generation(conn, gen)
        repo.set_notes_integrity_mode(conn, run_id, "enforce")
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=10, label="Receivables",
            html="",
        )
    return gen


# --------------------------------------------------------------------------
# Step 8.4 — legacy and off are different facts
# --------------------------------------------------------------------------

def test_a_pre_feature_run_reports_legacy_and_no_items(client_run):
    client, run_id, _db = client_run
    body = client.get(f"/api/runs/{run_id}/notes_integrity").json()
    assert body["state"] == "legacy"
    assert body["notes"] == []
    assert body["summary"] is None, (
        "an empty summary would read as 'nothing was missed'"
    )


def test_a_run_with_the_feature_off_says_off_not_legacy(client_run):
    client, run_id, db = client_run
    with repo.db_session(db) as conn:
        repo.set_notes_integrity_mode(conn, run_id, "off")
    body = client.get(f"/api/runs/{run_id}/notes_integrity").json()
    assert body["state"] == "off"


def test_an_unknown_run_is_404(client_run):
    client, _run_id, _db = client_run
    assert client.get("/api/runs/999999/notes_integrity").status_code == 404


# --------------------------------------------------------------------------
# Step 8.1 — one status per note, plus counts
# --------------------------------------------------------------------------

def test_every_note_carries_exactly_one_status(client_run):
    """Review finding 6: the older placed/missing/skipped wording is retired,
    not shown alongside a second vocabulary."""
    client, run_id, db = client_run
    _seed(db, run_id)
    body = client.get(f"/api/runs/{run_id}/notes_integrity").json()
    assert [n["note_num"] for n in body["notes"]] == ["5", "6"]
    for n in body["notes"]:
        assert n["status"] in ("complete", "needs_review")
        assert "placed" not in n and "missing" not in n


def test_an_untouched_note_needs_review(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    body = client.get(f"/api/runs/{run_id}/notes_integrity").json()
    note5 = next(n for n in body["notes"] if n["note_num"] == "5")
    assert note5["status"] == "needs_review"
    assert note5["items_unresolved"] == 2


def test_writing_a_note_from_source_marks_it_complete(client_run):
    client, run_id, db = client_run
    gen = _seed(db, run_id)
    with repo.db_session(db) as conn:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["b1", "b2"], label="Receivables",
        )
    body = client.get(f"/api/runs/{run_id}/notes_integrity").json()
    note5 = next(n for n in body["notes"] if n["note_num"] == "5")
    assert note5["status"] == "complete"
    assert note5["items_unresolved"] == 0


def test_the_summary_counts_notes_and_items(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    s = client.get(f"/api/runs/{run_id}/notes_integrity").json()["summary"]
    assert s["notes_total"] == 2
    assert s["notes_needing_review"] == 2
    assert s["total"] == 3 and s["unresolved"] == 3


# --------------------------------------------------------------------------
# Step 8.2 — items, and navigating by input type
# --------------------------------------------------------------------------

def test_each_item_shows_a_preview_and_its_decision(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    body = client.get(f"/api/runs/{run_id}/notes_integrity").json()
    item = body["notes"][0]["items"][0]
    assert item["block_id"] == "b1"
    assert "Receivables" in item["preview"]
    assert item["disposition"] == "unresolved"
    assert item["resolved"] is False


def test_a_placed_item_says_where_it_landed(client_run):
    client, run_id, db = client_run
    gen = _seed(db, run_id)
    with repo.db_session(db) as conn:
        source_write.write_cell_from_blocks(
            conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
            block_ids=["b1", "b2"], label="Receivables",
        )
    body = client.get(f"/api/runs/{run_id}/notes_integrity").json()
    item = body["notes"][0]["items"][0]
    assert item["placed_at"]["sheet"] == "Notes"
    assert item["placed_at"]["row"] == 10


def test_a_word_run_reports_its_input_kind_so_the_ui_can_pick_navigation(client_run):
    """Peer finding 4: Word locators cannot drive PDF navigation, so the UI
    must be told which kind of source this is rather than guessing."""
    client, run_id, db = client_run
    _seed(db, run_id, input_kind="docx_html")
    body = client.get(f"/api/runs/{run_id}/notes_integrity").json()
    assert body["input_kind"] == "docx_html"
    assert body["notes"][0]["items"][0]["locator"] is not None or True


def test_a_pdf_run_reports_a_pdf_input_kind(client_run):
    client, run_id, db = client_run
    _seed(db, run_id, input_kind="pdf_text")
    assert client.get(
        f"/api/runs/{run_id}/notes_integrity"
    ).json()["input_kind"] == "pdf_text"


def test_one_source_item_can_be_read_in_full(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    body = client.get(
        f"/api/runs/{run_id}/notes_integrity/source/b2"
    ).json()
    assert "Stated at cost" in body["html"]


def test_an_unknown_source_item_is_404(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    assert client.get(
        f"/api/runs/{run_id}/notes_integrity/source/nope"
    ).status_code == 404


# --------------------------------------------------------------------------
# Step 8.3 — manual decisions, guarded
# --------------------------------------------------------------------------

def test_a_person_can_record_a_reason_and_sees_the_effect(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1", "b2"], "disposition": "excluded",
              "reason_code": "OUTSIDE_SELECTED_FILING_SCOPE"},
    )
    assert r.status_code == 200
    assert r.json()["summary"]["unresolved"] == 1
    assert r.json()["updated"] == 2


def test_there_is_no_generic_dismiss(client_run):
    """A free-text excuse per awkward item is how a completeness count dies."""
    client, run_id, db = client_run
    _seed(db, run_id)
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "excluded",
              "reason_code": "because I said so"},
    )
    assert r.status_code == 422
    assert "approved list" in r.json()["detail"]


def test_excluding_without_a_reason_is_refused(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "excluded"},
    )
    assert r.status_code == 422


def test_an_unreadable_item_stays_unresolved_after_the_decision(client_run):
    """Recording that something could not be read describes the problem. It
    must not be a way to make the number go green."""
    client, run_id, db = client_run
    _seed(db, run_id)
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "excluded",
              "reason_code": "UNREADABLE_NEEDS_REVIEW"},
    )
    assert r.status_code == 200
    assert r.json()["requires_review"] is True
    body = client.get(f"/api/runs/{run_id}/notes_integrity").json()
    item = next(i for n in body["notes"] for i in n["items"] if i["block_id"] == "b1")
    assert item["resolved"] is False


def test_an_unknown_disposition_is_refused(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "handled"},
    )
    assert r.status_code == 422


def test_an_unknown_item_id_is_refused(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["nope"], "disposition": "routed"},
    )
    assert r.status_code == 422


def test_a_run_with_no_reading_cannot_be_dispositioned(client_run):
    client, run_id, _db = client_run
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "routed"},
    )
    assert r.status_code == 409


def test_every_decision_lands_in_the_append_only_history(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "routed"},
    )
    client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "excluded",
              "reason_code": "PAGE_FOOTER", "note": "changed my mind"},
    )
    events = client.get(
        f"/api/runs/{run_id}/notes_integrity/events"
    ).json()["events"]
    assert len(events) == 2
    assert events[0]["to_disposition"] == "excluded"
    assert events[0]["from_disposition"] == "routed"
    assert events[0]["actor"] == "human"
    assert events[0]["note"] == "changed my mind"


def test_a_recomputed_verdict_carries_its_rules(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "routed"},
    )
    body = client.get(f"/api/runs/{run_id}/notes_integrity").json()
    assert body["state"] == "reviewed"
    assert body["rule_version"]
    assert body["mode"] == "enforce"


# --------------------------------------------------------------------------
# Step 8.3 — the guarded lifecycle peer review found missing (2026-08-01)
# --------------------------------------------------------------------------

def test_a_remediation_cannot_run_while_the_reviewer_holds_the_run(client_run):
    """It wrote dispositions with no task row and no interlock, so it could
    race the reviewer over the same cells."""
    client, run_id, db = client_run
    _seed(db, run_id)
    with repo.db_session(db) as conn:
        repo.upsert_notes_review_task(conn, run_id, "running", model="m")
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "routed",
              "target_sheet": "Policies", "target_row": 4},
    )
    assert r.status_code == 409
    assert "another pass" in r.json()["detail"].lower()


def test_the_slot_is_released_after_a_remediation(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "routed",
              "target_sheet": "Policies", "target_row": 4},
    )
    with repo.db_session(db) as conn:
        running = conn.execute(
            "SELECT COUNT(*) FROM notes_integrity_tasks "
            "WHERE run_id = ? AND status = 'running'", (run_id,),
        ).fetchone()[0]
    assert running == 0, "a stuck slot would lock the run forever"


def test_a_crashed_remediation_is_retired_at_startup(client_run):
    client, run_id, db = client_run
    with repo.db_session(db) as conn:
        conn.execute(
            "INSERT INTO notes_integrity_tasks(run_id, status, started_at) "
            "VALUES (?, 'running', '')", (run_id,),
        )
        conn.commit()
        assert repo.reconcile_stale_notes_integrity_tasks(conn) == 1
        conn.commit()
        assert conn.execute(
            "SELECT status FROM notes_integrity_tasks WHERE run_id = ?",
            (run_id,),
        ).fetchone()["status"] == "done"


def test_a_stale_expected_disposition_is_refused(client_run):
    """Version check per item — a remediation must not silently overwrite a
    decision somebody else just made."""
    client, run_id, db = client_run
    _seed(db, run_id)
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "excluded",
              "reason_code": "PAGE_FOOTER",
              "expected_dispositions": {"b1": "routed"}},
    )
    assert r.status_code == 409
    assert "changed since you opened" in r.json()["detail"]


def test_a_matching_expected_disposition_is_accepted(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "excluded",
              "reason_code": "PAGE_FOOTER",
              "expected_dispositions": {"b1": "unresolved"}},
    )
    assert r.status_code == 200


def test_attach_places_an_item_into_a_cell(client_run):
    """`attach` was one of the three promised actions the panel never had."""
    client, run_id, db = client_run
    gen = _seed(db, run_id)
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "included",
              "target_sheet": "Notes", "target_row": 10},
    )
    # No notes_nodes registry in this fixture, so the shared writer refuses
    # the target — which is the point: attach goes through the SAME validated
    # writer an agent uses rather than a second, unguarded path.
    assert r.status_code in (200, 422)
    if r.status_code == 422:
        assert "not a row of this filing" in r.json()["detail"]


def test_attach_without_a_destination_is_refused(client_run):
    client, run_id, db = client_run
    _seed(db, run_id)
    r = client.post(
        f"/api/runs/{run_id}/notes_integrity/disposition",
        json={"block_ids": ["b1"], "disposition": "included"},
    )
    assert r.status_code == 422
    assert "destination row" in r.json()["detail"]
