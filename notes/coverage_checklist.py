"""Holistic notes coverage checklist — pure builder, no I/O.

Phase 3 of docs/PLAN-notes-coverage-and-routing.md (PRD:
docs/PRD-notes-coverage-and-routing.md). Reconciles the scout's notes
inventory against WHERE content actually landed across ALL notes sheets, so
a note absent from the List of Notes sheet still counts as placed when it
lives on the Accounting Policies sheet (or Corporate Info / the numeric
sheets).

gotcha-#14-safe by construction: reconciliation keys on integer note
numbers and sub-reference STRINGS from the writer's ``source_note_refs``
provenance — never on matching a note's content to a row. Content-level
judgement (is sub-section (b) really inside the placed cell?) belongs to
the notes reviewer agent, which consumes this draft checklist and upgrades
``not_verified`` sub-refs to verified/missing verdicts (Phase 5).

Inputs mirror the durable stores:
- ``inventory_rows`` — ``db.repository.fetch_notes_inventory`` shape:
  ``{"note_num", "title", "subnote_refs", "page_lo", "page_hi"}``.
- ``provenance_entries`` — the detector ``entries`` shape
  (``notes.detectors.load_provenance_entries``):
  ``{"sheet", "row", "row_label", "source_note_refs", ...}``.
- ``skip_receipts`` — Sheet-12 coverage-receipt skips:
  ``{"note_num", "reason"}``.

The builder is deliberately a pure function so it can be recomputed at any
point (draft before the reviewer pass, final after) from the same durable
inputs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Private-helper reuse is deliberate (same package): the checklist MUST
# agree with the detectors on what "cites note N / sub-ref X" means, or the
# two layers drift (the same reason notes/coverage.py delegates to
# notes.labels.normalize_label).
from notes.detectors import _subnote_key, _top_note_nums

POLICIES_SHEET_DEFAULT = "Notes-SummaryofAccPol"

# Top-level row statuses.
STATUS_PLACED = "placed"
STATUS_MISSING = "missing"
STATUS_SKIPPED = "skipped"
STATUS_SUSPECTED_GAP = "suspected_gap"

# Sub-ref states the BUILDER can prove. The reviewer upgrades
# ``not_verified`` to ``verified`` / ``missing`` (Phase 5 verdicts) via the
# ``subnote_verdicts`` merge below.
SUBNOTE_CITED = "cited"
SUBNOTE_NOT_VERIFIED = "not_verified"
SUBNOTE_VERIFIED = "verified"
SUBNOTE_MISSING = "missing"

# Reviewer verdicts on a top-level row (Phase 5 `resolve_coverage_note`).
# Both RESOLVE a non-placed row so it no longer tips run status: a suspected
# gap the reviewer confirmed is a PDF numbering skip, or a missing note the
# reviewer judged genuinely not applicable to this entity.
VERDICT_CONFIRMED_ABSENT = "confirmed_absent"
VERDICT_NOT_APPLICABLE = "not_applicable"
RESOLVED_VERDICTS = frozenset({VERDICT_CONFIRMED_ABSENT, VERDICT_NOT_APPLICABLE})


def row_is_unresolved(status: str, reviewer_verdict, subnote_states) -> bool:
    """The single tip-to-``completed_with_errors`` rule (PRD Decision 3), shared
    by :meth:`CoverageRow.is_unresolved` and the coverage API's row check so the
    two can't drift.

    A ``missing`` / ``suspected_gap`` top-level row is unresolved unless the
    reviewer recorded a resolving verdict; a sub-ref confirmed ``missing`` is
    also unresolved. ``not_verified`` sub-refs warn only — never tip."""
    if status in (STATUS_MISSING, STATUS_SUSPECTED_GAP):
        if (reviewer_verdict or "") not in RESOLVED_VERDICTS:
            return True
    return any(s == SUBNOTE_MISSING for s in subnote_states)

# Placement kinds. ``fan_out`` = the policies note spreading per topic
# across the policies sheet (Direction 2); ``carve_out`` = a labelled
# policy sub-section extracted from a topical note onto the policies sheet
# (Direction 1). Only these two ever render as always-visible child rows.
KIND_PRIMARY = "primary"
KIND_FAN_OUT = "fan_out"
KIND_CARVE_OUT = "carve_out"


@dataclass
class Placement:
    sheet: str
    row: int
    row_label: str = ""
    kind: str = KIND_PRIMARY

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet, "row": self.row,
            "row_label": self.row_label, "kind": self.kind,
        }


@dataclass
class SubNoteState:
    subnote_ref: str
    state: str  # cited | not_verified (builder) | verified | missing (reviewer)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"subnote_ref": self.subnote_ref, "state": self.state}
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class CoverageRow:
    note_num: int
    title: str
    status: str
    placements: list[Placement] = field(default_factory=list)
    subnotes: list[SubNoteState] = field(default_factory=list)
    reason: str = ""
    page_lo: Optional[int] = None
    page_hi: Optional[int] = None
    # Reviewer overlay (Phase 5). ``reviewer_verdict`` resolves a non-placed
    # row (see RESOLVED_VERDICTS); ``reviewer_added`` marks a row the reviewer
    # authored into place (audit marker on the UI).
    reviewer_verdict: str = ""
    reviewer_added: bool = False

    def is_unresolved(self) -> bool:
        """A row that should tip the run to ``completed_with_errors``.

        A ``missing`` / ``suspected_gap`` top-level row is unresolved unless
        the reviewer recorded a resolving verdict. A sub-ref the reviewer
        confirmed ``missing`` (and could not author back) is also unresolved.
        ``not_verified`` sub-refs warn only — they never tip (PRD Decision 5).
        """
        return row_is_unresolved(
            self.status, self.reviewer_verdict,
            [s.state for s in self.subnotes],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "note_num": self.note_num,
            "title": self.title,
            "status": self.status,
            "placements": [p.to_dict() for p in self.placements],
            "subnotes": [s.to_dict() for s in self.subnotes],
            "reason": self.reason,
            "page_lo": self.page_lo,
            "page_hi": self.page_hi,
            "reviewer_verdict": self.reviewer_verdict,
            "reviewer_added": self.reviewer_added,
        }


@dataclass
class Checklist:
    """The draft checklist plus its trust marker.

    ``inventory_available`` is the loud-emptiness contract (PRD success
    criterion 2): an empty/failed inventory yields ``False`` and NO rows —
    callers must surface that as a warning banner, never render it as a
    clean all-green list.
    """

    rows: list[CoverageRow] = field(default_factory=list)
    inventory_available: bool = True

    def counts(self) -> dict[str, int]:
        out = {
            STATUS_PLACED: 0, STATUS_MISSING: 0,
            STATUS_SKIPPED: 0, STATUS_SUSPECTED_GAP: 0,
        }
        for r in self.rows:
            if r.status in out:
                out[r.status] += 1
        return out

    def unresolved_rows(self) -> list[CoverageRow]:
        """Rows that still tip the run to ``completed_with_errors`` (PRD
        Decision 3). Empty when the reviewer resolved every non-placed row."""
        return [r for r in self.rows if r.is_unresolved()]

    def has_unresolved(self) -> bool:
        return any(r.is_unresolved() for r in self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inventory_available": self.inventory_available,
            "rows": [r.to_dict() for r in self.rows],
            "counts": self.counts(),
            "unresolved": len(self.unresolved_rows()),
        }


def load_notes12_skips(output_dir: Optional[str]) -> list[dict]:
    """Read the Sheet-12 skip-receipt side-log (``notes12_skips.json``) the notes
    coordinator wrote at fan-out time: ``[{"note_num", "reason"}]``. Best-effort
    — a missing/unreadable file means "no skips" (an unplaced inventory note then
    defaults to ``missing``). Shared by the server's coverage finalizer and the
    reviewer's context so both agree on which notes were intentionally skipped."""
    if not output_dir:
        return []
    try:
        path = Path(output_dir) / "notes12_skips.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _suspected_gaps(present: list[int]) -> list[int]:
    """Internal holes in the inventory's note-number sequence.

    Notes in Malaysian filings number ~contiguously, so 1..12 + 14..24
    strongly suggests the scout missed Note 13. Only INTERNAL holes are
    flagged (between min and max observed) — numbering conventions before
    the first or after the last observed note are unknowable from the
    sequence alone (a truly missed final note is the builder's documented
    blind spot; the reviewer's PDF hunt is the deeper check).
    """
    if len(present) < 2:
        return []
    lo, hi = min(present), max(present)
    have = set(present)
    return [n for n in range(lo + 1, hi) if n not in have]


