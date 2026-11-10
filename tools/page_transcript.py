"""Page transcripts from document preparation, for agents on scanned PDFs.

Preparation already transcribes every page (``preparation.json``), but scanned
PDFs carry no text layer, so text search found nothing and agents read every
figure from page images. This module exposes that transcript:

* :func:`transcript_page_texts` — plain text per page, used by
  ``tools.pdf_search`` when the PDF itself has no text layer.
* :func:`read_page_text` — the agent tool body: the transcribed HTML of the
  requested pages, each labelled with its capture status.
* :func:`add_table_figures_json` — the agent tool body: exact column sums read
  straight from a transcribed table, so the agent does not retype figures.

The transcript is model-written and never certified exact (gotcha #31). Pages
the preparation step could only capture on a best-effort basis are labelled
with the recorded reasons so the agent confirms them against the page image.
"""
from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from tools.calculator import _format_decimal

_ORIGINAL_NAME = "uploaded.pdf"
_MAX_PAGES_PER_CALL = 10
_CACHE_MAX = 8
_cache: "OrderedDict[tuple, Optional[dict[int, dict]]]" = OrderedDict()
_cache_lock = threading.Lock()


def _load(pdf_path: str) -> Optional[dict[int, dict]]:
    """``{page: {html, text, verified, reasons}}`` or None when unavailable.

    Uses the sanctioned ``read_prepared_document`` reader against the original
    upload, so only a succeeded preparation whose digests match is exposed.
    Memoised on the file signature (inode, size, mtime, ctime) of every input
    that reader checks — the metadata, the upload and the prepared outputs — so
    replacing any of them forces revalidation instead of a stale cache hit.
    """
    from ingest.document_preparation import (
        PREPARATION_NAME,
        _file_signature,
        read_prepared_document,
    )

    if not pdf_path:
        return None
    folder = Path(pdf_path).parent
    metadata = folder / PREPARATION_NAME
    original = folder / _ORIGINAL_NAME
    inputs = [metadata, *sorted(folder.glob("uploaded.*")), *sorted(folder.glob("prepared-*"))]
    try:
        key = (str(folder), tuple((path.name, _file_signature(path)) for path in inputs))
    except OSError:
        return None
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    prepared = read_prepared_document(original) if original.exists() else None
    pages: Optional[dict[int, dict]] = None
    if prepared is not None:
        pages = {}
        for page in prepared.pages:
            html = page.get("html") or ""
            reasons = [
                str(u.get("reason", "")).strip()
                for u in page.get("uncertainties") or []
                if isinstance(u, dict) and u.get("reason")
            ]
            pages[int(page["page"])] = {
                "html": html,
                "text": BeautifulSoup(html, "html.parser").get_text(" ", strip=True),
                "verified": page.get("verified") is True,
                "reasons": reasons,
            }
    with _cache_lock:
        _cache[key] = pages
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return pages


def transcript_available(pdf_path: Optional[str]) -> bool:
    return bool(pdf_path) and bool(_load(pdf_path))


def transcript_page_texts(pdf_path: str, page_count: int) -> Optional[list[str]]:
    """Plain transcript text for pages 1..page_count, or None when unavailable."""
    pages = _load(pdf_path)
    if not pages:
        return None
    return [pages.get(n, {}).get("text", "") for n in range(1, page_count + 1)]


def read_page_text(pdf_path: str, pages: list[int]) -> str:
    """Agent tool body: transcribed HTML for up to ten pages."""
    transcript = _load(pdf_path)
    if not transcript:
        return (
            "No page transcript is available for this document. Read the pages "
            "with view_pdf_pages instead."
        )
    requested = sorted({p for p in pages if isinstance(p, int)})
    parts: list[str] = []
    if len(requested) > _MAX_PAGES_PER_CALL:
        parts.append(
            f"Returned the first {_MAX_PAGES_PER_CALL} of {len(requested)} pages; "
            "request the rest in another call."
        )
        requested = requested[:_MAX_PAGES_PER_CALL]
    invalid = [p for p in requested if p not in transcript]
    if invalid:
        parts.append(
            f"Skipped page(s) {invalid}: valid pages are 1-{max(transcript)}."
        )
    for number in requested:
        page = transcript.get(number)
        if page is None:
            continue
        if page["verified"]:
            status = "transcript checked during preparation"
        else:
            reasons = "; ".join(page["reasons"]) or "capture uncertain"
            status = (
                "BEST-EFFORT transcript — confirm figures with view_pdf_pages "
                f"before writing. Reason: {reasons}"
            )
        parts.append(f"=== Page {number} ({status}) ===\n{page['html']}")
    return "\n\n".join(parts) if parts else "No pages were requested."


