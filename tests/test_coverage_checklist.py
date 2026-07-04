"""notes/coverage_checklist.py — pure-builder pins (Phase 3 of
docs/PLAN-notes-coverage-and-routing.md).

The checklist is the holistic inventory × placements reconciliation:
- a note placed on ANY notes sheet counts as placed (the "not on Sheet 12
  but on Sheet 11" case that motivated the feature);
- internal numbering holes become suspected_gap rows;
- an empty inventory is a LOUD `inventory_available=False`, never an
  empty-but-green list;
- sub-refs are cited/not_verified at builder level (reviewer verdicts are
  Phase 5);
- placements are classified primary / fan_out / carve_out — the only two
  always-visible child-row cases per PRD Decision 5.
"""
from __future__ import annotations

from notes.coverage_checklist import (
    Checklist,
    KIND_CARVE_OUT,
    KIND_FAN_OUT,
    KIND_PRIMARY,
    STATUS_MISSING,
    STATUS_PLACED,
    STATUS_SKIPPED,
    STATUS_SUSPECTED_GAP,
    SUBNOTE_CITED,
    SUBNOTE_NOT_VERIFIED,
    build_draft_checklist,
)


S10 = "Notes-CI"
S11 = "Notes-SummaryofAccPol"
S12 = "Notes-Listofnotes"


def _inv(note_num, title="", subs=(), lo=None, hi=None):
    return {
        "note_num": note_num, "title": title,
        "subnote_refs": list(subs), "page_lo": lo, "page_hi": hi,
    }


def _entry(sheet, row, refs, label=""):
    return {"sheet": sheet, "row": row, "row_label": label,
            "source_note_refs": refs}


def _row(checklist: Checklist, note_num: int):
    return next(r for r in checklist.rows if r.note_num == note_num)


def test_placed_missing_and_skipped_statuses():
    cl = build_draft_checklist(
        inventory_rows=[_inv(1, "Corporate information"),
                        _inv(2, "Investment properties"),
                        _inv(3, "Contingent liabilities")],
        provenance_entries=[_entry(S10, 6, ["1"])],
        skip_receipts=[{"note_num": 3, "reason": "no matching template row"}],
    )
    assert _row(cl, 1).status == STATUS_PLACED
    assert _row(cl, 2).status == STATUS_MISSING
    skipped = _row(cl, 3)
    assert skipped.status == STATUS_SKIPPED
    assert skipped.reason == "no matching template row"


def test_placement_on_any_sheet_counts_as_placed():
    """The motivating case: a note absent from Sheet 12 but present on
    Sheet 11 is placed, with its actual location recorded."""
    cl = build_draft_checklist(
        inventory_rows=[_inv(2, "Basis of preparation")],
        provenance_entries=[_entry(S11, 8, ["2"])],
    )
    row = _row(cl, 2)
    assert row.status == STATUS_PLACED
    assert [(p.sheet, p.row) for p in row.placements] == [(S11, 8)]


def test_internal_numbering_hole_becomes_suspected_gap():
    cl = build_draft_checklist(
        inventory_rows=[_inv(12), _inv(14)],
        provenance_entries=[],
    )
    gap = _row(cl, 13)
    assert gap.status == STATUS_SUSPECTED_GAP
    assert "12 → 14" in gap.reason
    # Rows sort numerically so the gap sits between its neighbours.
    assert [r.note_num for r in cl.rows] == [12, 13, 14]


def test_no_gap_flagged_outside_observed_range():
    """Holes before the first / after the last observed note are not
    knowable from the sequence — documented v1 blind spot."""
    cl = build_draft_checklist(
        inventory_rows=[_inv(3), _inv(4)],
        provenance_entries=[],
    )
    assert all(r.status != STATUS_SUSPECTED_GAP for r in cl.rows)


def test_empty_inventory_is_loud_not_green():
    cl = build_draft_checklist(inventory_rows=[], provenance_entries=[])
    assert cl.inventory_available is False
    assert cl.rows == []
    assert cl.to_dict()["inventory_available"] is False


def test_subref_states_cited_vs_not_verified():
    """Citation reconciliation mirrors detect_subnote_coverage_gaps
    semantics — '(a)' cited normalises to 'a'; the uncited sub-ref starts
    as not_verified pending the reviewer's content check."""
    cl = build_draft_checklist(
        inventory_rows=[_inv(9, "Investment properties", subs=["9.1", "(a)", "(b)"])],
        provenance_entries=[_entry(S12, 48, ["9", "9.1", "(a)"])],
    )
    states = {s.subnote_ref: s.state for s in _row(cl, 9).subnotes}
    assert states == {
        "9.1": SUBNOTE_CITED,
        "(a)": SUBNOTE_CITED,
        "(b)": SUBNOTE_NOT_VERIFIED,
    }