def build_draft_checklist(
    inventory_rows: list[dict],
    provenance_entries: list[dict],
    skip_receipts: Optional[list[dict]] = None,
    policies_sheet: str = POLICIES_SHEET_DEFAULT,
    note_verdicts: Optional[dict[int, dict]] = None,
    subnote_verdicts: Optional[dict[tuple[int, str], dict]] = None,
    reviewer_added_notes: Optional[set[int]] = None,
) -> Checklist:
    """Reconcile inventory × placements into the checklist.

    Called twice per run: once for the DRAFT (reviewer input, no verdicts) and
    again for the FINAL (post-reviewer) state. On the final pass the reviewer's
    accumulated verdicts are merged in:

    - ``note_verdicts`` — ``{note_num: {"verdict", "reason"}}`` from
      ``resolve_coverage_note``; a resolving verdict clears the run-status tip
      on a missing / suspected-gap row.
    - ``subnote_verdicts`` — ``{(note_num, subnote_key): {"verdict", "reason"}}``
      from ``verify_subnote``; upgrades a ``not_verified`` sub-ref to
      ``verified`` / ``missing``. Keys use the same ``_subnote_key`` coercion
      as the builder's citation reconciliation, so ``(a)`` and ``a`` collapse.
    - ``reviewer_added_notes`` — note numbers the reviewer authored back into
      place, tagged for the UI's "reviewer-added" audit marker.
    """
    if not inventory_rows:
        return Checklist(rows=[], inventory_available=False)

    note_verdicts = note_verdicts or {}
    subnote_verdicts = subnote_verdicts or {}
    reviewer_added_notes = reviewer_added_notes or set()

    # ---- placements + cited sub-refs per top-level note -----------------
    placements_by_note: dict[int, dict[tuple[str, int], str]] = {}
    cited_by_note: dict[int, set[str]] = {}
    for e in provenance_entries or []:
        sheet = e.get("sheet") or ""
        row = e.get("row")
        refs = e.get("source_note_refs") or []
        nums = _top_note_nums(refs)
        keys = {_subnote_key(r) for r in refs}
        for n in nums:
            cited_by_note.setdefault(n, set()).update(keys)
            if sheet and row is not None:
                placements_by_note.setdefault(n, {})[(sheet, int(row))] = (
                    e.get("row_label") or ""
                )

    skip_by_note: dict[int, str] = {}
    for s in skip_receipts or []:
        try:
            skip_by_note[int(s["note_num"])] = str(s.get("reason", "")).strip()
        except (KeyError, TypeError, ValueError):
            continue

    rows: list[CoverageRow] = []
    present_nums: list[int] = []
    for inv in inventory_rows:
        try:
            note_num = int(inv["note_num"])
        except (KeyError, TypeError, ValueError):
            continue
        present_nums.append(note_num)
        title = str(inv.get("title", "") or "")
        # De-duplicate the scout's sub-refs (order-preserving). A vision-batch
        # double-emit or overlapping regex can list the same ref twice; each
        # becomes a persisted child row, and the notes_coverage_rows UNIQUE index
        # (run_id, note_num, COALESCE(subnote_ref,'')) would then reject the whole
        # wholesale write — silently disabling coverage for a real run.
        subrefs = list(dict.fromkeys(inv.get("subnote_refs") or []))

        coords = placements_by_note.get(note_num, {})
        placements = _classify_placements(coords, policies_sheet)

        if placements:
            status, reason = STATUS_PLACED, ""
        elif note_num in skip_by_note:
            status, reason = STATUS_SKIPPED, skip_by_note[note_num]
        else:
            status, reason = STATUS_MISSING, ""

        cited = cited_by_note.get(note_num, set())
        subnotes = []
        for ref in subrefs:
            key = _subnote_key(ref)
            state = SUBNOTE_CITED if key in cited else SUBNOTE_NOT_VERIFIED
            sub_reason = ""
            verdict = subnote_verdicts.get((note_num, key))
            if verdict:
                v = str(verdict.get("verdict", "")).strip().lower()
                if v in (SUBNOTE_VERIFIED, SUBNOTE_MISSING):
                    state = v
                sub_reason = str(verdict.get("reason", "") or "").strip()
            subnotes.append(SubNoteState(subnote_ref=ref, state=state, reason=sub_reason))

        row = CoverageRow(
            note_num=note_num,
            title=title,
            status=status,
            placements=placements,
            subnotes=subnotes,
            reason=reason,
            page_lo=inv.get("page_lo"),
            page_hi=inv.get("page_hi"),
            reviewer_added=note_num in reviewer_added_notes,
        )
        _apply_note_verdict(row, note_verdicts.get(note_num))
        rows.append(row)

    for gap in _suspected_gaps(present_nums):
        before, after = gap - 1, gap + 1
        gap_row = CoverageRow(
            note_num=gap,
            title="",
            status=STATUS_SUSPECTED_GAP,
            reason=(
                f"Inventory numbering jumps {before} → {after}; the scout "
                f"may have missed note {gap}."
            ),
        )
        _apply_note_verdict(gap_row, note_verdicts.get(gap))
        rows.append(gap_row)

    rows.sort(key=lambda r: r.note_num)
    return Checklist(rows=rows, inventory_available=True)


