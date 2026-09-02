"""Column-map detection for an uploaded mTool template — Step 10, finding 3.

**What was wrong.** The first version picked the column with the most text
cells and then assigned every value role **positionally** from there. Its
``confidence`` measured text density, so "high" only ever meant "the label
column is clearly the label column". Nothing checked that the column it called
``group_current_year`` actually *is* Group CY — which is the worst possible
failure, because it produces a plausible, wrong filing rather than an error.

It is not hypothetical. In the one real mTool template we hold
(``data/MBRS_test.xlsx``), sheet ``Notes-Issuedcapital`` lays its value columns
out as share CLASSES — Ordinary / Redeemable preference / Non-redeemable
preference / Total — all for the same period. Positional assignment would have
written the current year into "Ordinary shares" and the prior year into
"Redeemable preference shares", and nothing would have complained.

**What it does now.** A real mTool sheet labels its own structure with marker
rows, and we read them instead of guessing:

* ``#PRIM#``          — marks the label column (mTool's own designation)
* ``#ENDT#``          — each value column's period END date
* ``#STDTENDTDATE#``  — the human period string
* ``#UNITSCALE#``     — the unit the column is stated in (e.g. ``MYR'000``)
* ``#DOM#``           — the columns are DIMENSION members (share classes,
                        equity components), not periods

So current-year vs prior-year is decided by comparing dates, not by which
column comes first; a dimensional sheet is recognised and refused; and the
template's own declared unit is reported so a denomination mismatch is visible.

**When there are no markers** (our own generated templates, and any workbook
we've never seen) it falls back to the positional heuristic — but says so
(``basis: "positional"``) and demands operator confirmation unless the
workbook's fingerprint is one we have on file. Group layouts ALWAYS demand
confirmation: mTool's Group column shape has not been observed, so there is
nothing to corroborate a four-column guess against.

The caller must honour ``requires_confirmation``; ``confidence`` alone is not
the gate any more.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from mtool.offline_fill import (
    col_to_idx,
    get_shared_strings,
    get_sheet_paths,
    load_workbook_entries,
    read_sheet_cells,
)

# Canonical left-to-right order of value roles across a template row. Used only
# by the positional fallback — the semantic path derives order from dates.
_ROLE_ORDER = [
    "group_current_year",
    "group_prior_year",
    "current_year",
    "company_current_year",
    "prior_year",
    "company_prior_year",
]

# mTool's own structural markers, found in the marker column (observed: C).
MARKER_LABEL = "#PRIM#"
MARKER_END_DATE = "#ENDT#"
MARKER_PERIOD = "#STDTENDTDATE#"
MARKER_UNIT_SCALE = "#UNITSCALE#"
MARKER_DIMENSION = "#DOM#"

_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")

# Fingerprints of templates we have inspected and vouched for. Anything else is
# "unknown" and needs a human to confirm the layout (Step 18).
_KNOWN_TEMPLATES_PATH = Path(__file__).resolve().parent / "known_templates.json"


def _idx_to_col(idx: int) -> str:
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


# --------------------------------------------------------------- fingerprint

def _sheet_structure(cells: dict) -> dict[str, Any]:
    """Layout-only signature for one sheet: markers and which columns carry a
    header cell. Deliberately excludes CONTENT — an mTool export stamps a fresh
    GUID into row 1 of every sheet, so any content-sensitive fingerprint would
    make every export a different template."""
    markers: set[str] = set()
    header_cols: set[str] = set()
    for row, row_cells in cells.items():
        for col, (_kind, text) in row_cells.items():
            value = (text or "").strip()
            if value.startswith("#") and value.endswith("#"):
                markers.add(f"{value}@{col}")
            if row <= 3 and value:
                header_cols.add(col)
    return {"markers": sorted(markers), "header_cols": sorted(header_cols)}


def fingerprint_workbook(data: dict) -> str:
    """A stable structural id for an uploaded template.

    Same shape ⇒ same fingerprint, whether the workbook is empty or filled;
    a different sheet set, marker layout or column count ⇒ different
    fingerprint. That is what lets the endpoint tell "the SSM MFRS Company
    template we've validated against" apart from "something we've never seen".
    """
    sheet_paths = get_sheet_paths(data)
    sst = get_shared_strings(data)
    payload = {}
    for name in sorted(sheet_paths):
        try:
            cells = read_sheet_cells(data[sheet_paths[name]], sst)
        except Exception:  # noqa: BLE001 — a sheet we can't read still counts
            payload[name] = {"markers": [], "header_cols": []}
            continue
        payload[name] = _sheet_structure(cells)
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def load_known_templates() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(_KNOWN_TEMPLATES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def describe_template(fingerprint: str) -> dict[str, Any] | None:
    """The registry entry for a fingerprint, or ``None`` when unknown."""
    return load_known_templates().get(fingerprint)


# --------------------------------------------------------------- semantics

def _parse_date(text: str) -> tuple[int, int, int] | None:
    """``31/12/2024`` -> a sortable ``(y, m, d)``. Anything else -> None."""
    m = _DATE_RE.match((text or "").strip())
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    return (year, month, day)


def _marker_rows(cells: dict) -> dict[str, list[int]]:
    """``{marker token -> [row, ...]}`` for every mTool marker present."""
    found: dict[str, list[int]] = {}
    for row, row_cells in cells.items():
        for _col, (_kind, text) in row_cells.items():
            value = (text or "").strip()
            if value.startswith("#") and value.endswith("#"):
                found.setdefault(value, []).append(row)
    return {k: sorted(v) for k, v in found.items()}


def _label_column_from_marker(cells: dict, rows: list[int]) -> str | None:
    for row in rows:
        for col, (_kind, text) in cells.get(row, {}).items():
            if (text or "").strip() == MARKER_LABEL:
                return col
    return None


def _row_values(cells: dict, row: int) -> dict[str, str]:
    """Non-empty cells on a marker row, minus the marker itself."""
    out = {}
    for col, (_kind, text) in cells.get(row, {}).items():
        value = (text or "").strip()
        if value and not (value.startswith("#") and value.endswith("#")):
            out[col] = value
    return out


def parse_unit_scale(declared: str) -> str | None:
    """``MYR'000`` -> ``"thousands"``; ``MYR`` -> ``"units"``.

    Returned for reporting only. The template declaring ``MYR'000`` is
    EVIDENCE about what mTool expects, not licence to rescale — that decision
    belongs to the translation manifest and the Windows acceptance run.
    """
    text = (declared or "").strip().replace("’", "'")
    if not text:
        return None
    if text.endswith("'000000") or text.endswith("'000,000"):
        return "millions"
    if text.endswith("'000"):
        return "thousands"
    return "units"


def _semantic_layout(cells: dict, roles: list[str], *,
                     template_known: bool) -> dict[str, Any] | None:
    """Read the sheet's own marker rows. ``None`` when it has none.

    ``template_known`` gates unattended use exactly as it does on the
    positional path: markers make the reading *semantic*, but an unknown
    workbook's markers have never been corroborated against a real fill, so
    the proposal still needs a human (Step 18 applies to BOTH bases).
    """
    markers = _marker_rows(cells)
    if MARKER_LABEL not in markers:
        return None

    label_col = _label_column_from_marker(cells, markers[MARKER_LABEL])
    end_rows = markers.get(MARKER_END_DATE, [])
    period_rows = markers.get(MARKER_PERIOD, [])
    unit_rows = markers.get(MARKER_UNIT_SCALE, [])
    dimensional = bool(markers.get(MARKER_DIMENSION))

    # A sheet can carry several layout blocks (an intro block, then the data
    # block). The one that dates its columns is the data block.
    dates: dict[str, tuple[int, int, int]] = {}
    for row in end_rows:
        for col, text in _row_values(cells, row).items():
            parsed = _parse_date(text)
            if parsed:
                dates[col] = parsed
    periods = {}
    for row in period_rows:
        periods.update(_row_values(cells, row))
    unit_scales = {}
    for row in unit_rows:
        unit_scales.update(_row_values(cells, row))

    notes: list[str] = []
    columns: dict[str, str] = {}
    confidence = "high"
    requires_confirmation = False

    if dimensional:
        # Columns are taxonomy dimension members (share classes, equity
        # components), not periods. The semantic filing resolver selects the
        # member column from the write's dimensions and fails closed when it
        # cannot resolve one exact target. A CY/PY confirmation form cannot
        # make this safer because those roles do not describe these columns.
        notes.append(
            "this sheet's value columns are categories (for example share "
            "classes or equity components), not years — they are matched "
            "from the fact's taxonomy dimensions")

    # Distinct period end dates, newest first: newest = current year.
    ordered = sorted({d for d in dates.values()}, reverse=True)
    period_of_col = {col: ("current_year" if d == ordered[0]
                           else "prior_year" if len(ordered) > 1 and d == ordered[1]
                           else None)
                     for col, d in dates.items()} if ordered else {}

    wants_group = any(r.startswith("group_") or r.startswith("company_")
                      for r in roles)
    if wants_group and not dimensional:
        # mTool's Group column shape has never been observed, so there is
        # nothing here to corroborate a Group/Company split against. Never
        # auto-proceed on a four-column shape (Step 10).
        confidence = "low"
        requires_confirmation = True
        notes.append(
            "this is a group filing (separate group and company figures) and "
            "we cannot tell from the template which columns are which — "
            "please confirm them")

    if not dimensional and not wants_group and ordered:
        for role in roles:
            match = [c for c, p in period_of_col.items() if p == role]
            if len(match) == 1:
                columns[role] = match[0]
            elif len(match) > 1:
                confidence = "low"
                requires_confirmation = True
                notes.append(
                    f"more than one column covers the {role.replace('_', ' ')} "
                    f"period ({', '.join(sorted(match))})")
            else:
                confidence = "low"
                requires_confirmation = True
                notes.append(
                    f"no column in this template covers the "
                    f"{role.replace('_', ' ')} period")
        if columns:
            notes.append(
                "columns matched by the period dates in the template: "
                + ", ".join(f"{r}={c} ({periods.get(c, '?')})"
                            for r, c in columns.items()))

    if label_col is None:
        confidence = "low"
        requires_confirmation = True
        notes.append("could not find the label column marker")

    if not template_known and not dimensional:
        # An unknown fingerprint always needs a human, marker rows or not —
        # a semantic reading that has never been corroborated can still be a
        # confidently-wrong reading of markers we've never seen arranged this
        # way (peer review, 2026-08-05).
        requires_confirmation = True
        notes.append(
            "this template's layout is one we haven't seen before — the "
            "columns were read from its own markers, but please confirm them")

    # Every requested role is present in `columns`, blank when nothing could
    # be proposed. The confirm dialog renders an editable input per key, so a
    # role that is absent entirely is a role the operator CANNOT supply — a
    # confirmation request with nothing to confirm (peer review, 2026-08-05).
    for role in roles:
        columns.setdefault(role, "")

    return {
        "label_column": label_col,
        "columns": columns,
        "confidence": confidence,
        "requires_confirmation": requires_confirmation,
        "basis": "semantic",
        "dimensional": dimensional,
        "period_columns": {c: periods.get(c) for c in sorted(dates)},
        "declared_unit_scales": {c: parse_unit_scale(v)
                                 for c, v in sorted(unit_scales.items())},
        "notes": notes,
    }


# --------------------------------------------------------------- positional

def _pick_label_column(cells: dict) -> tuple[str | None, int, int]:
    """Return (column_letter, text_count, runner_up_count) for the column with
    the most text cells. Ties/scarcity are surfaced via the counts."""
    counts: dict[str, int] = {}
    for row_cells in cells.values():
        for col, (kind, _text) in row_cells.items():
            if kind == "S":
                counts[col] = counts.get(col, 0) + 1
    if not counts:
        return None, 0, 0
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    best_col, best_n = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    return best_col, best_n, runner_up


def _order_roles(roles) -> list[str]:
    known = [r for r in _ROLE_ORDER if r in roles]
    # Any unrecognised role keeps its incoming order after the known ones.
    unknown = [r for r in roles if r not in _ROLE_ORDER]
    return known + unknown


def _positional_layout(cells: dict, roles: list[str], *,
                       template_known: bool) -> dict[str, Any]:
    """The old density + position heuristic, now honestly labelled.

    Kept because our own generated templates carry no mTool markers, and the
    Step-3 dry runs fill them. Its guess is only allowed to stand unconfirmed
    when the workbook is one whose layout we have on file.
    """
    notes: list[str] = []
    label_col, text_n, runner_up = _pick_label_column(cells)
    ordered = _order_roles(roles)
    columns: dict[str, str] = {}
    if label_col is not None:
        start = col_to_idx(label_col) + 1
        for offset, role in enumerate(ordered):
            columns[role] = _idx_to_col(start + offset)

    confidence = "high"
    if label_col is None:
        confidence = "low"
        notes.append("no text column found to use as labels")
    elif text_n < 5:
        confidence = "low"
        notes.append(f"label column {label_col!r} has only {text_n} text cells")
    elif runner_up and text_n < runner_up * 2:
        confidence = "low"
        notes.append(f"label column {label_col!r} ({text_n} text cells) "
                     f"is not clearly ahead of the next ({runner_up})")

    wants_group = any(r.startswith("group_") or r.startswith("company_")
                      for r in roles)
    requires_confirmation = (not template_known) or wants_group
    if wants_group:
        confidence = "low"
        notes.append(
            "group filings have four figure columns and we are matching them "
            "by position only — please confirm which is which")
    elif not template_known:
        notes.append(
            "this template's layout is one we haven't seen before, so the "
            "columns below are a guess from their position — please check them")

    if columns:
        notes.append("value columns assigned positionally right of "
                     f"{label_col}: "
                     + ", ".join(f"{r}={c}" for r, c in columns.items()))

    # Same contract as the semantic path: every requested role appears, blank
    # when there is no proposal, so the confirm dialog always has a field to
    # edit.
    for role in ordered:
        columns.setdefault(role, "")

    return {
        "label_column": label_col,
        "columns": columns,
        "confidence": confidence,
        "requires_confirmation": requires_confirmation,
        "basis": "positional",
        "dimensional": False,
        "period_columns": {},
        "declared_unit_scales": {},
        "notes": notes,
    }


# --------------------------------------------------------------- public API

def detect_column_map(
    template_path: str,
    doc: dict[str, Any],
    *,
    data: dict | None = None,
    cells_by_sheet: dict[str, dict] | None = None,
) -> dict[str, dict[str, Any]]:
    """Propose a column map for every sheet in ``doc``.

    Returns ``{sheet: {"label_column", "columns": {role: col}, "confidence",
    "requires_confirmation", "basis", "dimensional", "period_columns",
    "declared_unit_scales", "notes"}}``. A sheet not present in the template
    gets ``label_column=None`` and a note.

    ``requires_confirmation`` is the real gate — a proposal may be acted on
    unattended only when every sheet says ``False``.

    ``data`` is an optional pre-loaded ``{entry_path: bytes}`` map (from
    :func:`load_workbook_entries`). ``cells_by_sheet`` may additionally reuse
    an existing worksheet index so detection does not parse the sheet XML
    again.
    """
    if data is None:
        _, data, _ = load_workbook_entries(template_path)
    sheet_paths = get_sheet_paths(data)
    sst = get_shared_strings(data)
    template_known = describe_template(fingerprint_workbook(data)) is not None

    out: dict[str, dict[str, Any]] = {}
    for sheet, cfg in doc.get("sheets", {}).items():
        entry = sheet_paths.get(sheet)
        if entry is None:
            out[sheet] = {
                "label_column": None, "columns": {}, "confidence": "low",
                "requires_confirmation": True, "basis": "missing",
                "dimensional": False, "period_columns": {},
                "declared_unit_scales": {},
                "notes": [f"sheet {sheet!r} not in template"]}
            continue
        cells = (
            cells_by_sheet[sheet]
            if cells_by_sheet is not None and sheet in cells_by_sheet
            else read_sheet_cells(data[entry], sst)
        )
        roles = _order_roles(list(cfg.get("columns", {})))
        layout = _semantic_layout(cells, roles,
                                  template_known=template_known)
        if layout is None:
            layout = _positional_layout(cells, roles,
                                        template_known=template_known)
        out[sheet] = layout
    return out


def overall_confidence(column_map: dict[str, dict[str, Any]]) -> str:
    """'high' only if every sheet detected at high confidence."""
    if not column_map:
        return "low"
    return "high" if all(
        s.get("confidence") == "high" for s in column_map.values()) else "low"


def needs_confirmation(column_map: dict[str, dict[str, Any]]) -> bool:
    """Whether a human must confirm period/entity columns before writing.

    Separate from ``overall_confidence`` on purpose: confidence is about how
    sure the detector is, this is about whether it is ALLOWED to proceed alone
    (unknown template or a Group period/entity layout). A dimensional layout
    does not itself set ``requires_confirmation`` because a period-column form
    cannot describe taxonomy members. Any other reason on that same sheet must
    still reach this gate.
    """
    if not column_map:
        return True
    return any(s.get("requires_confirmation", True)
               for s in column_map.values())


def unit_scale_mismatches(
    column_map: dict[str, dict[str, Any]],
    denomination: str | None,
) -> list[dict[str, str]]:
    """Sheets whose declared unit disagrees with the run's denomination.

    Reported, never acted on: the template saying ``MYR'000`` while the run
    holds units (or vice versa) is exactly the 1000× error finding 2 is about,
    and the operator should see it before filing.
    """
    want = (denomination or "").strip().lower() or None
    if want is None:
        return []
    out = []
    for sheet, layout in column_map.items():
        for col, declared in (layout.get("declared_unit_scales") or {}).items():
            if declared and declared != want:
                out.append({"sheet": sheet, "column": col,
                            "template_declares": declared,
                            "run_denomination": want})
    return out


__all__ = ["detect_column_map", "overall_confidence", "needs_confirmation",
           "fingerprint_workbook", "describe_template", "load_known_templates",
           "parse_unit_scale", "unit_scale_mismatches"]
