"""Say what a download or an mTool fill will lose — plan Phase 9, Step 9.1.

Both exits from this system quietly degrade. The size ladder in
`mtool/notes_exporter.py` already trades formatting for size honestly and
counts what it dropped, but it does that DURING the fill, at which point the
operator is committed. And neither exit says anything about a note whose
source is only half accounted for — the workbook downloads exactly the same
either way.

So this module answers, before either action: what is not right about this
run's notes, and what will the export do about it?

It CHANGES nothing. `mtool/notes_decorate.py` and `web/src/lib/clipboard.ts`
are untouched — Step 9.1 says not to move them unless required, and they move
in lock-step when they do (gotcha #16).
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("server")

# The tier that means the note files with its formatting gone.
_LOSSY_TIERS = {"flat"}


@dataclass
class PreflightItem:
    kind: str            # coverage | formatting | content
    severity: str        # blocking | advisory
    sheet: Optional[str]
    row: Optional[int]
    label: str
    message: str


@dataclass
class Preflight:
    run_id: int
    items: list[PreflightItem] = field(default_factory=list)
    coverage_checked: bool = False

    @property
    def blocking(self) -> list[PreflightItem]:
        return [i for i in self.items if i.severity == "blocking"]

    @property
    def clean(self) -> bool:
        """Nothing to report AND nothing that failed to be assessed."""
        return not self.items

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "coverage_checked": self.coverage_checked,
            "clean": self.clean,
            "blocking_count": len(self.blocking),
            "advisory_count": len(self.items) - len(self.blocking),
            "items": [
                {
                    "kind": i.kind, "severity": i.severity, "sheet": i.sheet,
                    "row": i.row, "label": i.label, "message": i.message,
                }
                for i in self.items
            ],
        }


def _coverage_items(conn: sqlite3.Connection, run_id: int) -> tuple[list, bool]:
    """Notes whose source is not fully accounted for.

    Blocking, because exporting a note that is missing part of its source is
    the failure the whole feature exists to catch — and the workbook gives no
    sign of it.
    """
    from notes import source_repository as srepo
    from notes.source_models import Disposition, is_resolved

    gen = srepo.active_generation(conn, run_id)
    if gen is None:
        return [], False

    usages = {u["block_id"]: u for u in srepo.fetch_usages(conn, gen["id"])}
    notes = {
        n["source_note_id"]: n for n in srepo.fetch_notes(conn, gen["id"])
    }
    open_per_note: dict[str, int] = {}
    for b in srepo.fetch_blocks(conn, gen["id"]):
        note_id = b["source_note_id"]
        if not note_id:
            continue
        u = usages.get(b["block_id"])
        try:
            resolved = (
                u is not None
                and is_resolved(Disposition(u["disposition"]), u["reason_code"])
            )
        except ValueError:
            resolved = False
        if not resolved:
            open_per_note[note_id] = open_per_note.get(note_id, 0) + 1

    items = []
    for note_id, count in sorted(open_per_note.items()):
        n = notes.get(note_id)
        num = n["top_note_num"] if n else note_id
        items.append(PreflightItem(
            kind="coverage", severity="blocking", sheet=None, row=None,
            label=f"Note {num}",
            message=(
                f"{count} part(s) of note {num} in the source document are not "
                "accounted for. The export will not show this."
            ),
        ))
    return items, True


def _size_items(db_path, run_id: int) -> list[PreflightItem]:
    """What the mTool size ladder will drop, reported BEFORE the fill.

    Content loss is blocking; formatting loss is advisory — the note still
    files, it just files plain.
    """
    from mtool.notes_exporter import NotesTableStyle, build_notes_fill_doc
    from mtool.offline_fill import EXCEL_CELL_CHAR_LIMIT, wrap_footnote_html
    from notes.table_theme import firm_theme

    try:
        # Same theme resolution the fill itself uses, so the preflight measures
        # the payload the operator will actually send. Resolving through
        # `firm_theme()` rather than re-reading the env var is the rule a new
        # consumer must follow (gotcha #16).
        doc = build_notes_fill_doc(
            db_path, run_id, style=NotesTableStyle.from_theme(firm_theme()),
        )
    except Exception as exc:  # noqa: BLE001 — never block the export
        # Failure to assess is NOT proof of no loss. This used to return an
        # empty list, which made the whole preflight report `clean: true`
        # (peer review, 2026-08-01). Say what could not be checked.
        logger.warning(
            "export preflight: size assessment failed for run %s", run_id,
            exc_info=True,
        )
        return [PreflightItem(
            kind="unavailable", severity="advisory", sheet=None, row=None,
            label="Size check",
            message=(
                "Could not work out what the mTool fill would drop for this "
                f"run ({type(exc).__name__}). That is not the same as nothing "
                "being dropped — check the fill report after filing."
            ),
        )]

    items = []
    for fn in doc.get("footnotes", []):
        # The exporter annotates only the size-FORCED tiers, so `oversize` —
        # the one that loses content — carries no marker. Measure it the same
        # way the fill guard does rather than changing the entry shape a dozen
        # pinning tests depend on.
        tier = fn.get("format_tier")
        try:
            too_big = (
                len(wrap_footnote_html(fn.get("html") or ""))
                > EXCEL_CELL_CHAR_LIMIT
            )
        except Exception:  # noqa: BLE001
            too_big = False
        label = fn.get("label") or f"{fn.get('source_sheet')} row {fn.get('source_row')}"
        sheet, row = fn.get("source_sheet"), fn.get("source_row")
        if too_big:
            items.append(PreflightItem(
                kind="content", severity="blocking", sheet=sheet, row=row,
                label=label,
                message=(
                    "This note is too long for one cell even with all "
                    "formatting removed. Split its content before filing — "
                    "nothing is cut short automatically."
                ),
            ))
        elif tier in _LOSSY_TIERS:
            items.append(PreflightItem(
                kind="formatting", severity="advisory", sheet=sheet, row=row,
                label=label,
                message=(
                    "This note files without its formatting so the content "
                    "fits. The text is complete."
                ),
            ))
        elif fn.get("source_styling_dropped"):
            items.append(PreflightItem(
                kind="formatting", severity="advisory", sheet=sheet, row=row,
                label=label,
                message=(
                    "The Word document's own formatting was too large to keep, "
                    "so this note files with the house style instead."
                ),
            ))
        elif fn.get("white_grid_dropped"):
            items.append(PreflightItem(
                kind="formatting", severity="advisory", sheet=sheet, row=row,
                label=label,
                message=(
                    "mTool will draw its default grid on this note's unlined "
                    "edges — the lines that hide them did not fit."
                ),
            ))
    return items


def run_preflight(
    conn: sqlite3.Connection, db_path, run_id: int, *, include_size: bool = True
) -> Preflight:
    """Everything a person should know before downloading or filing."""
    coverage, checked = _coverage_items(conn, run_id)
    out = Preflight(run_id=run_id, items=list(coverage), coverage_checked=checked)
    if include_size:
        out.items.extend(_size_items(db_path, run_id))
    return out
