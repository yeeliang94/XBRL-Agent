import json
import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import openpyxl

from tools.section_headers import header_set

logger = logging.getLogger(__name__)


@dataclass
class FieldMapping:
    sheet: str
    field_label: str
    col: int  # 2 = CY (column B), 3 = PY (column C)
    value: Optional[float]
    # Section hint for disambiguating duplicate labels (e.g. "current" vs "non-current")
    section: str = ""
    # Legacy row-based fallback
    row: Optional[int] = None
    evidence: str = ""


@dataclass
class FillResult:
    success: bool
    fields_written: int
    output_path: str
    errors: list[str]


def fill_workbook(
    template_path: str,
    output_path: str,
    fields_json: str,
) -> FillResult:
    """Apply structured field mappings to an Excel template.

    Matches fields by label (column A text) with section-aware disambiguation.
    When a label appears multiple times (e.g. "Lease liabilities" under both
    non-current and current), the section hint ("current"/"non-current") picks
    the correct occurrence.
    """
    template = Path(template_path)
    if not template.exists():
        return FillResult(
            success=False,
            fields_written=0,
            output_path="",
            errors=[f"Template not found: {template_path}"],
        )

    try:
        mappings = _parse_fields_json(fields_json)
    except Exception as e:
        return FillResult(
            success=False,
            fields_written=0,
            output_path="",
            errors=[f"Invalid JSON: {e}"],
        )

    wb = openpyxl.load_workbook(template_path)
    errors: list[str] = []
    fields_written = 0

    # Build section-aware label index per sheet
    label_index = _build_label_index(wb)

    for mapping in mappings:
        if mapping.sheet not in wb.sheetnames:
            errors.append(f"Sheet '{mapping.sheet}' not found in template")
            continue

        ws = wb[mapping.sheet]

        # Resolve the target row: match by label first, fall back to explicit row
        target_row = None
        if mapping.field_label:
            target_row = _find_row_by_label(
                label_index.get(mapping.sheet, []),
                mapping.field_label,
                section_hint=mapping.section,
            )
            if target_row is None:
                errors.append(
                    f"No matching label for '{mapping.field_label}'"
                    f"{f' (section: {mapping.section})' if mapping.section else ''}"
                    f" in sheet '{mapping.sheet}'."
                    f" Check the exact label text from read_template()."
                )
                continue
        elif mapping.row is not None:
            target_row = mapping.row
        else:
            errors.append(f"Field has neither label nor row: {mapping}")
            continue

        cell = ws.cell(row=target_row, column=mapping.col)

        # Never overwrite formula cells
        if cell.value is not None and str(cell.value).startswith("="):
            errors.append(
                f"Refusing to overwrite formula cell {mapping.sheet}!{cell.coordinate}: {cell.value}"
            )
            continue

        cell.value = mapping.value
        fields_written += 1

        # Write evidence/source to a single column per sheet so notes don't repeat.
        # SOCIE sheets use cols B-X for equity components, so evidence goes to col Y (25).
        # All other sheets: evidence always goes to col D (4) — one column, not per-period.
        if mapping.evidence:
            if "socie" in mapping.sheet.lower():
                evidence_col = 25  # Y — after Total (X=24)
            else:
                evidence_col = 4  # D — single evidence column for all periods
            evidence_cell = ws.cell(row=target_row, column=evidence_col)
            # Always overwrite evidence so correction passes don't accumulate
            # stale provenance from values that were later replaced.
            evidence_cell.value = mapping.evidence

    wb.save(output_path)
    wb.close()

    if errors and fields_written == 0:
        return FillResult(
            success=False,
            fields_written=0,
            output_path=output_path,
            errors=errors,
        )

    return FillResult(
        success=True,
        fields_written=fields_written,
        output_path=output_path,
        errors=errors,
    )


@dataclass
class _LabelEntry:
    """A label in the template with its row and the section it belongs to."""
    normalized_label: str
    row: int
    section: str  # e.g. "non-current assets", "current liabilities"


# Legacy keyword fallbacks. These mirror the section headers previously
# hard-coded here — we keep them as an `extra_keywords` safety net for the
# `header_set()` discovery helper, in case a template ships a section header
# without the expected fill colour. Discovery by fill colour is the primary
# path; these keywords only add missing entries, never remove any.
_LEGACY_MAIN_HEADER_KEYWORDS = frozenset({
    "non-current assets",
    "current assets",
    "equity",
    "non-current liabilities",
    "current liabilities",
})

