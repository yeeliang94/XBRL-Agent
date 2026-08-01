"""Storing and acting on an integrity verdict — plan Phase 7, Steps 7.3 / 7.4.

The two things that must be true of a stored verdict:

* it carries the rules and the mode that produced it, so a result read months
  later is interpretable under those rules rather than whatever the rules are
  then;
* `shadow` computes exactly the same verdict as `enforce` and changes nothing.
  A middle mode that behaved differently would tell you nothing about what
  `enforce` will do.
"""
from __future__ import annotations

import pytest

from db import repository as repo
from db.schema import init_db
from notes import integrity, integrity_runner, source_write
from notes import source_repository as srepo
from notes.source_models import (
    Disposition,
    IntegrityMode,
    OwnerKind,
    SourceBlock,
    SourceNote,
)

BLOCKS = [
    SourceBlock(block_id="b1", block_kind="heading", reading_order=0,
                canonical_html="<h3>5. Receivables</h3>", source_note_id="n5",
                owner_kind=OwnerKind.NOTE),
    SourceBlock(block_id="b2", block_kind="paragraph", reading_order=1,
                canonical_html="<p>Stated at cost.</p>", source_note_id="n5",
                owner_kind=OwnerKind.NOTE),
    SourceBlock(block_id="f1", block_kind="paragraph", reading_order=2,
                canonical_html="<p>Company Sdn Bhd</p>",
                owner_kind=OwnerKind.FURNITURE),
]


@pytest.fixture()
def run_with_source(tmp_path):
    db = tmp_path / "audit.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
        gen = srepo.begin_generation(conn, run_id, input_kind="docx_html")
        srepo.write_blocks(conn, gen, BLOCKS)
        srepo.write_notes(conn, gen, [
            SourceNote(source_note_id="n5", top_note_num="5",
                       title="Receivables"),
        ])
        srepo.activate_generation(conn, gen)
        srepo.record_disposition(
            conn, run_id, gen, "f1", Disposition.EXCLUDED,
            reason_code="PAGE_HEADER",
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes", row=10, label="Receivables",
            html="", evidence=None, source_pages=[],
        )
        yield conn, run_id, gen


def _cover_the_note(conn, run_id, gen):
    source_write.write_cell_from_blocks(
        conn, run_id=run_id, generation_id=gen, sheet="Notes", row=10,
        block_ids=["b1", "b2"], label="Receivables",
    )


# --------------------------------------------------------------------------
# the snapshot
# --------------------------------------------------------------------------

def test_an_unwritten_note_reports_its_parts_unresolved(run_with_source):
    conn, run_id, gen = run_with_source
    result = integrity.run_checks(
        integrity_runner.build_input(conn, run_id, gen, scout_available=True)
    )
    assert result.requires_review is True
    unresolved = {b for f in result.findings for b in f.block_ids}
    assert {"b1", "b2"} <= unresolved
    assert "f1" not in unresolved, "settled furniture is not a gap"


def test_writing_the_note_clears_the_findings(run_with_source):
    conn, run_id, gen = run_with_source
    _cover_the_note(conn, run_id, gen)
    result = integrity.run_checks(
        integrity_runner.build_input(conn, run_id, gen, scout_available=True)
    )
    assert result.findings == []


def test_cells_with_no_source_parts_are_not_checked(run_with_source):
    """A cell an agent authored the old way has no block ids, so it has no
    render to match against — checking it would invent a failure."""
    conn, run_id, gen = run_with_source
    repo.upsert_notes_cell(
        conn, run_id=run_id, sheet="Notes", row=99, label="Authored",
        html="<p>hand written</p>",
    )
    inp = integrity_runner.build_input(conn, run_id, gen)
    assert all(c.row != 99 for c in inp.cells)


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def test_a_stored_verdict_carries_its_rules_and_its_mode(run_with_source):
    conn, run_id, gen = run_with_source
    integrity_runner.run_and_store(
        conn, run_id, gen, mode=IntegrityMode.SHADOW, scout_available=True
    )
    stored = integrity_runner.latest_result(conn, run_id)
    assert stored["rule_version"] == integrity.RULE_VERSION
    assert stored["mode"] == "shadow"
    assert stored["findings"], "the reasons are stored, not just the count"


def test_the_counts_stored_match_the_coverage_counts(run_with_source):
    conn, run_id, gen = run_with_source
    _cover_the_note(conn, run_id, gen)
    integrity_runner.run_and_store(
        conn, run_id, gen, mode=IntegrityMode.ENFORCE, scout_available=True
    )
    stored = integrity_runner.latest_result(conn, run_id)
    counts = srepo.coverage_counts(conn, gen)
    assert stored["blocks_total"] == counts["total"] == 3
    assert stored["blocks_included"] == counts["included"] == 2
    assert stored["blocks_excluded"] == counts["excluded"] == 1
    assert stored["blocks_unresolved"] == 0


def test_structured_consumed_has_its_own_column(run_with_source):
    """Peer review found this disposition had nowhere to land. A figure read
    into a field and a paragraph rendered into a disclosure are different
    outcomes and are judged by different coverage rules."""
    conn, run_id, gen = run_with_source
    srepo.record_disposition(
        conn, run_id, gen, "b1", Disposition.STRUCTURED_CONSUMED,
        sheet="CorpInfo", row=4,
    )
    srepo.record_disposition(conn, run_id, gen, "b2", Disposition.ROUTED)
    integrity_runner.run_and_store(conn, run_id, gen, mode=IntegrityMode.SHADOW)
    stored = integrity_runner.latest_result(conn, run_id)
    assert stored["blocks_structured_consumed"] == 1
    assert stored["blocks_routed"] == 1
    assert stored["blocks_included"] == 0


