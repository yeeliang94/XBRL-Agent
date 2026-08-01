"""The integrity checks — plan Phase 7, Step 7.1.

Every check gets a fixture that FAILS it and a fixture that PASSES it. A check
with only a failing fixture can be satisfied by never firing; a check with only
a passing one can be satisfied by never looking.

The severity split is deliberate and tested: `unresolved` means something in
the source is unaccounted for and, in `enforce`, the run cannot finish clean.
`warning` means worth showing, never blocking. A hand edit is a warning — it is
a legitimate action, recorded, not a defect.
"""
from __future__ import annotations

import pytest

from notes import integrity as ig
from notes.source_models import Disposition, OwnerKind, SourceBlock, SourceNote


def _blk(bid: str, *, note="n1", owner=OwnerKind.NOTE, group=None, order=0):
    return SourceBlock(
        block_id=bid, block_kind="paragraph", reading_order=order,
        source_note_id=note, owner_kind=owner, table_group_id=group,
    )


def _usage(disposition: Disposition, reason=None) -> dict:
    return {"disposition": disposition.value, "reason_code": reason}


def _settled(*block_ids) -> dict:
    return {b: _usage(Disposition.INCLUDED) for b in block_ids}


def _clean() -> ig.IntegrityInput:
    """A run with nothing wrong. Every 'passes' test starts here so a check
    that fires on a clean run is caught immediately.

    Both blocks are PLACED as well as dispositioned. Verifying the disposition
    alone was the defect peer review found: a block could claim to be included
    at a cell it had been relinked out of, or that had been deleted, and the
    run verified clean over content nobody had.
    """
    return ig.IntegrityInput(
        blocks=[_blk("b1", order=0), _blk("b2", order=1)],
        notes=[SourceNote(source_note_id="n1", top_note_num="1",
                          block_ids=["b1", "b2"])],
        usages=_settled("b1", "b2"),
        placements={"b1": [("Notes", 10)], "b2": [("Notes", 10)]},
        live_cells=frozenset({("Notes", 10)}),
        cells=[ig.CellRecord(sheet="Notes", row=10, block_ids=["b1", "b2"],
                             rendered_sha256="x", current_sha256="x",
                             content_origin="source_exact",
                             rendered_chars=100, cap=30_000, note_num="1")],
        boundary_disagreements=[],
        scout_available=True,
        pages_expected=3, pages_processed=3,
    )


def test_a_clean_run_produces_no_findings_at_all():
    result = ig.run_checks(_clean())
    assert result.findings == []
    assert result.requires_review is False


def test_the_rule_version_is_stamped_on_every_result():
    assert ig.run_checks(_clean()).rule_version == ig.RULE_VERSION


# --------------------------------------------------------------------------
# one failing and one passing fixture per check
# --------------------------------------------------------------------------

def test_page_receipts_fires_when_pages_were_not_read():
    inp = _clean()
    inp.pages_processed = 1
    fs = ig.check_page_receipts(inp)
    assert len(fs) == 1 and fs[0].blocking


def test_page_receipts_is_quiet_when_every_page_was_read():
    assert ig.check_page_receipts(_clean()) == []


def test_block_ownership_fires_on_an_unowned_block():
    inp = _clean()
    inp.blocks = [_blk("b1", owner=OwnerKind.UNRESOLVED)]
    fs = ig.check_block_ownership(inp)
    assert len(fs) == 1 and fs[0].block_ids == ["b1"]


def test_block_ownership_fires_when_note_content_names_no_note():
    inp = _clean()
    inp.blocks = [_blk("b1", note=None)]
    assert ig.check_block_ownership(inp)


def test_block_ownership_is_quiet_when_every_block_has_an_owner():
    assert ig.check_block_ownership(_clean()) == []


def test_disposition_fires_when_a_block_has_no_decision():
    inp = _clean()
    inp.usages = _settled("b1")            # b2 never dispositioned
    fs = ig.check_dispositions(inp)
    assert [f.block_ids for f in fs] == [["b2"]]
    assert fs[0].blocking


