"""Source-generation persistence — plan Phase 3, Steps 3.2 / 3.3.

The invariant that matters: **a run has at most one ACTIVE generation, and a
failed build never costs you the one you had.** Everything downstream counts
blocks within the active generation, so two active generations would make the
completeness number meaningless, and losing the previous one on a failed rerun
would destroy a good reading in exchange for a broken one.
"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_db
from notes.source_models import (
    Disposition,
    GenerationStatus,
    OwnerKind,
    SourceBlock,
    SourceNote,
)
from notes import source_repository as srepo


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "audit.sqlite"
    init_db(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO runs(pdf_filename, status, created_at) VALUES ('x.docx','draft','')"
    )
    conn.commit()
    yield conn
    conn.close()


def _run_id(conn) -> int:
    return int(conn.execute("SELECT id FROM runs LIMIT 1").fetchone()[0])


def _blocks(n: int = 3) -> list[SourceBlock]:
    return [
        SourceBlock(
            block_id=f"b{i}", block_kind="paragraph", reading_order=i,
            canonical_html=f"<p>{i}</p>", source_note_id="n1",
            owner_kind=OwnerKind.NOTE,
        )
        for i in range(n)
    ]


# --------------------------------------------------------------------------
# build → activate
# --------------------------------------------------------------------------

def test_a_new_generation_starts_as_building(db):
    gen = srepo.begin_generation(db, _run_id(db), input_kind="docx_html")
    row = srepo.fetch_generation(db, gen)
    assert row["status"] == GenerationStatus.BUILDING.value
    assert srepo.active_generation(db, _run_id(db)) is None


def test_activation_makes_it_the_only_active_one(db):
    run = _run_id(db)
    g1 = srepo.begin_generation(db, run, input_kind="docx_html")
    srepo.write_blocks(db, g1, _blocks())
    srepo.activate_generation(db, g1)

    g2 = srepo.begin_generation(db, run, input_kind="docx_html")
    srepo.write_blocks(db, g2, _blocks())
    srepo.activate_generation(db, g2)

    assert srepo.active_generation(db, run)["id"] == g2
    assert srepo.fetch_generation(db, g1)["status"] == GenerationStatus.SUPERSEDED.value
    actives = db.execute(
        "SELECT COUNT(*) FROM notes_source_generations "
        "WHERE run_id = ? AND status = 'active'", (run,),
    ).fetchone()[0]
    assert actives == 1


def test_generation_numbers_increment_per_run(db):
    run = _run_id(db)
    a = srepo.begin_generation(db, run, input_kind="docx_html")
    b = srepo.begin_generation(db, run, input_kind="docx_html")
    assert srepo.fetch_generation(db, a)["generation_no"] == 1
    assert srepo.fetch_generation(db, b)["generation_no"] == 2


def test_a_failed_build_leaves_the_previous_generation_active(db):
    run = _run_id(db)
    good = srepo.begin_generation(db, run, input_kind="docx_html")
    srepo.write_blocks(db, good, _blocks())
    srepo.activate_generation(db, good)

    bad = srepo.begin_generation(db, run, input_kind="docx_html")
    srepo.fail_generation(db, bad, "EXTRACTION_TRUNCATED")

    assert srepo.active_generation(db, run)["id"] == good
    assert srepo.fetch_generation(db, bad)["status"] == GenerationStatus.FAILED.value
    assert srepo.fetch_generation(db, bad)["failure_code"] == "EXTRACTION_TRUNCATED"


def test_activating_an_empty_generation_is_refused(db):
    """A manifest with no blocks would report 0 of 0 handled — a perfect score
    for having read nothing."""
    gen = srepo.begin_generation(db, _run_id(db), input_kind="docx_html")
    with pytest.raises(ValueError):
        srepo.activate_generation(db, gen)
    assert srepo.active_generation(db, _run_id(db)) is None


def test_activating_a_failed_generation_is_refused(db):
    gen = srepo.begin_generation(db, _run_id(db), input_kind="docx_html")
    srepo.write_blocks(db, gen, _blocks())
    srepo.fail_generation(db, gen, "BOOM")
    with pytest.raises(ValueError):
        srepo.activate_generation(db, gen)


class _FlakyConnection(sqlite3.Connection):
    """Raises on one chosen statement. `sqlite3.Connection.execute` is a
    read-only C attribute, so the failure has to be injected by subclassing
    rather than monkeypatching the instance."""

    fail_on: str | None = None

    def execute(self, sql, *args, **kwargs):  # type: ignore[override]
        if self.fail_on and self.fail_on in str(sql):
            raise sqlite3.OperationalError("disk gone")
        return super().execute(sql, *args, **kwargs)


def test_activation_is_atomic_when_supersede_fails(tmp_path):
    """If the supersede half cannot complete, the new generation must NOT be
    left active alongside the old one — better no activation than two."""
    path = tmp_path / "flaky.sqlite"
    init_db(path)
    conn = sqlite3.connect(str(path), factory=_FlakyConnection)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT INTO runs(pdf_filename, status, created_at) "
            "VALUES ('x.docx','draft','')"
        )
        conn.commit()
        run = _run_id(conn)

        old = srepo.begin_generation(conn, run, input_kind="docx_html")
        srepo.write_blocks(conn, old, _blocks())
        srepo.activate_generation(conn, old)

        new = srepo.begin_generation(conn, run, input_kind="docx_html")
        srepo.write_blocks(conn, new, _blocks())

        conn.fail_on = "status = 'superseded'"
        with pytest.raises(sqlite3.OperationalError):
            srepo.activate_generation(conn, new)
        conn.fail_on = None

        assert srepo.active_generation(conn, run)["id"] == old
        assert (
            srepo.fetch_generation(conn, new)["status"]
            != GenerationStatus.ACTIVE.value
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------
# blocks and notes
# --------------------------------------------------------------------------

def test_blocks_round_trip_in_reading_order(db):
    gen = srepo.begin_generation(db, _run_id(db), input_kind="docx_html")
    srepo.write_blocks(db, gen, list(reversed(_blocks(4))))
    got = srepo.fetch_blocks(db, gen)
    assert [b["block_id"] for b in got] == ["b0", "b1", "b2", "b3"]


def test_writing_blocks_twice_replaces_rather_than_duplicates(db):
    """A retry inside one generation must not double the manifest — that would
    inflate the denominator and make coverage look worse than it is."""
    gen = srepo.begin_generation(db, _run_id(db), input_kind="docx_html")
    srepo.write_blocks(db, gen, _blocks(3))
    srepo.write_blocks(db, gen, _blocks(3))
    assert len(srepo.fetch_blocks(db, gen)) == 3


def test_notes_round_trip(db):
    gen = srepo.begin_generation(db, _run_id(db), input_kind="docx_html")
    srepo.write_notes(db, gen, [
        SourceNote(source_note_id="n1", top_note_num="1", title="Corporate info",
                   block_ids=["b0", "b1"], page_lo=3, page_hi=4),
    ])
    notes = srepo.fetch_notes(db, gen)
    assert len(notes) == 1
    assert notes[0]["title"] == "Corporate info"


# --------------------------------------------------------------------------
# usages + append-only audit
# --------------------------------------------------------------------------

def test_recording_a_disposition_writes_current_state_and_history(db):
    run = _run_id(db)
    gen = srepo.begin_generation(db, run, input_kind="docx_html")
    srepo.write_blocks(db, gen, _blocks(1))

    srepo.record_disposition(
        db, run, gen, "b0", Disposition.INCLUDED, actor="agent", sheet="Notes", row=10,
    )
    srepo.record_disposition(
        db, run, gen, "b0", Disposition.ROUTED, actor="human",
        note="belongs on the policies sheet",
    )

    usage = srepo.fetch_usages(db, gen)
    assert len(usage) == 1, "current state is one row per block"
    assert usage[0]["disposition"] == "routed"

    events = db.execute(
        "SELECT from_disposition, to_disposition, actor FROM "
        "notes_disposition_events WHERE block_id='b0' ORDER BY id"
    ).fetchall()
    assert [(e["from_disposition"], e["to_disposition"]) for e in events] == [
        (None, "included"), ("included", "routed"),
    ]
    assert events[1]["actor"] == "human"


def test_an_excluded_block_without_a_reason_is_refused(db):
    run = _run_id(db)
    gen = srepo.begin_generation(db, run, input_kind="docx_html")
    srepo.write_blocks(db, gen, _blocks(1))
    with pytest.raises(ValueError):
        srepo.record_disposition(db, run, gen, "b0", Disposition.EXCLUDED, actor="agent")


def test_a_disposition_for_an_unknown_block_is_refused(db):
    """Guards the fabricated-id case: an agent naming a block that is not in
    this generation must not create a usage row out of thin air."""
    run = _run_id(db)
    gen = srepo.begin_generation(db, run, input_kind="docx_html")
    srepo.write_blocks(db, gen, _blocks(1))
    with pytest.raises(ValueError):
        srepo.record_disposition(db, run, gen, "not-a-block", Disposition.INCLUDED,
                                 actor="agent")


def test_coverage_counts_treat_unreadable_as_unresolved(db):
    run = _run_id(db)
    gen = srepo.begin_generation(db, run, input_kind="docx_html")
    srepo.write_blocks(db, gen, _blocks(3))
    srepo.record_disposition(db, run, gen, "b0", Disposition.INCLUDED, actor="agent")
    srepo.record_disposition(db, run, gen, "b1", Disposition.EXCLUDED,
                             reason_code="PAGE_FOOTER", actor="agent")
    srepo.record_disposition(db, run, gen, "b2", Disposition.EXCLUDED,
                             reason_code="UNREADABLE_NEEDS_REVIEW", actor="agent")

    counts = srepo.coverage_counts(db, gen)
    assert counts["total"] == 3
    assert counts["included"] == 1
    assert counts["excluded"] == 2
    assert counts["unresolved"] == 1, "unreadable does not settle a block"
    assert counts["resolved"] == 2


def test_a_block_with_no_usage_counts_as_unresolved(db):
    """Silence is not consent: a block nobody dispositioned is outstanding."""
    run = _run_id(db)
    gen = srepo.begin_generation(db, run, input_kind="docx_html")
    srepo.write_blocks(db, gen, _blocks(2))
    srepo.record_disposition(db, run, gen, "b0", Disposition.INCLUDED, actor="agent")
    counts = srepo.coverage_counts(db, gen)
    assert counts["total"] == 2
    assert counts["unresolved"] == 1


def test_coverage_survives_a_disposition_value_it_does_not_know(db):
    """The `disposition` column deliberately has no CHECK constraint (gotcha
    #11) so a future value can land without a full-table migration. The reader
    must therefore not crash on one — it counts it as unresolved, which is the
    safe direction: an unrecognised decision is not a decision."""
    run = _run_id(db)
    gen = srepo.begin_generation(db, run, input_kind="docx_html")
    srepo.write_blocks(db, gen, _blocks(2))
    srepo.record_disposition(db, run, gen, "b0", Disposition.INCLUDED, actor="agent")
    db.execute(
        "INSERT INTO notes_block_usages(run_id, generation_id, block_id, disposition) "
        "VALUES (?, ?, 'b1', 'invented_next_year')",
        (run, gen),
    )
    db.commit()

    counts = srepo.coverage_counts(db, gen)
    assert counts["total"] == 2
    assert counts["included"] == 1
    assert counts["unresolved"] == 1
    assert counts["resolved"] == 1