def test_fully_uncited_blob_write_leaves_all_subrefs_not_verified():
    """The combined-cell blind spot: agent cites only the bare note number
    → every sub-ref stays not_verified for the reviewer to check."""
    cl = build_draft_checklist(
        inventory_rows=[_inv(9, subs=["(a)", "(b)"])],
        provenance_entries=[_entry(S12, 48, ["9"])],
    )
    assert all(
        s.state == SUBNOTE_NOT_VERIFIED for s in _row(cl, 9).subnotes
    )


def test_policies_fan_out_placements_classified_fan_out():
    cl = build_draft_checklist(
        inventory_rows=[_inv(3, "Significant accounting policies",
                             subs=["3.2", "3.5"])],
        provenance_entries=[
            _entry(S11, 10, ["3.2"]),
            _entry(S11, 22, ["3.5"]),
        ],
    )
    kinds = {p.kind for p in _row(cl, 3).placements}
    assert kinds == {KIND_FAN_OUT}


def test_carve_out_classified_on_topical_note():
    """Direction 1: note 9 disclosure on Sheet 12 + labelled policy
    sub-section on Sheet 11 → the Sheet-11 placement is carve_out, the
    Sheet-12 one primary."""
    cl = build_draft_checklist(
        inventory_rows=[_inv(9, "Investment properties")],
        provenance_entries=[
            _entry(S12, 48, ["9"]),
            _entry(S11, 14, ["9"]),
        ],
    )
    by_sheet = {p.sheet: p.kind for p in _row(cl, 9).placements}
    assert by_sheet == {S12: KIND_PRIMARY, S11: KIND_CARVE_OUT}


def test_lone_policies_placement_is_primary():
    cl = build_draft_checklist(
        inventory_rows=[_inv(2, "Basis of preparation")],
        provenance_entries=[_entry(S11, 8, ["2"])],
    )
    assert [p.kind for p in _row(cl, 2).placements] == [KIND_PRIMARY]


def test_duplicate_coords_deduped_and_counts_summarise():
    cl = build_draft_checklist(
        inventory_rows=[_inv(5), _inv(6), _inv(8)],
        provenance_entries=[
            _entry(S12, 60, ["5"]),
            _entry(S12, 60, ["5.1"]),  # second payload, same row
        ],
        skip_receipts=[{"note_num": 6, "reason": "belongs on face"}],
    )
    assert len(_row(cl, 5).placements) == 1
    counts = cl.counts()
    assert counts[STATUS_PLACED] == 1
    assert counts[STATUS_SKIPPED] == 1
    assert counts[STATUS_MISSING] == 1
    assert counts[STATUS_SUSPECTED_GAP] == 1  # note 7 hole


def test_duplicate_subrefs_are_deduped_for_persistence():
    """A scout inventory that lists the same raw sub-ref twice must not produce
    two DB child rows — the notes_coverage_rows UNIQUE index would reject the
    whole wholesale write and silently disable coverage (code-review C1)."""
    from notes.coverage_checklist import checklist_to_db_rows

    cl = build_draft_checklist(
        inventory_rows=[_inv(3, "Policies", subs=["(a)", "(a)", "(b)"])],
        provenance_entries=[_entry(S11, 10, ["3", "(a)"])],
    )
    refs = [s.subnote_ref for s in _row(cl, 3).subnotes]
    assert refs == ["(a)", "(b)"]  # order-preserving dedup
    child_refs = [
        r["subnote_ref"] for r in checklist_to_db_rows(cl)
        if r["subnote_ref"] is not None
    ]
    assert child_refs == ["(a)", "(b)"]  # no duplicate child rows


def test_to_dict_round_trip_shape():
    cl = build_draft_checklist(
        inventory_rows=[_inv(1, "Corporate information", subs=["(a)"], lo=8, hi=9)],
        provenance_entries=[_entry(S10, 6, ["1", "(a)"], "Company details")],
    )
    d = cl.to_dict()
    assert d["inventory_available"] is True
    row = d["rows"][0]
    assert row["note_num"] == 1
    assert row["status"] == STATUS_PLACED
    assert row["page_lo"] == 8 and row["page_hi"] == 9
    assert row["placements"][0] == {
        "sheet": S10, "row": 6, "row_label": "Company details",
        "kind": KIND_PRIMARY,
    }
    assert row["subnotes"] == [{"subnote_ref": "(a)", "state": SUBNOTE_CITED}]