def test_disposition_fires_on_an_unreadable_exclusion():
    """UNREADABLE_NEEDS_REVIEW describes a problem; it does not settle one."""
    inp = _clean()
    inp.usages = {
        "b1": _usage(Disposition.INCLUDED),
        "b2": _usage(Disposition.EXCLUDED, "UNREADABLE_NEEDS_REVIEW"),
    }
    fs = ig.check_dispositions(inp)
    assert len(fs) == 1 and fs[0].blocking


def test_disposition_fires_on_an_unknown_reason_code():
    """The column has no CHECK constraint, so an unrecognised code must fail
    closed rather than read as settled."""
    inp = _clean()
    inp.usages = {
        "b1": _usage(Disposition.INCLUDED),
        "b2": _usage(Disposition.EXCLUDED, "SOMETHING_FROM_A_NEWER_BUILD"),
    }
    assert ig.check_dispositions(inp)


def test_disposition_accepts_an_approved_exclusion():
    inp = _clean()
    inp.usages = {
        "b1": _usage(Disposition.INCLUDED),
        "b2": _usage(Disposition.EXCLUDED, "PAGE_FOOTER"),
    }
    assert ig.check_dispositions(inp) == []


def test_note_coverage_fires_when_part_of_a_note_is_unaccounted_for():
    inp = _clean()
    inp.usages = _settled("b1")
    fs = ig.check_prose_note_coverage(inp)
    assert len(fs) == 1
    assert fs[0].note_num == "1" and fs[0].block_ids == ["b2"]


def test_note_coverage_is_quiet_when_the_whole_note_is_settled():
    assert ig.check_prose_note_coverage(_clean()) == []


def test_table_group_fires_when_segments_are_handled_differently():
    """Half a table rendered and half dropped scores clean on a per-block
    count — the group is what makes it visible."""
    inp = _clean()
    inp.blocks = [_blk("t1", group="tg", order=0), _blk("t2", group="tg", order=1)]
    inp.usages = {
        "t1": _usage(Disposition.INCLUDED),
        "t2": _usage(Disposition.EXCLUDED, "PAGE_FOOTER"),
    }
    fs = ig.check_table_groups(inp)
    assert len(fs) == 1 and set(fs[0].block_ids) == {"t1", "t2"}


def test_table_group_is_quiet_when_segments_share_a_fate():
    inp = _clean()
    inp.blocks = [_blk("t1", group="tg", order=0), _blk("t2", group="tg", order=1)]
    inp.usages = _settled("t1", "t2")
    assert ig.check_table_groups(inp) == []


def test_note_continuity_fires_on_a_hole_in_the_numbering():
    inp = _clean()
    inp.notes = [
        SourceNote(source_note_id="n1", top_note_num="1"),
        SourceNote(source_note_id="n3", top_note_num="3"),
    ]
    fs = ig.check_note_continuity(inp)
    assert len(fs) == 1 and fs[0].note_num == "2"


def test_note_continuity_is_quiet_on_a_complete_sequence():
    inp = _clean()
    inp.notes = [
        SourceNote(source_note_id=f"n{i}", top_note_num=str(i))
        for i in (1, 2, 3)
    ]
    assert ig.check_note_continuity(inp) == []


def test_boundary_disagreement_blocks_the_run():
    """Step 4.4 / peer finding 3 — rev 1 only measured this. A mis-assigned
    block shows 100% completeness and a wrong answer, so it must gate."""
    inp = _clean()
    inp.boundary_disagreements = [
        {"kind": "missing_trailing", "detail": "scout listed note 16",
         "note_num": "16"}
    ]
    fs = ig.check_boundaries(inp)
    assert len(fs) == 1 and fs[0].blocking


def test_boundary_check_is_quiet_when_the_readings_agree():
    assert ig.check_boundaries(_clean()) == []


def test_render_match_fires_when_a_cell_drifted_with_no_edit_recorded():
    inp = _clean()
    inp.cells[0].current_sha256 = "different"
    fs = ig.check_render_matches_selection(inp)
    assert len(fs) == 1 and fs[0].blocking


