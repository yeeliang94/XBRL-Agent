import json
import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import openpyxl

from tools.section_headers import (
    discover_section_headers,
    header_set,
    keyword_fallback_for_sheet,
)

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
    # RUN-REVIEW P1-1 (2026-04-26): non-fatal warnings surfaced to the
    # agent so it can self-correct double-booking before the next
    # verify_totals pass. Empty list when no concerns detected.
    warnings: list[str] = field(default_factory=list)


# Default SOCIE evidence column for the MFRS 24-col equity-component matrix.
# Lives just past Total (col X = 24); MFRS templates have no row-1 "Source"
# header so we fall back to this. MPERS templates declare a real Source
# header at col D / F and `_resolve_socie_evidence_col` honours that.
_DEFAULT_MATRIX_SOCIE_EVIDENCE_COL = 25


def _resolve_socie_evidence_col(ws) -> int:
    """Return the column where SOCIE evidence/source should be written.

    Looks for a row-1 cell whose text equals "Source" (case-insensitive).
    The MPERS Group/Company SOCIE templates publish this header at col D
    (4-col layout) and the MPERS Group SoRE template at col F. The MFRS
    matrix SOCIE templates carry no Source header — fall back to col Y
    (25) so existing MFRS behaviour is preserved.
    """
    for col in range(1, ws.max_column + 1):
        value = ws.cell(row=1, column=col).value
        if isinstance(value, str) and value.strip().lower() == "source":
            return col
    return _DEFAULT_MATRIX_SOCIE_EVIDENCE_COL


def _evidence_token_overlap(a: str, b: str) -> int:
    """Count distinct lowercase tokens (≥4 chars) shared by two evidence
    strings. Used by the double-booking guard (RUN-REVIEW P1-1) to decide
    whether two same-value writes really refer to the same disclosure.
    Short tokens are ignored because words like "of", "and", "the", "RM"
    overlap on every pair and would make the guard fire constantly.
    """
    if not a or not b:
        return 0
    norm = lambda s: {  # noqa: E731
        tok for tok in
        ''.join(c.lower() if c.isalnum() else ' ' for c in s).split()
        if len(tok) >= 4
    }
    return len(norm(a) & norm(b))


def _detect_double_bookings(
    label_index: dict[str, "list[_LabelEntry]"],
    written: list[FieldMapping],
    *,
    min_value: float = 1.0,
    overlap_threshold: int = 3,
) -> list[str]:
    """Return human-readable warnings about same-value/same-section writes.

    RUN-REVIEW §3.3-D: the Amway run wrote restoration provision PY 1,881
    onto BOTH row 287 (Provision for decommissioning…) and row 318 (Other
    non-current non-trade payables) in the same Non-current liabilities
    section. The face balance still passes because *Total non-current
    liabilities sums both — but the value is double-booked.

    The guard is intentionally narrow:

    * Same sheet, same column, same numeric value.
    * Evidence-string token overlap ≥ ``overlap_threshold`` distinct
      tokens of length ≥ 4. Disjoint evidence (two different
      disclosures that happen to round to the same number) does NOT
      trip the guard — that's a coincidence, not a double-book.
    * Tiny values (|val| < ``min_value``) are ignored — zeros and
      single-digit RM amounts coincide too often to be meaningful.

    Note we do NOT require both rows to share an exact section label.
    The Amway bug had row 287 (section "non-current provisions") and
    row 318 (section "non-current non-trade payables") — peer
    sub-sections under Non-current liabilities. Forcing exact-section
    match would silently miss this real failure mode. The
    evidence-overlap discriminator carries the load instead.

    The guard is column-scoped so legitimate consolidation pass-through
    on Group filings (same value in Group-CY col B AND Company-CY col
    D for the same row) does NOT trigger — that's by design, the
    discriminator is whether we're seeing two ROWS with the same value
    in ONE column.
    """
    warnings: list[str] = []
    if not written:
        return warnings

    # Group writes by (sheet, col) so we only compare within the same
    # column — Group consolidation pass-through (same value in Group +
    # Company columns) is legitimate and must not warn.
    by_col: dict[tuple[str, int], list[tuple[FieldMapping, "_LabelEntry"]]] = {}
    for m in written:
        if m.value is None:
            continue
        try:
            v = float(m.value)
        except (TypeError, ValueError):
            continue
        if abs(v) < min_value:
            continue
        sheet_entries = label_index.get(m.sheet, [])
        # Find the entry for the row we wrote to (already resolved during
        # the main loop; here we just look it up by mapping fields).
        entry = None
        for e in sheet_entries:
            if m.row is not None and e.row == m.row:
                entry = e
                break
            if m.field_label and e.normalized_label == _normalize_label(m.field_label):
                # Section-aware match when row coordinate isn't carried.
                if m.section and m.section.lower() not in e.section.lower():
                    continue
                entry = e
                break
        if entry is None or entry.is_header:
            continue
        by_col.setdefault((m.sheet, m.col), []).append((m, entry))

    for (sheet, col), items in by_col.items():
        # Find pairs sharing (value, overlapping-evidence) within this
        # (sheet, col). We do NOT gate on exact section match — the
        # canonical Amway bug straddled peer sub-sections, see the
        # docstring rationale above. Section labels are still surfaced
        # in the warning text so the agent has navigation context.
        for i, (m_a, e_a) in enumerate(items):
            for m_b, e_b in items[i + 1:]:
                if e_a.row == e_b.row:
                    continue
                # Cast through float so 1881 == 1881.0 etc.
                if abs(float(m_a.value) - float(m_b.value)) > 0.5:
                    continue
                overlap = _evidence_token_overlap(m_a.evidence, m_b.evidence)
                if overlap < overlap_threshold:
                    continue
                col_letter = openpyxl.utils.get_column_letter(col)
                same_section = e_a.section == e_b.section
                section_note = (
                    f"section '{e_a.section}'"
                    if same_section
                    else f"sections '{e_a.section}' / '{e_b.section}'"
                )
                warnings.append(
                    f"Possible double-booking on {sheet} col {col_letter} "
                    f"{section_note}: value {m_a.value} appears "
                    f"on row {e_a.row} ('{m_a.field_label or ''}') AND "
                    f"row {e_b.row} ('{m_b.field_label or ''}') with "
                    f"overlapping evidence ({overlap} shared token(s)). "
                    f"If both are correct, leave them; if one is the "
                    f"wrong row, remove it before the next verify_totals."
                )
    return warnings


