"""Reverse-map a human-filled mTool workbook into gold facts (Step C1).

An accountant's hand-filled SSM mTool template already IS a gold answer. The
mTool fill pipeline (mtool/) reads these files to *write* them; here we run the
same readers in reverse to *ingest* them: match each mTool value row back to a
concept and emit gold facts in the shape ``gold_concept_facts`` expects.

Design constraints (mirroring gotcha #28):

* **Reuse, don't reinvent.** Cell reading, label mapping, column detection all
  come from ``mtool.offline_fill`` / ``mtool.column_detect`` — no parallel
  parser.
* **Strict label matching.** A row whose label doesn't match a concept exactly
  (after the shared ``normalize_label``) is surfaced verbatim as unmatched, never
  fuzzy-guessed into a concept.
* **The user declares the unit.** ``unit_scale`` is applied verbatim (authoritative);
  a magnitude backstop only WARNS when the numbers look inconsistent with it.
* **Numeric gold only here.** Prose footnotes are captured separately
  (``source_note_texts`` / Step C2). This module handles the value grid.

The heavy lifting is split so it's testable in isolation:

* :func:`build_catalogue` — read the concept catalogue from the DB (impure but
  trivial).
* :func:`ingest_workbook` — file → :class:`IngestReport` (composes the mTool
  readers).
"""
from __future__ import annotations

import logging
import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("server")

from mtool.column_detect import detect_column_map
from mtool.offline_fill import (
    build_label_map,
    get_shared_strings,
    get_sheet_paths,
    load_workbook_entries,
    normalize_label,
    read_footnote_rows,
    read_sheet_cells,
)

# A value column role maps to exactly one (period, entity_scope) slot — the
# inverse of exporter._column_role. Company filings use current_year/prior_year;
# group filings use the four explicit group_*/company_* roles.
_ROLE_TO_SLOT: dict[str, tuple[str, str]] = {
    "current_year": ("CY", "Company"),
    "prior_year": ("PY", "Company"),
    "group_current_year": ("CY", "Group"),
    "group_prior_year": ("PY", "Group"),
    "company_current_year": ("CY", "Company"),
    "company_prior_year": ("PY", "Company"),
}

_ROLES_BY_LEVEL: dict[str, tuple[str, ...]] = {
    "company": ("current_year", "prior_year"),
    "group": (
        "group_current_year",
        "group_prior_year",
        "company_current_year",
        "company_prior_year",
    ),
}


class ColumnDetectionError(Exception):
    """Raised when the value-column layout can't be auto-detected with
    confidence and no explicit column map was supplied. The endpoint turns this
    into an actionable 'point at the value columns' response."""

    def __init__(self, low_sheets: list[str]):
        self.low_sheets = low_sheets
        super().__init__(
            "Could not confidently detect value columns for: "
            + ", ".join(low_sheets)
        )


@dataclass
class ConceptTarget:
    concept_uuid: str
    template_id: str
    canonical_label: str
    statement_type: str


@dataclass
class GoldFact:
    concept_uuid: str
    period: str
    entity_scope: str
    value: float


@dataclass
class NoteText:
    """A prose payload captured from an mTool footnote (Step C2). ``note_key`` is
    the mTool ``fn_N`` join key; ``text`` is the XHTML the human filed."""
    note_key: str
    text: str


@dataclass
class IngestReport:
    facts: list[GoldFact] = field(default_factory=list)
    matched_by_statement: dict[str, int] = field(default_factory=dict)
    # mTool rows carrying a label + at least one value that matched NO concept.
    unmatched_rows: list[dict[str, Any]] = field(default_factory=list)
    # Concept sheets that don't appear in the uploaded file at all.
    sheets_missing: list[str] = field(default_factory=list)
    # Labels that matched more than one template row (surfaced, not guessed).
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    scale_warning: Optional[str] = None
    template_ids: set[str] = field(default_factory=set)
    # SOCIE / MATRIX_CELL concepts in scope that were NOT ingested. Mirrors the
    # mTool exporter's "deferred and counted, never silently dropped" contract
    # (gotcha #28): matrix cells share row labels across equity-component
    # columns and mTool's SOCIE layout is Windows-recon-gated, so reverse-
    # mapping them is deferred — but the count is surfaced so the coverage gap
    # is visible, not a silent denominator shrink.
    matrix_deferred: int = 0

    @property
    def fact_count(self) -> int:
        return len(self.facts)