def test_a_clean_run_stores_status_complete(run_with_source):
    conn, run_id, gen = run_with_source
    _cover_the_note(conn, run_id, gen)
    integrity_runner.run_and_store(
        conn, run_id, gen, mode=IntegrityMode.ENFORCE, scout_available=True
    )
    stored = integrity_runner.latest_result(conn, run_id)
    assert stored["status"] == "complete"
    assert stored["requires_review"] == 0


def test_repeated_checks_append_rather_than_replace(run_with_source):
    """Attempt 2 must not erase attempt 1 — the point of the retry is that
    somebody can see what it changed."""
    conn, run_id, gen = run_with_source
    integrity_runner.run_and_store(conn, run_id, gen, mode=IntegrityMode.SHADOW)
    _cover_the_note(conn, run_id, gen)
    integrity_runner.run_and_store(
        conn, run_id, gen, mode=IntegrityMode.SHADOW, attempt=2,
        scout_available=True,
    )
    rows = conn.execute(
        "SELECT attempt, status FROM notes_integrity_runs WHERE run_id = ? "
        "ORDER BY attempt", (run_id,),
    ).fetchall()
    assert [r["attempt"] for r in rows] == [1, 2]
    assert [r["status"] for r in rows] == ["needs_review", "complete"]


def test_latest_result_is_none_before_any_check(tmp_path):
    db = tmp_path / "empty.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.pdf", session_id="s", output_dir=str(tmp_path / "s")
        )
        assert integrity_runner.latest_result(conn, run_id) is None


# --------------------------------------------------------------------------
# Step 7.3 — what tips the run
# --------------------------------------------------------------------------

def test_shadow_computes_the_same_verdict_and_changes_nothing(run_with_source):
    conn, run_id, gen = run_with_source
    shadow = integrity_runner.run_and_store(
        conn, run_id, gen, mode=IntegrityMode.SHADOW, scout_available=True
    )
    enforce = integrity_runner.run_and_store(
        conn, run_id, gen, mode=IntegrityMode.ENFORCE, attempt=2,
        scout_available=True,
    )
    assert shadow.requires_review == enforce.requires_review is True
    assert integrity_runner.tips_run_status(shadow, IntegrityMode.SHADOW) is False
    assert integrity_runner.tips_run_status(enforce, IntegrityMode.ENFORCE) is True


def test_off_never_tips_the_run(run_with_source):
    conn, run_id, gen = run_with_source
    result = integrity.run_checks(integrity_runner.build_input(conn, run_id, gen))
    assert integrity_runner.tips_run_status(result, IntegrityMode.OFF) is False


def test_a_clean_verdict_does_not_tip_even_in_enforce(run_with_source):
    conn, run_id, gen = run_with_source
    _cover_the_note(conn, run_id, gen)
    result = integrity.run_checks(
        integrity_runner.build_input(conn, run_id, gen, scout_available=True)
    )
    assert integrity_runner.tips_run_status(result, IntegrityMode.ENFORCE) is False


# --------------------------------------------------------------------------
# Step 7.2 — the targeted retry
# --------------------------------------------------------------------------

def test_the_retry_list_names_the_blocks_that_are_actually_missing(run_with_source):
    conn, run_id, gen = run_with_source
    result = integrity.run_checks(
        integrity_runner.build_input(conn, run_id, gen, scout_available=True)
    )
    assert set(integrity.missing_block_ids(result)) == {"b1", "b2"}


def test_the_retry_list_is_empty_once_the_note_is_covered(run_with_source):
    conn, run_id, gen = run_with_source
    _cover_the_note(conn, run_id, gen)
    result = integrity.run_checks(
        integrity_runner.build_input(conn, run_id, gen, scout_available=True)
    )
    assert integrity.missing_block_ids(result) == []


# --------------------------------------------------------------------------
# Step 7.4 — recompute without a rerun
# --------------------------------------------------------------------------

def test_a_manual_disposition_updates_the_counts_without_rerunning(run_with_source):
    conn, run_id, gen = run_with_source
    before = integrity_runner.run_and_store(
        conn, run_id, gen, mode=IntegrityMode.ENFORCE, scout_available=True
    )
    assert before.requires_review is True

    for bid in ("b1", "b2"):
        srepo.record_disposition(
            conn, run_id, gen, bid, Disposition.EXCLUDED,
            reason_code="OUTSIDE_SELECTED_FILING_SCOPE", actor="human",
        )
    after = integrity_runner.run_and_store(
        conn, run_id, gen, mode=IntegrityMode.ENFORCE, attempt=2,
        scout_available=True,
    )
    assert after.requires_review is False


def test_saying_a_part_is_unreadable_does_not_settle_it(run_with_source):
    """UNREADABLE_NEEDS_REVIEW describes a problem. Letting it resolve a block
    would turn the review queue into a way to make the number go green."""
    conn, run_id, gen = run_with_source
    for bid in ("b1", "b2"):
        srepo.record_disposition(
            conn, run_id, gen, bid, Disposition.EXCLUDED,
            reason_code="UNREADABLE_NEEDS_REVIEW", actor="human",
        )
    result = integrity_runner.run_and_store(
        conn, run_id, gen, mode=IntegrityMode.ENFORCE, scout_available=True
    )
    assert result.requires_review is True