def _apply_note_verdict(row: CoverageRow, verdict: Optional[dict]) -> None:
    """Overlay a ``resolve_coverage_note`` verdict onto a top-level row.

    A resolving verdict (``confirmed_absent`` / ``not_applicable``) is recorded
    on the row so ``is_unresolved`` stops tipping run status; the reviewer's
    reason replaces the placeholder text so the UI explains WHY the row is
    resolved. A ``placed`` row ignores verdicts (nothing to resolve)."""
    if not verdict:
        return
    v = str(verdict.get("verdict", "")).strip().lower()
    reason = str(verdict.get("reason", "") or "").strip()
    if v in RESOLVED_VERDICTS and row.status in (STATUS_MISSING, STATUS_SUSPECTED_GAP):
        row.reviewer_verdict = v
        if reason:
            row.reason = reason


def checklist_to_db_rows(checklist: Checklist) -> list[dict]:
    """Flatten a :class:`Checklist` into ``notes_coverage_rows`` DB shape.

    One row per top-level note (``subnote_ref`` None) followed by one child row
    per sub-ref state. Lossless — the API re-nests children under their parent
    and the UI decides always-visible vs expandable from the placement kinds
    (PRD Decision 5)."""
    out: list[dict] = []
    for r in checklist.rows:
        out.append({
            "note_num": r.note_num,
            "subnote_ref": None,
            "status": r.status,
            "reason": r.reason,
            "placements": [p.to_dict() for p in r.placements],
            "reviewer_added": r.reviewer_added,
            "reviewer_verdict": r.reviewer_verdict or None,
            "title": r.title,
            "page_lo": r.page_lo,
            "page_hi": r.page_hi,
        })
        # Final guard against the notes_coverage_rows UNIQUE index
        # (run_id, note_num, COALESCE(subnote_ref,'')): the builder already
        # de-dups exact sub-ref strings, but keep the flatten collision-proof so
        # a wholesale persist can never fail (and silently disable coverage) on a
        # stray duplicate from any future caller.
        seen_refs: set[str] = set()
        for s in r.subnotes:
            if s.subnote_ref in seen_refs:
                continue
            seen_refs.add(s.subnote_ref)
            out.append({
                "note_num": r.note_num,
                "subnote_ref": s.subnote_ref,
                "status": s.state,
                "reason": s.reason,
            })
    return out


def _classify_placements(
    coords: dict[tuple[str, int], str], policies_sheet: str,
) -> list[Placement]:
    """Assign each distinct (sheet, row) its placement kind.

    - On the policies sheet: ``carve_out`` when the note ALSO has
      placements elsewhere (Direction 1 — a labelled policy sub-section
      extracted from a topical note); ``fan_out`` when the note lives
      entirely on the policies sheet across ≥2 rows (Direction 2 — the
      policies note spreading per topic); ``primary`` for a lone
      policies-sheet placement.
    - Everywhere else: ``primary``.
    """
    on_policies = [c for c in coords if c[0] == policies_sheet]
    elsewhere = [c for c in coords if c[0] != policies_sheet]
    out: list[Placement] = []
    for (sheet, row), label in coords.items():
        if sheet == policies_sheet and elsewhere:
            kind = KIND_CARVE_OUT
        elif sheet == policies_sheet and len(on_policies) >= 2:
            kind = KIND_FAN_OUT
        else:
            kind = KIND_PRIMARY
        out.append(Placement(sheet=sheet, row=row, row_label=label, kind=kind))
    out.sort(key=lambda p: (p.sheet, p.row))
    return out