def build_catalogue(
    conn: sqlite3.Connection,
    filing_standard: str,
    filing_level: str,
    template_ids: Optional[list[str]] = None,
) -> dict[str, dict[str, ConceptTarget]]:
    """Concept catalogue for reverse mapping: ``{render_sheet: {normalized
    label: ConceptTarget}}``.

    Scoped to LEAF concepts (the only fillable rows) in the ``{standard}-{level}-``
    family. When ``template_ids`` is given the scope is tightened to that exact
    set — the variant-precise scoping every eval path needs (gotcha #21), so an
    SOFP-CuNonCu benchmark doesn't pull SOFP-OrderOfLiquidity concepts.
    ``statement_type`` is read from ``concept_templates`` when present, else
    derived from the template_id.
    """
    family = f"{filing_standard.lower()}-{filing_level.lower()}-"
    params: list[Any] = [family + "%"]
    scope_sql = "n.template_id LIKE ?"
    if template_ids:
        placeholders = ",".join("?" for _ in template_ids)
        scope_sql += f" AND n.template_id IN ({placeholders})"
        params.extend(template_ids)
    else:
        # Family-only scope spans BOTH variants of every statement, whose
        # (sheet, label) pairs can collide with different uuids (gotcha #21) —
        # a last-write-wins overwrite would silently pick one variant. All
        # production callers pass an explicit set; warn loudly if one doesn't.
        logger.warning(
            "build_catalogue called without template_ids — scoping to the whole "
            "%s%s family. Cross-variant label collisions resolve last-wins; pass "
            "an explicit variant set to avoid ambiguity (gotcha #21).",
            filing_standard, filing_level,
        )
    rows = conn.execute(
        "SELECT n.concept_uuid, n.template_id, n.canonical_label, "
        "n.render_sheet FROM concept_nodes n "
        f"WHERE n.kind = 'LEAF' AND {scope_sql}",
        tuple(params),
    ).fetchall()

    out: dict[str, dict[str, ConceptTarget]] = {}
    for r in rows:
        uuid, template_id, label, sheet = r[0], r[1], r[2], r[3]
        stmt = _statement_from_template_id(template_id)
        norm = normalize_label(label)
        if not norm:
            continue
        sheet_map = out.setdefault(sheet, {})
        prior = sheet_map.get(norm)
        if prior is not None and prior.template_id != template_id:
            # Two variants claim the same (sheet, label) — surface it rather
            # than silently overwriting.
            logger.warning(
                "build_catalogue: label %r on sheet %r exists in both %s and %s "
                "— keeping the first; scope by template_ids to disambiguate.",
                label, sheet, prior.template_id, template_id,
            )
            continue
        sheet_map[norm] = ConceptTarget(
            concept_uuid=uuid,
            template_id=template_id,
            canonical_label=label,
            statement_type=stmt,
        )
    return out


def extract_prose_gold(path: str | Path) -> list[NoteText]:
    """Capture every populated footnote prose payload from a filled mTool file.

    Capture-only (Step C2): the human's actual note text is stored as gold now
    so a future prose-fidelity pass has ground truth, without a second ingest.
    Nothing grades it in Phase 1. Keyed by the ``fn_N`` join key (unique in the
    footnote sheet)."""
    _, data, _ = load_workbook_entries(str(path))
    sheet_paths = get_sheet_paths(data)
    sst = get_shared_strings(data)
    fn_rows = read_footnote_rows(data, sheet_paths, sst)
    out: list[NoteText] = []
    for key, info in fn_rows.items():
        text = info.get("payload_text")
        if info.get("payload_populated") and text and text.strip():
            out.append(NoteText(note_key=key, text=text))
    return out