def test_a_recorded_hand_edit_is_a_warning_not_a_failure():
    """Free-form human editing stays allowed (Key Decision). It is marked
    diverged, not treated as a defect."""
    inp = _clean()
    inp.cells[0].current_sha256 = "different"
    inp.cells[0].content_origin = "human_modified"
    fs = ig.check_render_matches_selection(inp)
    assert len(fs) == 1
    assert fs[0].severity == ig.WARNING and not fs[0].blocking


def test_render_match_is_quiet_when_the_cell_matches():
    assert ig.check_render_matches_selection(_clean()) == []


def test_character_cap_fires_on_an_oversized_note():
    inp = _clean()
    inp.cells[0].rendered_chars = 41_000
    fs = ig.check_character_cap(inp)
    assert len(fs) == 1 and fs[0].blocking
    assert "authoring path" in fs[0].message


def test_character_cap_is_quiet_under_the_limit():
    assert ig.check_character_cap(_clean()) == []


def test_duplicate_use_of_one_block_fires_without_an_approval():
    """Reads the PLACEMENT ledger. The old version read cell records whose
    block lists came from `notes_block_usages`, which is UNIQUE per block —
    so one block in two cells was unrepresentable and this check could never
    fire in production. Its fixture hand-built a shape the real builder
    cannot produce."""
    inp = _clean()
    inp.placements = {"b1": [("Notes", 10), ("Policies", 4)],
                      "b2": [("Notes", 10)]}
    inp.live_cells = frozenset({("Notes", 10), ("Policies", 4)})
    fs = ig.check_approved_duplicates(inp)
    assert len(fs) == 1 and fs[0].block_ids == ["b1"]


def test_an_approved_duplicate_is_accepted():
    inp = _clean()
    inp.placements = {"b1": [("Notes", 10), ("Policies", 4)],
                      "b2": [("Notes", 10)]}
    inp.live_cells = frozenset({("Notes", 10), ("Policies", 4)})
    inp.approved_duplicate_block_ids = frozenset({"b1"})
    assert ig.check_approved_duplicates(inp) == []


def test_a_duplicate_pointing_at_a_deleted_cell_is_not_a_duplicate():
    """One live placement and one stale one is a placement problem, not a
    duplication — and reporting it as duplication would send the operator to
    the wrong question."""
    inp = _clean()
    inp.placements = {"b1": [("Notes", 10), ("Policies", 4)],
                      "b2": [("Notes", 10)]}
    inp.live_cells = frozenset({("Notes", 10)})
    assert ig.check_approved_duplicates(inp) == []


def test_absent_scout_data_warns_rather_than_reporting_agreement():
    inp = _clean()
    inp.scout_available = False
    fs = ig.check_scout_agreement(inp)
    assert len(fs) == 1 and fs[0].severity == ig.WARNING


def test_scout_agreement_is_quiet_when_the_comparison_ran():
    assert ig.check_scout_agreement(_clean()) == []


# --------------------------------------------------------------------------
# the registry itself
# --------------------------------------------------------------------------

# Every check in the registry, listed here on purpose. Adding a check without
# adding its failing and passing fixtures fails this test rather than shipping
# unverified — the whole point of a registry is that the SET is reviewable.
EXPECTED_CHECKS = {
    "check_page_receipts",
    "check_block_ownership",
    "check_dispositions",
    "check_prose_note_coverage",
    "check_table_groups",
    "check_note_continuity",
    "check_boundaries",
    "check_render_matches_selection",
    "check_character_cap",
    "check_approved_duplicates",
    "check_scout_agreement",
}


def test_the_registry_matches_the_checks_this_file_covers():
    assert {c.__name__ for c in ig.CHECKS} == EXPECTED_CHECKS


def test_no_check_fires_on_a_clean_run():
    inp = _clean()
    for check in ig.CHECKS:
        assert check(inp) == [], f"{check.__name__} fires on a clean run"


def test_requires_review_is_true_only_for_blocking_findings():
    warn_only = ig.IntegrityResult(
        findings=[ig.Finding("x", ig.WARNING, "just so you know")]
    )
    assert warn_only.requires_review is False
    blocking = ig.IntegrityResult(
        findings=[ig.Finding("x", ig.UNRESOLVED, "unaccounted")]
    )
    assert blocking.requires_review is True


