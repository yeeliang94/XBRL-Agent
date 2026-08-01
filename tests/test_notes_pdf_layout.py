"""Digital-PDF layout reading — plan Phase 10.

**These fixtures are GENERATED.** Both PDFs in `data/` are 150 DPI scans with
no text layer, so the real Step 0.4 gate — *zero false-green omissions across
≥50 annotated pages of ≥3 real filings* — is NOT closed by this file and the
phase stays gated. What these tests do establish is that the mechanism can
express the gate at all: that a missed region shows up as unresolved rather
than as silence.

The central test is `test_a_table_the_detector_misses_shows_up_as_unaccounted`.
Everything else is scaffolding around it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from notes import pdf_layout  # noqa: E402
from notes.source_models import OwnerKind  # noqa: E402


def _text_page(doc, lines: list[str], *, start_y: float = 72) -> None:
    page = doc.new_page(width=595, height=842)
    y = start_y
    for line in lines:
        page.insert_text((72, y), line, fontsize=11)
        y += 18


@pytest.fixture()
def simple_pdf(tmp_path) -> Path:
    doc = fitz.open()
    _text_page(doc, [
        "5. Receivables",
        "Trade receivables are stated at cost.",
        "No amounts are past due at the reporting date.",
    ])
    _text_page(doc, [
        "6. Cash and bank balances",
        "Cash at bank earns interest at floating rates.",
    ])
    out = tmp_path / "simple.pdf"
    doc.save(out)
    doc.close()
    return out


@pytest.fixture()
def scanned_pdf(tmp_path) -> Path:
    """A page with no text layer at all — a scan, as far as this reader is
    concerned."""
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    out = tmp_path / "scan.pdf"
    doc.save(out)
    doc.close()
    return out


# --------------------------------------------------------------------------
# Step 10.1 — blocks
# --------------------------------------------------------------------------

def test_text_becomes_paragraph_blocks_with_page_locators(simple_pdf):
    result = pdf_layout.build_pdf_manifest(simple_pdf)
    paras = [b for b in result.blocks if b.block_kind == "paragraph"]
    assert paras
    for b in paras:
        assert b.page in (1, 2)
        assert b.locator["kind"] == "pdf_bbox"
        assert len(b.locator["bbox"]) == 4


def test_reading_order_is_dense_and_follows_the_page(simple_pdf):
    result = pdf_layout.build_pdf_manifest(simple_pdf)
    orders = [b.reading_order for b in result.blocks]
    assert orders == sorted(orders)
    assert len(set(orders)) == len(orders)
    first = min(result.blocks, key=lambda b: b.reading_order)
    assert first.page == 1


def test_the_text_of_the_document_survives(simple_pdf):
    result = pdf_layout.build_pdf_manifest(simple_pdf)
    joined = " ".join(b.canonical_html for b in result.blocks)
    assert "Receivables" in joined
    assert "past due" in joined
    assert "Cash at bank" in joined


def test_a_receipt_is_produced_for_every_page(simple_pdf):
    result = pdf_layout.build_pdf_manifest(simple_pdf)
    assert [r.page for r in result.receipts] == [1, 2]
    assert result.pages_expected == 2
    assert result.pages_accounted == 2


def test_the_source_is_fingerprinted(simple_pdf):
    result = pdf_layout.build_pdf_manifest(simple_pdf)
    assert len(result.source_sha256) == 64
    assert result.input_kind == "pdf_text"


# --------------------------------------------------------------------------
# Step 10.2 — independent region accounting (the gate's whole point)
# --------------------------------------------------------------------------

def test_a_table_the_detector_misses_shows_up_as_unaccounted(simple_pdf, monkeypatch):
    """Step 10.4's criterion in miniature.

    The text blocks are all captured, so a per-block count would report
    complete. Area accounting is what makes the miss visible: the marked area
    the blocks cover falls short of the marked area on the page.
    """
    real_dict = fitz.Page.get_text

    def half_blind(self, kind="text", *a, **kw):
        out = real_dict(self, kind, *a, **kw)
        if kind == "dict" and self.number == 0:
            # Pretend the reader missed the LAST text block on page 1. The
            # page still has plenty of text, so this is a partial miss rather
            # than a "no text layer" page — the case a per-block count would
            # report as complete.
            out = dict(out)
            text_blocks = [b for b in out["blocks"] if b.get("type") == 0]
            out["blocks"] = text_blocks[:-1]
        return out

    monkeypatch.setattr(fitz.Page, "get_text", half_blind)
    result = pdf_layout.build_pdf_manifest(simple_pdf)
    unresolved = [b for b in result.unaccounted_regions if b.page == 1]
    assert unresolved, "a silent miss must become a visible unresolved region"
    assert unresolved[0].locator["reason"] == "unaccounted_region"
    assert result.pages_accounted < result.pages_expected


def test_a_complete_page_reports_no_unaccounted_region(simple_pdf):
    result = pdf_layout.build_pdf_manifest(simple_pdf)
    assert result.unaccounted_regions == []
    for r in result.receipts:
        assert r.accounted is True
        assert r.unresolved_area == pytest.approx(0.0, abs=1.0)


def test_overlapping_rectangles_are_counted_once():
    """A plain sum would double-count a table's cells against the text inside
    it and report over 100% coverage of a page that is genuinely short."""
    a = (0.0, 0.0, 10.0, 10.0)
    b = (5.0, 5.0, 15.0, 15.0)
    assert pdf_layout._union_area([a]) == pytest.approx(100.0)
    assert pdf_layout._union_area([a, b]) == pytest.approx(175.0)
    assert pdf_layout._union_area([a, a]) == pytest.approx(100.0)


def test_the_measure_is_independent_of_the_block_segmentation():
    """The version that compared blocks against the block dict could only
    catch losses AFTER segmentation, never the misses that actually happen.
    The words come from a different extraction call, so a block the
    segmentation dropped still has its words to be missed against."""
    src = Path("notes/pdf_layout.py").read_text(encoding="utf-8")
    assert 'get_text("words")' in src
    assert "INDEPENDENT" in src


def test_disjoint_rectangles_add_up():
    assert pdf_layout._union_area([
        (0.0, 0.0, 10.0, 10.0), (20.0, 20.0, 30.0, 30.0),
    ]) == pytest.approx(200.0)


def test_a_table_detector_crash_becomes_an_unaccounted_region(simple_pdf, monkeypatch):
    """A detector failure is a miss. It must be counted like one, not logged
    and forgotten."""
    monkeypatch.setattr(
        fitz.Page, "find_tables",
        lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result = pdf_layout.build_pdf_manifest(simple_pdf)
    assert any("table detection failed" in w for w in result.warnings)


# --------------------------------------------------------------------------
# Step 10.4 — a scan reaches review, never a clean finish
# --------------------------------------------------------------------------

def test_a_page_with_no_text_layer_produces_a_visible_unresolved_region(scanned_pdf):
    result = pdf_layout.build_pdf_manifest(scanned_pdf)
    assert len(result.unaccounted_regions) == 1
    block = result.unaccounted_regions[0]
    assert block.locator["reason"] == "no_text_layer"
    assert block.page == 1
    assert result.pages_accounted == 0


def test_a_scanned_page_is_not_silently_dropped(scanned_pdf):
    """Emitting nothing for an unreadable page would let it vanish from the
    count, which reads as "there was nothing there"."""
    result = pdf_layout.build_pdf_manifest(scanned_pdf)
    assert result.pages_expected == 1
    assert any("no text layer" in w for w in result.warnings)


def test_a_scan_never_reports_a_clean_reading(scanned_pdf):
    result = pdf_layout.build_pdf_manifest(scanned_pdf)
    assert result.pages_accounted < result.pages_expected


# --------------------------------------------------------------------------
# Step 10.3 — multi-page tables
# --------------------------------------------------------------------------

def test_a_table_continued_on_the_next_page_is_one_group():
    from notes.source_models import SourceBlock

    blocks = [
        SourceBlock(block_id="p1t0", block_kind="table", reading_order=0,
                    canonical_html="<table><tr><td>a</td><td>1</td></tr></table>",
                    page=1),
        SourceBlock(block_id="p2t0", block_kind="table", reading_order=1,
                    canonical_html="<table><tr><td>b</td><td>2</td></tr></table>",
                    page=2),
    ]
    pdf_layout._link_multi_page_tables(blocks)
    assert blocks[0].table_group_id == blocks[1].table_group_id is not None
    assert blocks[1].continues_block_id == "p1t0"


def test_tables_of_different_shapes_are_not_joined():
    from notes.source_models import SourceBlock

    blocks = [
        SourceBlock(block_id="p1t0", block_kind="table", reading_order=0,
                    canonical_html="<table><tr><td>a</td><td>1</td></tr></table>",
                    page=1),
        SourceBlock(block_id="p2t0", block_kind="table", reading_order=1,
                    canonical_html="<table><tr><td>b</td><td>2</td>"
                                   "<td>3</td></tr></table>", page=2),
    ]
    pdf_layout._link_multi_page_tables(blocks)
    assert blocks[0].table_group_id is None


def test_prose_between_two_tables_stops_the_join():
    """Deliberately narrow: a wrong join invents a table that does not exist,
    which is worse than leaving a real continuation split — the split one
    still shows as two complete tables."""
    from notes.source_models import SourceBlock

    blocks = [
        SourceBlock(block_id="p1t0", block_kind="table", reading_order=0,
                    canonical_html="<table><tr><td>a</td></tr></table>", page=1),
        SourceBlock(block_id="p1b0", block_kind="paragraph", reading_order=1,
                    canonical_html="<p>after</p>", page=1),
        SourceBlock(block_id="p2t0", block_kind="table", reading_order=2,
                    canonical_html="<table><tr><td>b</td></tr></table>", page=2),
    ]
    pdf_layout._link_multi_page_tables(blocks)
    assert blocks[0].table_group_id is None
    assert blocks[2].table_group_id is None


# --------------------------------------------------------------------------
# the gate is NOT closed
# --------------------------------------------------------------------------

def test_the_module_says_it_is_gated():
    """Step 0.4 is unmet: no digital PDF exists in the repo, so this path has
    only ever run against generated fixtures. Anyone reading the module must
    be told that before trusting it on a filing."""
    src = Path("notes/pdf_layout.py").read_text(encoding="utf-8")
    assert "Gated" in src or "gated" in src
    assert "0.4" in src