def count_deferred_matrix(
    conn: sqlite3.Connection,
    filing_standard: str,
    filing_level: str,
    template_ids: Optional[list[str]] = None,
) -> int:
    """Count MATRIX_CELL concepts in scope that ingest defers (SOCIE).

    The grader treats LEAF and MATRIX_CELL alike (gotcha #23), but the mTool
    exporter defers matrix cells and so does this reverse path (gotcha #28) —
    this makes the deferral COUNTED (never silently dropped) so the operator
    knows SOCIE wasn't captured, rather than seeing an inflated-looking clean
    grade over a silently smaller denominator."""
    family = f"{filing_standard.lower()}-{filing_level.lower()}-"
    params: list[Any] = [family + "%"]
    scope_sql = "n.template_id LIKE ?"
    if template_ids:
        placeholders = ",".join("?" for _ in template_ids)
        scope_sql += f" AND n.template_id IN ({placeholders})"
        params.extend(template_ids)
    row = conn.execute(
        "SELECT COUNT(*) FROM concept_nodes n "
        f"WHERE n.kind = 'MATRIX_CELL' AND {scope_sql}",
        tuple(params),
    ).fetchone()
    return int(row[0]) if row else 0


def _statement_from_template_id(template_id: str) -> str:
    """'mfrs-company-sofp-cunoncu-v1' -> 'SOFP'. The statement token is the
    third dash-segment; upper-cased for display."""
    parts = template_id.split("-")
    return parts[2].upper() if len(parts) >= 3 else "OTHER"


def ingest_workbook(
    path: str | Path,
    catalogue: dict[str, dict[str, ConceptTarget]],
    *,
    filing_level: str,
    unit_scale: float = 1.0,
    column_map_override: Optional[dict[str, dict[str, Any]]] = None,
) -> IngestReport:
    """Read a filled mTool workbook and produce gold facts + an ingest report.

    ``catalogue`` comes from :func:`build_catalogue`. ``unit_scale`` is the
    factor the user's declared unit implies (1.0 = figures as-is, 1000.0 = the
    file is in thousands and stores the thousands figure). ``column_map_override``
    lets the caller supply the value-column layout explicitly when auto-detection
    is refused.
    """
    level = (filing_level or "company").lower()
    roles = _ROLES_BY_LEVEL.get(level, _ROLES_BY_LEVEL["company"])

    _, data, _ = load_workbook_entries(str(path))
    sheet_paths = get_sheet_paths(data)
    sst = get_shared_strings(data)

    column_map = column_map_override or _detect(path, catalogue, roles, data)

    report = IngestReport()
    for sheet, targets in catalogue.items():
        entry = sheet_paths.get(sheet)
        if entry is None:
            report.sheets_missing.append(sheet)
            continue
        cfg = column_map.get(sheet, {})
        label_col = cfg.get("label_column")
        role_cols = cfg.get("columns", {})
        if not label_col:
            report.sheets_missing.append(sheet)
            continue

        cells = read_sheet_cells(data[entry], sst)
        label_map = build_label_map(cells, label_col)
        matched_rows: set[int] = set()

        for norm_label, target in targets.items():
            # STRICT exact match only (after the shared normalize_label). No
            # fuzzy fallback — an off-template label must surface as unmatched,
            # not be silently coerced into a concept (gotcha #28).
            hits = label_map.get(norm_label)
            if not hits:
                continue
            if len(hits) > 1:
                report.ambiguous.append(
                    {"sheet": sheet, "label": target.canonical_label,
                     "detail": f"label matches rows {[r for r, _ in hits]}"}
                )
                continue
            row_num = hits[0][0]
            matched_rows.add(row_num)
            emitted = 0
            for role in roles:
                col = role_cols.get(role)
                if not col:
                    continue
                value = _numeric(cells.get(row_num, {}).get(col))
                if value is None:
                    continue
                period, scope = _ROLE_TO_SLOT[role]
                report.facts.append(
                    GoldFact(target.concept_uuid, period, scope,
                             value * unit_scale)
                )
                emitted += 1
            if emitted:
                report.template_ids.add(target.template_id)
                report.matched_by_statement[target.statement_type] = (
                    report.matched_by_statement.get(target.statement_type, 0) + 1
                )

        _collect_unmatched(report, sheet, cells, label_col, role_cols, matched_rows)

    report.scale_warning = _scale_backstop(report.facts, unit_scale)
    return report