def fill_workbook(
    template_path: str,
    output_path: str,
    fields_json: str,
    filing_level: str = "company",
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
    # RUN-REVIEW P1-1: track successful writes so the post-loop double-
    # booking guard only sees mappings that actually landed (skipped
    # writes shouldn't raise spurious warnings).
    successful_writes: list[FieldMapping] = []

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
            # Bug 5b — if the agent supplied a row coordinate (no field_label),
            # check that col A at that row actually has a label. The MPERS
            # SOCIE bug was exactly this: socie.md's MFRS-matrix instructions
            # told the agent to write at rows 30/35/49, which on the MPERS
            # Company template have NO label in col A — the writes landed on
            # blank cells silently. Row 1 is the documented carve-out for
            # date cells (see `prompts/_base.md`). Any other labelless row
            # means the agent is targeting a row that does not exist in the
            # current template.
            if target_row != 1:
                col_a_value = ws.cell(row=target_row, column=1).value
                if col_a_value is None or not str(col_a_value).strip():
                    # S-5: earlier wording said "this row does not exist in
                    # the loaded template" — technically wrong (the row
                    # exists, the LABEL is absent). The new phrasing points
                    # at the real fix: field_label matching, and cross-
                    # check against read_template if the agent believed
                    # the row was intentional.
                    errors.append(
                        f"Refusing to write to {mapping.sheet} row {target_row}: "
                        f"col A is empty — this row has no label. Use "
                        f"field_label matching, or call read_template() to "
                        f"confirm the row is the one you intended."
                    )
                    continue
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

        # Bug A (2026-04-26): refuse writes to abstract section-header rows.
        # The screenshot bug on SOPL-Analysis-Function had the agent writing
        # 6,092 onto the dark-navy "Interest income" row instead of the
        # leaves below — the formula-driven "Total interest income" then
        # evaluated to 0 because the leaves were empty. The header is an
        # XBRL abstract concept, never a data target. We look the row up
        # in the same `label_index` we just built so the check stays cheap
        # and consistent with `_find_row_by_label`'s leaf-preference logic.
        sheet_entries = label_index.get(mapping.sheet, [])
        target_entry = next(
            (e for e in sheet_entries if e.row == target_row),
            None,
        )
        if target_entry is not None and target_entry.is_header:
            label_text = ws.cell(row=target_row, column=1).value
            errors.append(
                f"Refusing to write to {mapping.sheet}!{cell.coordinate}: "
                f"row {target_row} ('{label_text}') is an XBRL abstract "
                f"section header, not a data-entry cell. Write to a leaf "
                f"row under it (call read_template() and look for non-"
                f"[ABSTRACT] rows in this section), or roll the value up "
                f"into the nearest matching leaf. Never plug a residual "
                f"into a catch-all to make totals reconcile."
            )
            continue

        cell.value = mapping.value
        fields_written += 1
        # Stash a copy with the resolved row coordinate so the double-
        # booking guard doesn't have to redo label/section matching.
        successful_writes.append(FieldMapping(
            sheet=mapping.sheet,
            field_label=mapping.field_label,
            col=mapping.col,
            value=mapping.value,
            section=mapping.section,
            row=target_row,
            evidence=mapping.evidence,
        ))

        # Write evidence/source to a single column per sheet so notes don't repeat.
        #
        # SOCIE sheets historically used col Y (25) because the MFRS template is
        # a 24-col equity-component matrix with no Source header. MPERS SOCIE
        # templates publish a real Source header at col D (Company) or F (Group),
        # so writing to col 25 there hides the audit trail off-screen and leaves
        # the visible Source column empty (peer-review H2). For SOCIE sheets we
        # now look up the Source header by name and only fall back to 25 when
        # no header is found (MFRS matrix layouts). Other sheets keep the
        # filing-level branch as before.
        if mapping.evidence:
            if "socie" in mapping.sheet.lower():
                evidence_col = _resolve_socie_evidence_col(ws)
            elif filing_level == "group":
                evidence_col = 6  # F — after Company PY (E=5)
            else:
                evidence_col = 4  # D — after PY (C=3)
            evidence_cell = ws.cell(row=target_row, column=evidence_col)
            # Always overwrite evidence so correction passes don't accumulate
            # stale provenance from values that were later replaced.
            evidence_cell.value = mapping.evidence

    wb.save(output_path)
    wb.close()

    # RUN-REVIEW P1-1: scan successful writes for double-bookings now
    # that the workbook is closed. Warnings are advisory — they don't
    # flip success to False — but they bubble up to the agent so it
    # can decide whether to keep both rows or remove one before the
    # next verify_totals pass.
    warnings = _detect_double_bookings(label_index, successful_writes)

    if errors and fields_written == 0:
        return FillResult(
            success=False,
            fields_written=0,
            output_path=output_path,
            errors=errors,
            warnings=warnings,
        )

    return FillResult(
        success=True,
        fields_written=fields_written,
        output_path=output_path,
        errors=errors,
        warnings=warnings,
    )


@dataclass
class _LabelEntry:
    """A label in the template with its row and the section it belongs to."""
    normalized_label: str
    row: int
    section: str  # e.g. "non-current assets", "current liabilities"
    # Bug A (2026-04-26): True when this label is itself a section-header
    # (XBRL-abstract) row. Used by `_find_row_by_label` to prefer leaves
    # over headers on duplicate labels, and by the writer to refuse writes
    # whose target lands on an abstract row.
    is_header: bool = False


# Keyword fallback registry now lives in `tools.section_headers` so the
# reader (template_reader) and the writer share one source of truth — see
# peer-review #1 (2026-04-26) and `keyword_fallback_for_sheet`.


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

        # Detect header rows by row index (not label string). The legacy
        # form returned a set of normalised labels, which mis-marked any
        # leaf with the same text as a header — that was itself part of
        # the SOPL-Analysis duplicate-label bug. Keyword fallback selection
        # is shared with template_reader via section_headers — see
        # peer-review #1 (2026-04-26).
        fallback = keyword_fallback_for_sheet(name)
        header_rows = {
            h.row for h in discover_section_headers(ws, extra_keywords=fallback)
        }

        for row in range(1, ws.max_row + 1):
            cell_val = ws.cell(row=row, column=1).value
            if cell_val is None:
                continue

            normalized = _normalize_label(str(cell_val))
            is_header = row in header_rows
            # Section transitions: every header switches the running
            # section. Leaves that happen to share a header's label do
            # not — they keep their parent's section because is_header
            # is row-based.
            if is_header:
                current_section = normalized

            entries.append(_LabelEntry(
                normalized_label=normalized,
                row=row,
                section=current_section,
                is_header=is_header,
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

    # Bug A (2026-04-26): if the label has both a header occurrence and a
    # leaf occurrence (the "Other fee and commission income" case on
    # SOPL-Analysis), prefer the leaves. The header is XBRL-abstract — the
    # writer's separate header guard will refuse it anyway, and bumping
    # past it here lets the legitimate leaf write succeed without forcing
    # the agent to add a section hint it shouldn't need.
    if exact_matches and any(not e.is_header for e in exact_matches):
        exact_matches = [e for e in exact_matches if not e.is_header]

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