# --------------------------------------------------------------------------
# Step 7.2 — the targeted retry
# --------------------------------------------------------------------------

def test_the_retry_list_names_only_what_a_retry_could_fix():
    """Asking an agent to redo a note because the page count is short burns a
    turn on something it cannot change."""
    result = ig.IntegrityResult(findings=[
        ig.Finding("disposition", ig.UNRESOLVED, "", ["b2"]),
        ig.Finding("note_coverage", ig.UNRESOLVED, "", ["b3"]),
        ig.Finding("page_receipts", ig.UNRESOLVED, "", ["b9"]),
        ig.Finding("render_match", ig.WARNING, "", ["b8"]),
    ])
    assert ig.missing_block_ids(result) == ["b2", "b3"]


def test_the_retry_list_is_empty_for_a_clean_result():
    assert ig.missing_block_ids(ig.run_checks(_clean())) == []


def test_the_retry_list_does_not_repeat_a_block():
    result = ig.IntegrityResult(findings=[
        ig.Finding("disposition", ig.UNRESOLVED, "", ["b2"]),
        ig.Finding("note_coverage", ig.UNRESOLVED, "", ["b2", "b4"]),
    ])
    assert ig.missing_block_ids(result) == ["b2", "b4"]


# --------------------------------------------------------------------------
# placement — the check peer review made necessary (2026-08-01)
# --------------------------------------------------------------------------

def test_a_block_relinked_out_of_its_cell_stops_counting_as_included():
    """The reproduction: relink a cell from b1+b2 to b1. b2's disposition row
    still says `included` at that cell. Before the placement ledger the run
    verified clean."""
    inp = _clean()
    inp.placements = {"b1": [("Notes", 10)]}      # b2 dropped
    fs = ig.check_dispositions(inp)
    assert [f.check for f in fs] == ["placement"]
    assert fs[0].block_ids == ["b2"] and fs[0].blocking


def test_a_block_placed_in_a_deleted_cell_stops_counting_as_included():
    """The other reproduction: the sheet was clobbered, so the cells are gone
    while every disposition still says included."""
    inp = _clean()
    inp.live_cells = frozenset()
    fs = ig.check_dispositions(inp)
    assert {f.check for f in fs} == {"placement"}
    assert {b for f in fs for b in f.block_ids} == {"b1", "b2"}


def test_a_routed_block_with_no_destination_does_not_resolve_itself():
    inp = _clean()
    inp.usages = {
        "b1": _usage(Disposition.INCLUDED),
        "b2": {"disposition": Disposition.ROUTED.value, "reason_code": None,
               "sheet": None, "row": None},
    }
    fs = ig.check_dispositions(inp)
    assert [f.check for f in fs] == ["placement"]
    assert "no destination" in fs[0].message


def test_a_routed_block_with_a_destination_is_accepted():
    inp = _clean()
    inp.usages = {
        "b1": _usage(Disposition.INCLUDED),
        "b2": {"disposition": Disposition.ROUTED.value, "reason_code": None,
               "sheet": "Policies", "row": 4},
    }
    assert ig.check_dispositions(inp) == []


def test_structured_consumed_also_needs_a_destination():
    inp = _clean()
    inp.usages = {
        "b1": _usage(Disposition.INCLUDED),
        "b2": {"disposition": Disposition.STRUCTURED_CONSUMED.value,
               "reason_code": None, "sheet": None, "row": None},
    }
    assert ig.check_dispositions(inp)


def test_note_coverage_counts_an_unplaced_block_as_missing():
    inp = _clean()
    inp.placements = {"b1": [("Notes", 10)]}
    fs = ig.check_prose_note_coverage(inp)
    assert len(fs) == 1 and fs[0].block_ids == ["b2"]


def test_a_placement_finding_is_repairable_by_a_retry():
    result = ig.IntegrityResult(findings=[
        ig.Finding("placement", ig.UNRESOLVED, "", ["b2"]),
    ])
    assert ig.missing_block_ids(result) == ["b2"]