def _detect(path, catalogue, roles, data) -> dict[str, dict[str, Any]]:
    """Run column detection over the catalogue's sheets, refusing low
    confidence (the caller must then supply an explicit map)."""
    doc = {
        "sheets": {
            sheet: {"columns": {role: None for role in roles}}
            for sheet in catalogue
        }
    }
    column_map = detect_column_map(str(path), doc, data=data)
    # A sheet absent from the file is 'missing', not a detection failure — only
    # sheets that ARE present but couldn't be read confidently block ingest.
    sheet_paths = get_sheet_paths(data)
    low = [
        sheet
        for sheet, cfg in column_map.items()
        if sheet in sheet_paths and cfg.get("confidence") != "high"
    ]
    if low:
        raise ColumnDetectionError(low)
    return column_map


def _numeric(cell: Optional[tuple]) -> Optional[float]:
    """Parse a value cell to float, or None. Only genuine numbers ('N') count —
    a formula-derived total ('F') is not a human-entered value and LEAF rows
    shouldn't carry one anyway."""
    if not cell:
        return None
    kind, text = cell
    if kind != "N":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _collect_unmatched(
    report: IngestReport,
    sheet: str,
    cells: dict,
    label_col: str,
    role_cols: dict,
    matched_rows: set[int],
) -> None:
    """Record mTool rows that carry a label AND ≥1 numeric value but matched no
    concept — surfaced verbatim so a systematic label drift is obvious."""
    value_cols = [c for c in role_cols.values() if c]
    for row_num, row_cells in cells.items():
        if row_num in matched_rows:
            continue
        label_cell = row_cells.get(label_col)
        if not label_cell or label_cell[0] != "S" or not label_cell[1].strip():
            continue
        values = {
            col: _numeric(row_cells.get(col))
            for col in value_cols
            if _numeric(row_cells.get(col)) is not None
        }
        if not values:
            continue
        report.unmatched_rows.append(
            # `row` = the workbook row number, so the operator can find the
            # line in the mTool file (the UI printed "row undefined" while
            # this key was missing — peer-review Step 14).
            {"sheet": sheet, "row": row_num, "label": label_cell[1],
             "values": values}
        )


def _scale_backstop(facts: list[GoldFact], unit_scale: float) -> Optional[str]:
    """A magnitude sanity check on the DECLARED unit — not authority.

    The user's declared unit already scaled every value. This only warns when
    the resulting magnitudes look implausible for a financial statement, which
    usually means the unit was declared wrong (the silent 1000× trap)."""
    magnitudes = [abs(f.value) for f in facts if f.value]
    if len(magnitudes) < 3:
        return None
    peak = max(magnitudes)
    median = statistics.median(magnitudes)
    if peak < 1000:
        return (
            f"All figures are small (largest ≈ {peak:,.0f}). If this file is "
            "actually stated in thousands, re-ingest with the 'thousands' unit."
        )
    if median > 1e12:
        return (
            f"Figures look very large (median ≈ {median:,.0f}). Check the "
            "declared unit — you may have applied a thousands multiplier twice."
        )
    return None
