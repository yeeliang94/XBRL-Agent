"""N1 — notes↔face numeric tie-out checks (PLAN-orchestration-hardening).

Numeric rows on the notes sheets are written by notes agents and never
validated against the face statements' figures. A transcription error there
(a wrong scale, a transposed digit) survives every existing check. This adds a
conservative, WARN-only reconciliation: a hand-curated table of unambiguous
notes-row ↔ face-row pairs, compared by label (never by reference-file cell
coords — gotcha #4), flagging a mismatch as advisory.

Conservatism (gotcha #14): the pairing table is hand-curated and small. We
never fabricate a pairing the disclosure doesn't clearly establish, and an
unmatched topic produces no warning. This is POST-HOC validation only — no
deterministic matching enters the notes WRITING path.

Note on scope vs the plan: the MFRS numeric notes templates are *Issued
Capital* (sheet 13) and *Related Party* (sheet 14), not the employee-benefits /
income-tax examples the plan sketched. Related-party transactions have no clean
single face tie-out, so the seed table starts with the one unambiguous pair —
issued/share capital — and is structured so more pairs drop in as the numeric
notes set grows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cross_checks.util import open_workbook, find_sheet, find_value_by_label


def _cy_columns(filing_level: str) -> list[tuple[int, str]]:
    """(CY column index, scope label) pairs (mirrors verifier._cy_columns):
    Company → B only; Group → Group CY (B) + Company CY (D)."""
    if filing_level == "group":
        return [(2, "Group"), (4, "Company")]
    return [(2, "")]


@dataclass(frozen=True)
class TieoutPair:
    """One conservative notes-row ↔ face-row reconciliation."""
    topic: str
    face_sheets: tuple[str, ...]
    face_labels: tuple[str, ...]
    notes_sheets: tuple[str, ...]
    notes_labels: tuple[str, ...]
    applies_to_standard: str = "all"  # "all" | "mfrs" | "mpers"


# Seed table — only unambiguous pairs. The face share-capital line must equal
# the issued-capital note's closing issued-capital AMOUNT.
#
# Notes-side label choice (verified against live templates 2026-06-13): the
# sheet's "Issued capital" row is an ABSTRACT section header (1F3864 fill,
# gotcha #17) with an empty value cell on both MFRS (row 5) and MPERS
# (rows 5-6), so anchoring on it made notes_val None on every real run and
# the check silently no-op'd. The closing amount lives at the leaf
# "Balance at the end of period" inside the "Amount of shares issued and
# fully paid" block — that label is UNIQUE on the issued-capital sheet in
# all four live templates (MFRS/MPERS × Company/Group; the shares-COUNT
# block uses "Number of shares ..." labels instead), so a plain
# find_value_by_label lookup is unambiguous. Pinned by the live-template
# test in tests/test_notes_face_tieouts.py.
_PAIRS: list[TieoutPair] = [
    TieoutPair(
        topic="Share capital",
        face_sheets=("SOFP-CuNonCu", "SOFP-OrderOfLiquidity"),
        face_labels=("share capital", "issued capital"),
        notes_sheets=("Notes-Issuedcapital", "Notes-IssuedCapital"),
        notes_labels=("balance at the end of period",),
        applies_to_standard="all",
    ),
]


@dataclass
class TieoutWarning:
    """One notes↔face numeric mismatch (always WARN — never blocks)."""
    status: str  # always "warning"
    topic: str
    face_value: float
    notes_value: float
    message: str


def _tolerance(*magnitudes: float) -> float:
    """Magnitude-scaled tolerance: ~1 unit for ordinary statements, growing with
    size, mirroring the verifier's balance tolerance so RM'000 rounding doesn't
    manufacture a warning while a real (e.g. 1000×) drift still trips."""
    scale = max((abs(m) for m in magnitudes), default=0.0)
    return max(1.0, 5e-3 * scale)


def check_notes_face_tieouts(
    workbook_path: str,
    filing_level: str = "company",
    filing_standard: str = "mfrs",
) -> list[TieoutWarning]:
    """Reconcile curated notes figures against their face counterparts.

    Compares the CY value (col B — Group CY on group filings, Company CY on
    company filings) of each paired row. A pair where both sides are present and
    differ beyond a magnitude-scaled tolerance yields one WARN. Advisory only —
    never raises (a read error returns []).
    """
    if not Path(workbook_path).exists():
        return []
    try:
        wb = open_workbook(workbook_path)
    except Exception:  # noqa: BLE001 — advisory, never raise
        return []
    try:
        warnings: list[TieoutWarning] = []
        for pair in _PAIRS:
            if pair.applies_to_standard not in ("all", filing_standard):
                continue
            face_ws = find_sheet(wb, *pair.face_sheets)
            notes_ws = find_sheet(wb, *pair.notes_sheets)
            if face_ws is None or notes_ws is None:
                continue  # a sheet this run didn't produce — nothing to compare
            # CY column per entity scope (gotcha #12): Company filings have only
            # B (CY); Group filings carry Group CY in B AND Company CY in D, and
            # numeric notes (sheets 13/14) fill both — so reconcile each scope's
            # column, not just B (peer-review MEDIUM).
            for cy_col, scope in _cy_columns(filing_level):
                face_val = find_value_by_label(
                    face_ws, list(pair.face_labels), col=cy_col, wb=wb)
                notes_val = find_value_by_label(
                    notes_ws, list(pair.notes_labels), col=cy_col, wb=wb)
                if face_val is None or notes_val is None:
                    continue  # one side blank → no reconciliation to make
                if abs(face_val - notes_val) > _tolerance(face_val, notes_val):
                    scope_tag = f"{scope} " if scope else ""
                    warnings.append(TieoutWarning(
                        status="warning",
                        topic=pair.topic,
                        face_value=face_val,
                        notes_value=notes_val,
                        message=(
                            f"{scope_tag}{pair.topic}: face statement shows "
                            f"{face_val:,.0f} but the note shows {notes_val:,.0f}. "
                            f"These should reconcile — check the note for a wrong "
                            f"scale or transcription error (a ~1000× gap is a "
                            f"thousands/units mix-up)."
                        ),
                    ))
        return warnings
    finally:
        wb.close()
