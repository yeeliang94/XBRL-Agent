"""Read a digital PDF into source blocks — plan Phase 10.

**Gated.** This path does not run unless Step 0.4's criterion is met on real
filings: *zero false-green omissions*. It is built here so the criterion can be
tested at all, but the gate is not closed — both PDFs in `data/` are 150 DPI
scans with no text layer, so it has been exercised only against generated
fixtures. The `off` default keeps it out of every real run until that changes.

The design difference from the Word path is the whole point of Step 10.2:
Word gives you the document's own structure, so "did we get everything?" is
answered by reading it. A PDF gives you marks on a page, so the same question
has to be answered by AREA — every region of every page is either attributed
to a block or visibly unresolved. A detector that silently misses a table
would otherwise report complete coverage of a document it only partly read,
which is the exact false-green Step 0.4 refuses to accept.

So:

* blocks come from `page.get_text("dict")` and `page.find_tables()`;
* every page's covered area is measured against its text area, and the
  shortfall becomes an UNRESOLVED block rather than nothing;
* a page with no text layer produces one unresolved region covering the page —
  a scan cannot be read this way, and saying so is the only honest output.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from notes.source_models import OwnerKind, SourceBlock

logger = logging.getLogger("server")

EXTRACTOR_VERSION = "pdf-layout-1"
INPUT_KIND_PDF = "pdf_text"

# A page whose blocks cover less than this share of its text area has a region
# nobody accounted for. Deliberately strict: the cost of a false "unresolved"
# is one review item, the cost of a false "complete" is a missing disclosure.
_MIN_AREA_COVERAGE = 0.98

# Below this many characters a page is treated as having no usable text layer.
# A scanned page still yields a few stray characters from stamps and OCR
# artifacts; calling that a text layer would produce a manifest of noise.
_MIN_PAGE_CHARS = 20


@dataclass
class PageReceipt:
    page: int
    has_text_layer: bool
    chars: int
    blocks: int
    tables: int
    area_covered: float
    unresolved_area: float

    @property
    def accounted(self) -> bool:
        return self.has_text_layer and self.area_covered >= _MIN_AREA_COVERAGE


@dataclass
class PdfLayoutResult:
    blocks: list[SourceBlock]
    receipts: list[PageReceipt]
    source_sha256: str
    extractor_version: str = EXTRACTOR_VERSION
    input_kind: str = INPUT_KIND_PDF
    warnings: list[str] = field(default_factory=list)

    @property
    def pages_expected(self) -> int:
        return len(self.receipts)

    @property
    def pages_accounted(self) -> int:
        return sum(1 for r in self.receipts if r.accounted)

    @property
    def unaccounted_regions(self) -> list[SourceBlock]:
        """Areas of a page attributed to nothing.

        Selected by BLOCK KIND, not by `owner_kind`: at this stage no block has
        been assigned to a note yet, so every captured block is legitimately
        owner-unresolved. Conflating "not yet assigned to a note" with "we
        could not read this" would make the number meaningless in the
        direction that matters.
        """
        return [b for b in self.blocks if b.block_kind == "unresolved_region"]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rect_area(rect) -> float:
    return max(0.0, float(rect[2] - rect[0])) * max(0.0, float(rect[3] - rect[1]))


def _union_area(rects: list[tuple]) -> float:
    """Area of a set of rectangles, counting overlaps once.

    A plain sum would double-count a table's cells against the text blocks
    inside it and report >100% coverage on a page that is genuinely short.
    Sweep by x-boundaries; the page has tens of rectangles, so this is cheap.
    """
    if not rects:
        return 0.0
    xs = sorted({r[0] for r in rects} | {r[2] for r in rects})
    total = 0.0
    for x0, x1 in zip(xs, xs[1:]):
        width = x1 - x0
        if width <= 0:
            continue
        spans = sorted(
            (r[1], r[3]) for r in rects if r[0] <= x0 and r[2] >= x1
        )
        covered, cur_lo, cur_hi = 0.0, None, None
        for lo, hi in spans:
            if cur_hi is None or lo > cur_hi:
                if cur_hi is not None:
                    covered += cur_hi - cur_lo
                cur_lo, cur_hi = lo, hi
            else:
                cur_hi = max(cur_hi, hi)
        if cur_hi is not None:
            covered += cur_hi - cur_lo
        total += width * covered
    return total


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _table_html(rows: list[list[Optional[str]]]) -> str:
    body = "".join(
        "<tr>" + "".join(f"<td>{_escape(c or '')}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return f"<table>{body}</table>"


def build_pdf_manifest(pdf_path: str | Path) -> PdfLayoutResult:
    """Read a digital PDF into blocks, with a receipt per page."""
    import fitz

    path = Path(pdf_path)
    blocks: list[SourceBlock] = []
    receipts: list[PageReceipt] = []
    warnings: list[str] = []
    order = 0

    with fitz.open(path) as doc:
        for page_index, page in enumerate(doc, start=1):
            page_rect = page.rect
            text_dict = page.get_text("dict")
            page_chars = sum(
                len(span.get("text", ""))
                for blk in text_dict.get("blocks", [])
                for line in blk.get("lines", [])
                for span in line.get("spans", [])
            )

            if page_chars < _MIN_PAGE_CHARS:
                # Step 10.4 — a scanned page produces a VISIBLE unresolved
                # region covering the whole page. Emitting nothing would let
                # the page vanish from the count.
                blocks.append(SourceBlock(
                    block_id=f"p{page_index:04d}-unresolved",
                    block_kind="unresolved_region", reading_order=order,
                    canonical_html="", page=page_index,
                    owner_kind=OwnerKind.UNRESOLVED,
                    locator={
                        "kind": "pdf_bbox", "page": page_index,
                        "bbox": [page_rect.x0, page_rect.y0,
                                 page_rect.x1, page_rect.y1],
                        "reason": "no_text_layer",
                    },
                ))
                order += 1
                receipts.append(PageReceipt(
                    page=page_index, has_text_layer=False, chars=page_chars,
                    blocks=0, tables=0, area_covered=0.0,
                    unresolved_area=_rect_area(tuple(page_rect)),
                ))
                warnings.append(
                    f"page {page_index} has no text layer; it cannot be read "
                    "this way and is left unresolved"
                )
                continue

            table_rects: list[tuple] = []
            page_blocks: list[SourceBlock] = []
            try:
                found = page.find_tables()
                tables = list(getattr(found, "tables", found) or [])
            except Exception:  # noqa: BLE001 — a detector failure is a miss,
                tables = []    # and a miss must show up as unresolved area
                warnings.append(
                    f"table detection failed on page {page_index}; any table "
                    "there will surface as an unaccounted region"
                )
            for t_index, table in enumerate(tables):
                bbox = tuple(table.bbox)
                table_rects.append(bbox)
                try:
                    rows = table.extract()
                except Exception:  # noqa: BLE001
                    rows = []
                page_blocks.append(SourceBlock(
                    block_id=f"p{page_index:04d}t{t_index:02d}",
                    block_kind="table", reading_order=0,
                    canonical_html=_table_html(rows), page=page_index,
                    locator={"kind": "pdf_bbox", "page": page_index,
                             "bbox": list(bbox)},
                ))

            def _inside_a_table(bbox) -> bool:
                return any(
                    bbox[0] >= r[0] - 1 and bbox[1] >= r[1] - 1
                    and bbox[2] <= r[2] + 1 and bbox[3] <= r[3] + 1
                    for r in table_rects
                )

            # The INDEPENDENT measure for Step 10.2. Word boxes come from a
            # different extraction call than the block dict, so a block the
            # segmentation dropped still has its words here. Comparing blocks
            # against the block dict — the obvious version — would only ever
            # catch losses AFTER segmentation, never the misses that actually
            # happen. Not independent of "the page has no text layer at all";
            # that case is handled above, by page character count.
            try:
                word_rects = [
                    (w[0], w[1], w[2], w[3]) for w in page.get_text("words")
                ]
            except Exception:  # noqa: BLE001
                word_rects = []
                warnings.append(
                    f"page {page_index}: could not measure the page "
                    "independently, so its coverage is unverified"
                )

            for blk in text_dict.get("blocks", []):
                if blk.get("type") != 0:
                    continue
                bbox = tuple(blk.get("bbox", (0, 0, 0, 0)))
                text = "".join(
                    span.get("text", "")
                    for line in blk.get("lines", [])
                    for span in line.get("spans", [])
                ).strip()
                if not text:
                    continue
                if _inside_a_table(bbox):
                    continue    # the table block already owns this text
                page_blocks.append(SourceBlock(
                    block_id=f"p{page_index:04d}b{len(page_blocks):03d}",
                    block_kind="paragraph", reading_order=0,
                    canonical_html=f"<p>{_escape(text)}</p>", page=page_index,
                    locator={"kind": "pdf_bbox", "page": page_index,
                             "bbox": list(bbox)},
                ))

            page_blocks.sort(
                key=lambda b: (
                    (b.locator or {}).get("bbox", [0, 0])[1],
                    (b.locator or {}).get("bbox", [0, 0])[0],
                )
            )
            for b in page_blocks:
                b.reading_order = order
                order += 1
            blocks.extend(page_blocks)

            # Step 10.2 — independent region accounting. Measure the words the
            # blocks DO NOT cover, rather than comparing two areas: a block box
            # is padded relative to its words, so an area ratio reads over 100%
            # on a complete page and hides a real shortfall.
            block_boxes = [
                tuple((b.locator or {}).get("bbox", (0, 0, 0, 0)))
                for b in page_blocks
            ]

            def _covered_by_a_block(w) -> bool:
                cx, cy = (w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0
                return any(
                    bb[0] - 1 <= cx <= bb[2] + 1 and bb[1] - 1 <= cy <= bb[3] + 1
                    for bb in block_boxes
                )

            missed = [w for w in word_rects if not _covered_by_a_block(w)]
            word_area = _union_area(word_rects)
            missed_area = _union_area(missed)
            ratio = (
                1.0 if word_area <= 0
                else max(0.0, 1.0 - missed_area / word_area)
            )
            shortfall = missed_area
            if ratio < _MIN_AREA_COVERAGE:
                blocks.append(SourceBlock(
                    block_id=f"p{page_index:04d}-unaccounted",
                    block_kind="unresolved_region", reading_order=order,
                    canonical_html="", page=page_index,
                    owner_kind=OwnerKind.UNRESOLVED,
                    locator={"kind": "pdf_bbox", "page": page_index,
                             "reason": "unaccounted_region",
                             "shortfall_ratio": round(1.0 - ratio, 4)},
                ))
                order += 1
                warnings.append(
                    f"page {page_index}: {(1 - ratio):.0%} of the marked area "
                    "is not attributed to anything"
                )

            receipts.append(PageReceipt(
                page=page_index, has_text_layer=True, chars=page_chars,
                blocks=len([b for b in page_blocks if b.block_kind == "paragraph"]),
                tables=len([b for b in page_blocks if b.block_kind == "table"]),
                area_covered=ratio, unresolved_area=shortfall,
            ))

    _link_multi_page_tables(blocks)
    return PdfLayoutResult(
        blocks=blocks, receipts=receipts,
        source_sha256=_sha256_file(path), warnings=warnings,
    )


def _link_multi_page_tables(blocks: list[SourceBlock]) -> None:
    """Step 10.3 — a table continued on the next page is one table.

    Joined when the next page's FIRST block is a table with the same column
    count and the previous page's LAST block was a table. Deliberately narrow:
    a wrong join merges two unrelated disclosures, which is worse than leaving
    a real continuation split, because the split one still shows up as two
    complete tables while the merge invents a table that does not exist.
    """
    by_page: dict[int, list[SourceBlock]] = {}
    for b in blocks:
        if b.page is not None and b.block_kind != "unresolved_region":
            by_page.setdefault(b.page, []).append(b)
    for page in sorted(by_page):
        nxt = by_page.get(page + 1)
        if not nxt:
            continue
        last = by_page[page][-1]
        first = nxt[0]
        if last.block_kind != "table" or first.block_kind != "table":
            continue
        if _columns(last.canonical_html) != _columns(first.canonical_html):
            continue
        group = last.table_group_id or f"tg-{last.block_id}"
        last.table_group_id = group
        first.table_group_id = group
        first.continues_block_id = last.block_id


def _columns(html: str) -> int:
    import re

    first_row = re.search(r"<tr>(.*?)</tr>", html, re.S)
    return len(re.findall(r"<td", first_row.group(1))) if first_row else 0
