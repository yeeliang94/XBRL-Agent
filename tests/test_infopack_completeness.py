"""Pinning tests for the infopack completeness probe (Plan 3).

Guards `Infopack.completeness_warnings()` — the pre-fan-out smoke detector that
surfaces a degraded scout pack (unknown unit, empty/gappy inventory, missing
entity/period) as advisory warnings without blocking the run (gotcha #13).
"""

from scout.infopack import Infopack
from scout.notes_discoverer import NoteInventoryEntry


def _note(n: int) -> NoteInventoryEntry:
    return NoteInventoryEntry(
        note_num=n, title=f"Note {n}", page_range=(n, n), subnotes=[]
    )


def _clean_pack(**overrides) -> Infopack:
    """A fully-populated pack that produces zero warnings, plus overrides."""
    kwargs = dict(
        toc_page=2,
        page_offset=0,
        notes_inventory=[_note(1), _note(2), _note(3)],
        scale_unit="thousands",
        inventory_source="text",
        entity_name="Example Bhd",
        reporting_period_cy="2021",
        reporting_period_py="2020",
    )
    kwargs.update(overrides)
    return Infopack(**kwargs)


def test_clean_pack_has_no_warnings():
    assert _clean_pack().completeness_warnings() == []


def test_unknown_scale_warns():
    warns = _clean_pack(scale_unit="unknown").completeness_warnings()
    assert any("scale_unit" in w for w in warns)


def test_empty_inventory_after_pass_warns():
    warns = _clean_pack(
        notes_inventory=[], inventory_source="vision"
    ).completeness_warnings()
    assert any("notes_inventory is empty" in w for w in warns)


def test_empty_inventory_without_pass_is_silent():
    # inventory_source "none"/"unknown" means no pass ran — not a degradation.
    warns = _clean_pack(
        notes_inventory=[], inventory_source="none"
    ).completeness_warnings()
    assert not any("notes_inventory is empty" in w for w in warns)


def test_note_number_gap_warns():
    pack = _clean_pack(
        notes_inventory=[_note(1), _note(2), _note(3), _note(7)]
    )
    warns = pack.completeness_warnings()
    gap_warnings = [w for w in warns if "gaps" in w]
    assert gap_warnings
    # The missing run 4,5,6 should be named.
    assert "4, 5, 6" in gap_warnings[0]


def test_contiguous_inventory_no_gap_warning():
    pack = _clean_pack(notes_inventory=[_note(3), _note(4), _note(5)])
    assert not any("gaps" in w for w in pack.completeness_warnings())


def test_missing_entity_and_period_warn():
    warns = _clean_pack(
        entity_name=None, reporting_period_cy=None
    ).completeness_warnings()
    assert any("entity_name" in w for w in warns)
    assert any("reporting_period_cy" in w for w in warns)


def test_multiple_degradations_accumulate():
    pack = _clean_pack(
        scale_unit="unknown",
        notes_inventory=[],
        inventory_source="text",
        entity_name=None,
    )
    warns = pack.completeness_warnings()
    # unknown scale + empty inventory + missing entity = at least 3 warnings.
    assert len(warns) >= 3