_LEGACY_SUB_HEADER_KEYWORDS = _LEGACY_MAIN_HEADER_KEYWORDS | frozenset({
    "property, plant and equipment",
    "investment property",
    "biological assets",
    "intangible assets",
    "investments in subsidiaries",
    "investments in associates",
    "investments in joint ventures",
    "non-current trade receivables",
    "current trade receivables",
    "non-current derivative financial assets",
    "current derivative financial assets",
    "inventories",
    "cash and cash equivalents",
    "non-current borrowings",
    "current borrowings",
    "non-current employee benefit liabilities",
    "current employee benefit liabilities",
    "non-current provisions",
    "current provisions",
    "non-current trade payables",
    "current trade payables",
    "non-current non-trade payables",
    "current non-trade payables",
    "non-current derivative financial liabilities",
    "current derivative financial liabilities",
})


def _build_label_index(wb: openpyxl.Workbook) -> dict[str, list[_LabelEntry]]:
    """Build a section-aware label index per sheet.

    Walks column A top-to-bottom, tracking which section we're in based on
    header rows. Each label is stored with its section context.
    """
    index: dict[str, list[_LabelEntry]] = {}
    for name in wb.sheetnames:
        ws = wb[name]
        entries: list[_LabelEntry] = []
        current_section = ""

        # Use granular sections only for sub-sheets; main sheet uses top-level only.
        # Section headers are discovered from the template itself (fill colour)
        # so new MBRS templates work without code changes; the legacy keyword
        # set is passed as a safety net for any header missing a coloured fill.
        is_sub_sheet = "sub" in name.lower() or "analysis" in name.lower()
        fallback = _LEGACY_SUB_HEADER_KEYWORDS if is_sub_sheet else _LEGACY_MAIN_HEADER_KEYWORDS
        headers = header_set(wb, name, extra_keywords=fallback)

        for row in range(1, ws.max_row + 1):
            cell_val = ws.cell(row=row, column=1).value
            if cell_val is None:
                continue

            normalized = _normalize_label(str(cell_val))

            # Check if this row is a section header
            if normalized in headers:
                current_section = normalized

            entries.append(_LabelEntry(
                normalized_label=normalized,
                row=row,
                section=current_section,
            ))

        index[name] = entries
    return index


def _normalize_label(label: str) -> str:
    """Strip leading *, whitespace, and lowercase for matching."""
    return label.strip().lstrip("*").strip().lower()


def _find_row_by_label(
    entries: list[_LabelEntry],
    field_label: str,
    section_hint: str = "",
    threshold: float = 0.7,
) -> Optional[int]:
    """Find the best matching row for a field label.

    When section_hint is provided (e.g. "current"), uses it to disambiguate
    duplicate labels by filtering to entries whose section contains the hint.
    """
    normalized = _normalize_label(field_label)
    section_hint_lower = (section_hint or "").strip().lower()

    # Collect all exact matches
    exact_matches = [e for e in entries if e.normalized_label == normalized]

    if exact_matches:
        if len(exact_matches) == 1:
            return exact_matches[0].row

        # Multiple matches — use section hint to disambiguate
        if section_hint_lower:
            # 1. Exact section match
            filtered = [e for e in exact_matches if e.section == section_hint_lower]
            if not filtered:
                # 2. Section starts with hint or hint starts with section
                filtered = [
                    e for e in exact_matches
                    if e.section.startswith(section_hint_lower) or section_hint_lower.startswith(e.section)
                ]
            if not filtered:
                # 3. Keyword disambiguation: if hint contains "current" (but not
                # "non-current"), prefer matches in a "current" section, and vice versa
                hint_has_current = "current" in section_hint_lower
                hint_has_noncurrent = "non-current" in section_hint_lower
                if hint_has_current and not hint_has_noncurrent:
                    filtered = [e for e in exact_matches if "current" in e.section and "non-current" not in e.section]
                elif hint_has_noncurrent:
                    filtered = [e for e in exact_matches if "non-current" in e.section]
            if len(filtered) == 1:
                return filtered[0].row
            if filtered:
                return filtered[0].row

        # No section hint — return the first occurrence (legacy behavior)
        return exact_matches[0].row

    # No exact match — fuzzy match across all entries
    best_score = 0.0
    best_row = None
    for entry in entries:
        score = SequenceMatcher(None, normalized, entry.normalized_label).ratio()
        if score > best_score:
            best_score = score
            best_row = entry.row

    if best_score >= threshold and best_row is not None:
        return best_row

    return None


def _parse_fields_json(fields_json: str) -> list[FieldMapping]:
    data = json.loads(fields_json)

    if isinstance(data, dict) and "fields" in data:
        items = data["fields"]
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError('Expected a list of field mappings or {"fields": [...]}')

    mappings = []
    for item in items:
        mappings.append(
            FieldMapping(
                sheet=item["sheet"],
                field_label=item.get("field_label", ""),
                col=int(item.get("col", 2)),
                value=item.get("value"),
                section=item.get("section") or "",
                row=int(item["row"]) if "row" in item else None,
                evidence=item.get("evidence", ""),
            )
        )

    return mappings
