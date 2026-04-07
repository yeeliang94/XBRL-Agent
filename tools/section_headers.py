"""Discover section-header rows by walking a worksheet's column A.

Why: `fill_workbook` needs to know which rows are section boundaries so it can
disambiguate duplicate labels (e.g. "Lease liabilities" appears under both
non-current and current liabilities). Previously this was hard-coded against
one SOFP template. For the multi-statement rollout, the same detector has to
work across 9 MBRS templates — and it has to keep working on the legacy
`SOFP-Xbrl-template.xlsx` until Phase 7 retires it.

How: rows in MBRS templates use fill color to signal their role:

    Header rows     -> coloured fill, not bold-only; repeated per section
    Total rows      -> coloured fill with "Total" in the label
    Line items      -> default / white fill

We recognise header rows by (a) a whitelist of header fill colours observed
across both the legacy and new template families, and (b) a keyword set the
caller can pass in (scout populates this from the registry).
"""
from __future__ import annotations

from dataclasses import dataclass

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet


# ARGB fill codes used to paint section-header rows. Two template families:
#   FFC0C0C0 — legacy grey (SOFP-Xbrl-template.xlsx)
#   FFD6E4F0 — new blue   (XBRL-template-MFRS/*.xlsx)
_HEADER_FILL_RGB = frozenset({"FFC0C0C0", "FFD6E4F0"})

# ARGB fill codes used to paint Total rows. We exclude these so totals don't
# get mistaken for section headers.
_TOTAL_FILL_RGB = frozenset({"FF99CCFF", "FFE2EFDA"})


@dataclass(frozen=True)
class SectionHeader:
    row: int
    label: str             # original cell text, trimmed
    normalized: str        # lowercased, leading '*' stripped


def _normalize(label: str) -> str:
    return label.strip().lstrip("*").strip().lower()


def _cell_fill_rgb(cell) -> str | None:
    """Return the fill's ARGB string if set, else None.

    openpyxl sometimes returns a `Color` object whose `rgb` is `None` when the
    fill is theme-indexed rather than RGB — we treat that as "no fill" since
    none of our templates use themed fills for headers.
    """
    if not cell.fill or not cell.fill.fgColor:
        return None
    rgb = cell.fill.fgColor.rgb
    if isinstance(rgb, str):
        return rgb
    return None


def discover_section_headers(
    ws: Worksheet,
    extra_keywords: frozenset[str] | None = None,
) -> list[SectionHeader]:
    """Return the section-header rows in worksheet order.

    Walks column A top-to-bottom. A row is classified as a header if either:
      1. its column-A cell has a header-coloured fill AND is not a total row, or
      2. its normalised label matches one of `extra_keywords`.
    """
    extra = extra_keywords or frozenset()
    headers: list[SectionHeader] = []

    for row in range(1, ws.max_row + 1):
        cell = ws.cell(row=row, column=1)
        if cell.value is None:
            continue
        label_raw = str(cell.value).strip()
        if not label_raw:
            continue
        norm = _normalize(label_raw)

        rgb = _cell_fill_rgb(cell)
        is_header_fill = rgb in _HEADER_FILL_RGB
        is_total_fill = rgb in _TOTAL_FILL_RGB

        # Total rows are coloured too but they're not section headers.
        if is_total_fill or norm.startswith("total "):
            continue

        if is_header_fill or norm in extra:
            headers.append(SectionHeader(row=row, label=label_raw, normalized=norm))

    return headers


def header_set(
    wb: openpyxl.Workbook,
    sheet_name: str,
    extra_keywords: frozenset[str] | None = None,
) -> frozenset[str]:
    """Convenience: normalized-label set for a sheet, used by fill_workbook."""
    if sheet_name not in wb.sheetnames:
        return frozenset()
    return frozenset(
        h.normalized for h in discover_section_headers(wb[sheet_name], extra_keywords)
    )