class TableSum(BaseModel):
    """One column range to add up in a transcribed table."""

    page: int = Field(description="PDF page number holding the table.")
    table: int = Field(default=1, ge=1, description="Which table on the page, counting from 1.")
    column: int = Field(ge=1, description="Column to add, counting from 1 at the left.")
    from_row: str = Field(description='First row: its label as printed, or "#N" for row N.')
    to_row: str = Field(description='Last row (inclusive): its label, or "#N" for row N.')


_BLANK_CELLS = frozenset({"", "-", "–", "—", "nil"})
_AMOUNT_RE = re.compile(r"^-?\d+(\.\d+)?$")
_SPACED_THOUSANDS_RE = re.compile(r"^-?\d{1,3}( \d{3})+(\.\d+)?$")
# Same ceiling document preparation applies to a transcribed table's spans.
_MAX_TABLE_SPAN = 1000


def _cell_amount(text: str) -> Optional[Decimal]:
    """A printed amount as a number; blank and dash cells count as zero.

    None for anything else, including a cell that holds two figures.
    """
    cleaned = text.strip()
    if cleaned.casefold() in _BLANK_CELLS:
        return Decimal(0)
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1].strip()
    cleaned = cleaned.replace(",", "")
    if _SPACED_THOUSANDS_RE.match(cleaned):
        cleaned = cleaned.replace(" ", "")
    if not _AMOUNT_RE.match(cleaned):
        return None
    value = Decimal(cleaned)
    return -value if negative else value


def _table_grid(table) -> tuple[list[list[str]], int]:
    """``(rows of cell text, number of header rows)`` for one table.

    Merged cells repeat their text across every position they span. Header
    rows are the leading rows made only of ``th`` cells. Raises ValueError for
    a span too large to be a real table.
    """
    cells: dict[tuple[int, int], str] = {}
    header_rows = 0
    trs = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    for r, tr in enumerate(trs):
        col = 0
        tags = tr.find_all(["td", "th"], recursive=False)
        if header_rows == r and tags and all(tag.name == "th" for tag in tags):
            header_rows = r + 1
        for cell in tags:
            while (r, col) in cells:  # position covered by a rowspan above
                col += 1
            colspan, rowspan = _span(cell, "colspan"), _span(cell, "rowspan")
            if max(colspan, rowspan, col + colspan) > _MAX_TABLE_SPAN:
                raise ValueError("This table's merged cells are not readable in the "
                                 "transcript. Read the page with view_pdf_pages.")
            text = " ".join(cell.get_text(" ", strip=True).split())
            for dr in range(rowspan):
                for dc in range(colspan):
                    cells[(r + dr, col + dc)] = text
            col += colspan
    width = max((c for _, c in cells), default=-1) + 1
    return [[cells.get((r, c), "") for c in range(width)] for r in range(len(trs))], header_rows


def _span(cell, name: str) -> int:
    try:
        return max(1, int(cell.get(name) or 1))
    except ValueError:
        return 1


def _row_label(row: list[str]) -> str:
    return next((text for text in row if text), "")


def _find_row(grid: list[list[str]], wanted: str, start: Optional[int] = None) -> int:
    """Index of the row named by ``wanted``; raises ValueError.

    With ``start`` (the range's first row) the first match at or after it is
    taken; without it the label must name exactly one row.
    """
    wanted = wanted.strip()
    if wanted.startswith("#"):
        try:
            index = int(wanted[1:]) - 1
        except ValueError:
            raise ValueError(f'"{wanted}" is not a row number; write it as "#5".') from None
        if not 0 <= index < len(grid):
            raise ValueError(f"Row {wanted} is outside this table's rows 1-{len(grid)}.")
        return index
    key = " ".join(wanted.casefold().split())
    labels = [(i, _row_label(row).casefold()) for i, row in enumerate(grid) if i >= (start or 0)]
    matches = ([i for i, label in labels if label == key]
               or [i for i, label in labels if key and key in label])
    if not matches:
        raise ValueError(f'No row is labelled "{wanted}". Rows: ' + _row_list(grid))
    if len(matches) > 1 and start is None:
        raise ValueError(f'"{wanted}" names more than one row; use "#N". Rows: '
                         + _row_list(grid, matches))
    return matches[0]


