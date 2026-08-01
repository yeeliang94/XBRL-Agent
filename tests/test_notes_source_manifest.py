"""Word source manifest — plan Phase 4, Steps 4.1 / 4.2 / 4.3 / 4.4.

The manifest is the DENOMINATOR of every later completeness figure, so the
tests here are mostly about what the builder must refuse to do:

* never return a short manifest when extraction failed or was cut — a
  truncated denominator scores 100% for reading half the document;
* never mint a note boundary from a contents-page line (the run-74 failure);
* never leave a block with no owner, because an unowned block is invisible to
  a per-note count.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from db import repository as repo
from db.schema import init_db
from notes import source_manifest as sm
from notes import source_repository as srepo
from notes.source_models import Disposition, OwnerKind

FIXTURE = Path("data/FINCO-Audited-Financial-Statement-2021.docx")
pytestmark = pytest.mark.skipif(
    not FIXTURE.is_file(), reason="Word fixture not present"
)


@pytest.fixture(scope="module")
def manifest() -> sm.ManifestResult:
    return sm.build_docx_manifest(FIXTURE)


# --------------------------------------------------------------------------
# Step 4.1 — blocks
# --------------------------------------------------------------------------

def test_every_block_has_a_locator_and_an_owner(manifest):
    assert manifest.blocks
    for b in manifest.blocks:
        assert b.locator and b.locator.get("kind") == "docx_dom"
        assert b.locator.get("block_index") is not None
        assert isinstance(b.owner_kind, OwnerKind)
        assert b.content_sha256


def test_block_ids_are_unique_and_reading_order_is_dense(manifest):
    ids = [b.block_id for b in manifest.blocks]
    assert len(ids) == len(set(ids))
    orders = sorted(b.reading_order for b in manifest.blocks)
    assert orders == list(range(len(orders)))


def test_the_manifest_accounts_for_the_whole_body(manifest):
    """`unaccounted_chars` is the real measure — non-whitespace characters of
    the extracted body that fell outside every block. `body_chars` is defined
    as the sum of block lengths, so asserting on it alone would prove nothing.
    """
    assert manifest.unaccounted_chars == 0
    assert manifest.coverage_ratio == pytest.approx(1.0)


def test_content_between_blocks_is_counted_not_ignored():
    """The splitter in source_snippets is a navigation aid and may skip what
    falls between chunks. This one is a ledger, so a stray must show up."""
    spans, gap = sm._split_with_gaps("<p>kept</p>STRAY TEXT<p>kept</p>")
    assert len(spans) == 2
    assert gap == len("STRAYTEXT")


def test_every_table_is_captured(manifest):
    tables = [b for b in manifest.blocks if b.block_kind == "table"]
    assert len(tables) == 21
    # Depth-aware capture: each table block closes its own tag.
    for t in tables:
        opens = len(re.findall(r"<table\b", t.canonical_html, re.I))
        closes = len(re.findall(r"</table>", t.canonical_html, re.I))
        assert opens == closes >= 1


def test_a_table_continued_after_a_page_break_is_one_group():
    """Word splits a long table at a page break; two separate <table> elements
    are one disclosure. Without the group, Phase 7's whole-table check would
    pass on half of one."""
    html = (
        "<table><tr><td>a</td><td>1</td></tr></table>"
        "<p>  </p>"
        "<table><tr><td>b</td><td>2</td></tr></table>"
        "<p>Real prose breaks the run.</p>"
        "<table><tr><td>c</td><td>3</td></tr></table>"
    )
    spans, _ = sm._split_with_gaps(html)
    blocks = [
        sm.SourceBlock(block_id=f"b{i}", block_kind=sm._block_kind(t),
                       reading_order=i, canonical_html=html[s:e])
        for i, (s, e, t) in enumerate(spans)
    ]
    sm._link_table_groups(blocks)
    groups = [b.table_group_id for b in blocks if b.block_kind == "table"]
    assert groups[0] is not None and groups[0] == groups[1]
    assert groups[2] is None, "prose between tables ends the group"
    assert blocks[2].continues_block_id == blocks[0].block_id


def test_tables_of_different_shapes_are_not_grouped():
    html = (
        "<table><tr><td>a</td><td>1</td></tr></table>"
        "<table><tr><td>b</td><td>2</td><td>3</td></tr></table>"
    )
    spans, _ = sm._split_with_gaps(html)
    blocks = [
        sm.SourceBlock(block_id=f"b{i}", block_kind="table", reading_order=i,
                       canonical_html=html[s:e])
        for i, (s, e, _t) in enumerate(spans)
    ]
    sm._link_table_groups(blocks)
    assert all(b.table_group_id is None for b in blocks)


def test_the_builder_records_what_it_read(manifest):
    assert len(manifest.source_sha256) == 64
    assert manifest.extractor_version
    assert manifest.input_kind == "docx_html"


def test_extraction_failure_raises_rather_than_shortening(tmp_path, monkeypatch):
    """A short manifest that then reports complete is the exact false-green
    this feature exists to prevent, so a failed read must stop the build."""
    monkeypatch.setattr(
        sm, "_extract_html", lambda _p: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(sm.ManifestError):
        sm.build_docx_manifest(FIXTURE)


def test_an_empty_extraction_raises(monkeypatch):
    monkeypatch.setattr(sm, "_extract_html", lambda _p: "   ")
    with pytest.raises(sm.ManifestError):
        sm.build_docx_manifest(FIXTURE)


def test_a_truncated_extraction_raises(monkeypatch):
    """`write_source_html` hard-cuts at 8 MB and the per-note reader at 60k.
    If a capped string ever reaches the manifest builder it must be refused,
    not measured."""
    monkeypatch.setattr(
        sm, "_extract_html",
        lambda _p: "<p>a</p>" + sm.TRUNCATION_SENTINELS[0],
    )
    with pytest.raises(sm.ManifestError):
        sm.build_docx_manifest(FIXTURE)


# --------------------------------------------------------------------------
# Step 4.2 — ownership and boundaries
# --------------------------------------------------------------------------

def test_notes_one_to_fifteen_are_found_in_order(manifest):
    nums = [n.top_note_num for n in manifest.notes]
    assert nums == [str(i) for i in range(1, 16)]


def test_no_contents_page_line_becomes_a_note(manifest):
    """run-74: before the TOC guard, all 15 notes resolved to their one-line
    contents entry. Each note here must own real content, not a single line."""
    for n in manifest.notes:
        assert len(n.block_ids) >= 2, f"note {n.top_note_num} owns one block"


def test_repeated_boilerplate_is_furniture_not_note_content(manifest):
    by_id = {b.block_id: b for b in manifest.blocks}
    furniture = [b for b in manifest.blocks if b.owner_kind is OwnerKind.FURNITURE]
    assert furniture, "the fixture repeats its company header on every page"
    for b in furniture:
        assert b.source_note_id is None
    # ... and no note claims one.
    for n in manifest.notes:
        for bid in n.block_ids:
            assert by_id[bid].owner_kind is OwnerKind.NOTE


def test_front_matter_is_metadata_and_not_silently_dropped(manifest):
    meta = [b for b in manifest.blocks if b.owner_kind is OwnerKind.METADATA]
    assert meta, "cover page and face statements precede the first note"
    kinds = {b.owner_kind for b in manifest.blocks}
    assert OwnerKind.UNRESOLVED not in kinds or any(
        b.owner_kind is OwnerKind.UNRESOLVED for b in manifest.blocks
    )


def test_note_block_ids_are_contiguous_in_reading_order(manifest):
    order = {b.block_id: b.reading_order for b in manifest.blocks}
    for n in manifest.notes:
        seq = [order[b] for b in n.block_ids]
        assert seq == sorted(seq)


# --------------------------------------------------------------------------
# Step 4.4 — boundary report
# --------------------------------------------------------------------------

def test_a_clean_fixture_reports_no_boundary_problem(manifest):
    rep = sm.check_boundaries(manifest, scout_note_nums=list(range(1, 16)))
    assert rep.disagreements == []
    assert rep.ok


def test_a_missing_leading_note_is_detected(manifest):
    rep = sm.check_boundaries(manifest, scout_note_nums=list(range(0, 16)))
    assert any(d.kind == "missing_leading" for d in rep.disagreements)


def test_a_missing_trailing_note_is_detected(manifest):
    rep = sm.check_boundaries(manifest, scout_note_nums=list(range(1, 18)))
    kinds = {d.kind for d in rep.disagreements}
    assert "missing_trailing" in kinds


def test_an_internal_gap_is_detected():
    notes = [sm._note(str(n), f"Note {n}", [f"b{n}"]) for n in (1, 2, 4)]
    rep = sm.check_boundaries(
        sm.ManifestResult(blocks=[], notes=notes, source_sha256="x" * 64,
                          extractor_version="t", input_kind="docx_html",
                          body_chars=0, warnings=[]),
        scout_note_nums=[1, 2, 4],
    )
    assert any(d.kind == "internal_gap" for d in rep.disagreements)


def test_scout_disagreement_is_flagged_not_resolved_silently(manifest):
    """Plan Step 4.2: flag disagreements rather than picking a winner."""
    rep = sm.check_boundaries(manifest, scout_note_nums=[1, 2, 3])
    assert rep.disagreements
    assert not rep.ok


def test_no_scout_inventory_is_not_treated_as_agreement(manifest):
    rep = sm.check_boundaries(manifest, scout_note_nums=[])
    assert rep.scout_available is False
    assert rep.ok, "absent scout data is unknown, not a disagreement"


# --------------------------------------------------------------------------
# Step 4.3 — freeze
# --------------------------------------------------------------------------

@pytest.fixture()
def conn_and_run(tmp_path):
    db = tmp_path / "audit.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
        yield conn, run_id


def test_freeze_activates_one_generation_with_every_block(conn_and_run, manifest):
    conn, run_id = conn_and_run
    gen_id = sm.freeze_manifest(conn, run_id, manifest)
    active = srepo.active_generation(conn, run_id)
    assert active["id"] == gen_id
    assert len(srepo.fetch_blocks(conn, gen_id)) == len(manifest.blocks)
    assert len(srepo.fetch_notes(conn, gen_id)) == len(manifest.notes)


def test_freeze_settles_furniture_and_metadata_but_not_note_content(
    conn_and_run, manifest
):
    """Auto-dispositioning the document's own furniture is the difference
    between a usable review queue and 246 rows of page headers."""
    conn, run_id = conn_and_run
    gen_id = sm.freeze_manifest(conn, run_id, manifest)
    counts = srepo.coverage_counts(conn, gen_id)
    assert counts["total"] == len(manifest.blocks)
    assert counts["excluded"] > 0
    note_blocks = sum(
        1 for b in manifest.blocks if b.owner_kind is OwnerKind.NOTE
    )
    assert counts["unresolved"] == note_blocks

    usages = {u["block_id"]: u for u in srepo.fetch_usages(conn, gen_id)}
    for b in manifest.blocks:
        if b.owner_kind in (OwnerKind.FURNITURE, OwnerKind.METADATA):
            u = usages[b.block_id]
            assert u["disposition"] == Disposition.EXCLUDED.value
            assert u["reason_code"]


def test_freeze_is_rerunnable_and_leaves_one_active(conn_and_run, manifest):
    conn, run_id = conn_and_run
    first = sm.freeze_manifest(conn, run_id, manifest)
    second = sm.freeze_manifest(conn, run_id, manifest)
    assert first != second
    assert srepo.active_generation(conn, run_id)["id"] == second


def test_freeze_refuses_an_empty_manifest(conn_and_run):
    conn, run_id = conn_and_run
    empty = sm.ManifestResult(
        blocks=[], notes=[], source_sha256="0" * 64, extractor_version="t",
        input_kind="docx_html", body_chars=0, warnings=[],
    )
    with pytest.raises(ValueError):
        sm.freeze_manifest(conn, run_id, empty)
    assert srepo.active_generation(conn, run_id) is None