def _row_list(grid: list[list[str]], only: Optional[list[int]] = None) -> str:
    rows = only if only is not None else range(len(grid))
    return "; ".join(f"#{i + 1} {_row_label(grid[i])!r}" for i in rows)


def add_table_figures_json(pdf_path: str, sums: list[TableSum]) -> str:
    """Agent tool body: add up column ranges of transcribed tables exactly.

    Reads the figures from the transcript itself, so no figure is retyped.
    Every figure read is echoed back with its row label so the caller can see
    that the right rows were added.
    """
    transcript = _load(pdf_path)
    if not transcript:
        return json.dumps({"error": "No page transcript is available for this document. "
                                    "Read the pages with view_pdf_pages and use calculator."})
    if not sums:
        return json.dumps({"error": "Pass at least one column range to add."})
    return json.dumps([_add_one(transcript, item) for item in sums])


def _add_one(transcript: dict[int, dict], item: TableSum) -> dict:
    out: dict = {"page": item.page, "table": item.table, "column": item.column}
    page = transcript.get(item.page)
    if page is None:
        return {**out, "error": f"Page {item.page} is outside pages 1-{max(transcript)}."}
    soup = BeautifulSoup(page["html"], "html.parser")
    tables = [t for t in soup.find_all("table") if t.find_parent("table") is None]
    if item.table > len(tables):
        return {**out, "error": f"Page {item.page} has {len(tables)} table(s) in its transcript."}
    try:
        grid, header_rows = _table_grid(tables[item.table - 1])
        first = _find_row(grid, item.from_row)
        last = _find_row(grid, item.to_row, start=first)
    except ValueError as exc:
        return {**out, "error": str(exc)}
    if last < first:
        return {**out, "error": f"to_row (#{last + 1}) comes before from_row (#{first + 1})."}
    column = item.column - 1
    if column >= len(grid[0]):
        return {**out, "error": f"This table has {len(grid[0])} column(s); "
                                f"column {item.column} does not exist."}
    total = Decimal(0)
    rows = []
    for index in range(first, last + 1):
        text = grid[index][column]
        value = _cell_amount(text)
        if value is None:
            return {**out, "error": f"Row #{index + 1} {_row_label(grid[index])!r} holds {text!r} "
                                    f"in column {item.column}, which is not a figure. Choose "
                                    "another column or row range."}
        total += value
        rows.append({"row": index + 1, "label": _row_label(grid[index]), "cell": text,
                     "value": _format_decimal(value)})
    # Every header row, so stacked headings name the entity and the year.
    headings = dict.fromkeys(row[column] for row in grid[:header_rows or 1] if row[column])
    out.update(column_heading=" / ".join(headings), rows=rows, sum=_format_decimal(total))
    if last + 1 < len(grid):
        after = grid[last + 1]
        out["next_row"] = {"row": last + 2, "label": _row_label(after), "cell": after[column]}
    if not page["verified"]:
        out["warning"] = ("BEST-EFFORT transcript — confirm these figures with view_pdf_pages "
                          "before writing.")
    return out


def register_add_table_figures(agent) -> None:
    """Give an agent whose deps carry ``pdf_path`` the table-adding tool."""

    @agent.tool
    def add_table_figures(ctx: RunContext[Any], sums: list[TableSum]) -> str:
        """Add up figures in a table on a transcribed page, exactly. Batch ALL sums in ONE call.

        Use this instead of calculator whenever the figures sit in one column
        of a table returned by read_page_text: name the page, the table, the
        column and the first and last row, and the figures are read from the
        transcript — you do not retype them. Bracketed amounts are negative;
        blank and dash cells are zero. Returns the column's headings, every
        figure it read with its row label, their sum, and the row that follows
        (often the printed total) so you can compare. Check the headings name
        the year and entity you meant and the labels are the rows you meant.
        The transcript is not certified exact: if a sum does not agree with
        the printed total, confirm the figures with view_pdf_pages.
        """
        return add_table_figures_json(ctx.deps.pdf_path, sums)
