"""Notes bridge — a run's ``notes_cells`` → mTool footnote fill instructions.

The prose-note twin of :mod:`mtool.exporter` (which turns ``run_concept_facts``
into numeric writes). This turns a completed run's canonical notes HTML into the
``footnotes`` document that :func:`mtool.offline_fill.fill_footnotes` consumes to
fill mTool prose text-blocks — the same one-patcher, no-fork discipline.

What this module owns:

* **Source = ``notes_cells`` only** — the canonical per-note HTML store
  (gotcha #16), never the flattened xlsx snapshot. Each row already carries the
  note ``label`` and sanitised ``html``.
* **Every prose note is a candidate.** notes_cells holds prose HTML for ALL
  notes sheets — including the "numeric" sheets 13/14, whose narrative
  disclosure (issued-capital classes, related-party transactions) is prose and
  belongs in an mTool text-block. The numbers on those sheets travel the
  separate numeric fill path; here we only ever emit HTML.
* **Label targeting, not physical cells.** Each write carries the note ``label``;
  ``fill_footnotes`` resolves it to the template's ``fn_*`` at fill time
  (decoration-tolerant fuzzy match), so this stays layout-neutral — mirroring
  how the numeric exporter defers column resolution.
* **Render decoration, not content transform.** The note's words/structure are
  unchanged, but the style-free DB HTML is run through
  :func:`mtool.notes_decorate.decorate_notes_html` — the backend port of the
  clipboard decorator — so mTool's TX27 text-block editor renders borders,
  fills, fonts and numeric alignment instead of flat text. This is the SAME
  styling the manual "Copy → paste into mTool" workflow has always applied;
  the automated path previously skipped it and lost all formatting. Wrapping in
  the mTool XHTML shell still happens in the filler, not here.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from mtool.notes_decorate import (
    DEFAULT_STYLE, NotesTableStyle, decorate_notes_html, strip_inline_styles,
)
from mtool.offline_fill import EXCEL_CELL_CHAR_LIMIT, wrap_footnote_html


def build_notes_fill_doc(
    db_path: str | Path,
    run_id: int,
    *,
    strict: bool = True,
    style: NotesTableStyle = DEFAULT_STYLE,
    decorate: bool = True,
) -> dict[str, Any]:
    """Build the ``footnotes`` fill document for a run's prose notes.

    Returns a :func:`mtool.offline_fill.fill_footnotes`-shaped doc::

        {
          "meta": {run_id, counts: {notes, skipped_empty, skipped_no_label}},
          "footnotes": [{label, html, source_sheet, source_row}, ...],
          "strict": bool,
        }

    A note with empty HTML or no label is counted and skipped (never emitted as
    a blank write). ``source_sheet`` / ``source_row`` are provenance only —
    ``fill_footnotes`` targets by ``label`` and ignores extra keys.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT sheet, row, label, html
            FROM notes_cells
            WHERE run_id = ?
            ORDER BY sheet, row
            """,
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    footnotes: list[dict[str, Any]] = []
    skipped_empty = 0
    skipped_no_label = 0
    formatting_compacted = 0  # "compact" tier — same look, slimmer styling
    formatting_reduced = 0    # "lite" tier — cosmetic props dropped
    formatting_dropped = 0    # "flat" tier — all styling dropped
    source_styling_dropped = 0  # destyle retry — verbatim Word styling stripped
    white_grid_dropped = 0    # run-76 white grid dropped for size (grey grid)
    for r in rows:
        html = (r["html"] or "").strip()
        label = (r["label"] or "").strip()
        if not html:
            skipped_empty += 1
            continue
        if not label:
            skipped_no_label += 1
            continue
        # Decorate the style-free DB HTML so mTool's TX27 editor renders the
        # formatting (borders/fills/font/alignment) — see the module docstring.
        # `decorate=False` keeps the raw HTML (the "no styling" diagnostic
        # toggle on the fill endpoint, plus tests / debug).
        out_html, tier, destyled, grid_dropped = _resolve_note_html(
            r["html"], style, decorate)
        if tier == "compact":
            formatting_compacted += 1
        elif tier == "lite":
            formatting_reduced += 1
        elif tier == "flat":
            formatting_dropped += 1
        entry: dict[str, Any] = {
            "label": label,
            "html": out_html,
            "source_sheet": r["sheet"],
            "source_row": r["row"],
        }
        # Record only the size-forced tiers (full/raw notes stay unannotated so
        # the common case is unchanged); back-compat: `formatting_dropped` bool
        # still marks the flat tier.
        if tier in ("compact", "lite", "flat"):
            entry["format_tier"] = tier
        if tier == "flat":
            entry["formatting_dropped"] = True
        # Destyle retry (verbatim passthrough): the note's own Word styling
        # was stripped to make it fit, then re-decorated with the house theme.
        # The tier alone would misreport this as "formatting intact", so the
        # loss gets its own honest annotation on the entry + a meta count.
        if destyled:
            source_styling_dropped += 1
            entry["source_styling_dropped"] = True
        # White-grid fallback (run 76): the per-cell white borders were dropped
        # for size, so TX shows its default grey grid on this note's undeclared
        # edges. Cosmetic — but the operator should hear it from the report,
        # not discover it in the popup.
        if grid_dropped:
            white_grid_dropped += 1
            entry["white_grid_dropped"] = True
        footnotes.append(entry)

    meta = {
        "run_id": run_id,
        # Honest labelling for the diagnostic no-styling fill: consumers (the
        # patch report, the modal) surface this so a deliberately-plain fill
        # can't be misread as a formatting bug.
        "styling_disabled": not decorate,
        "counts": {
            "notes": len(footnotes),
            "skipped_empty": skipped_empty,
            "skipped_no_label": skipped_no_label,
            # Deterministic size signals (full → compact → lite → flat ladder):
            #   formatting_compacted = same visible formatting, slimmer
            #     per-cell styling (table-level attrs carry the grid).
            #   formatting_reduced = kept borders/font/align, dropped cosmetics.
            #   formatting_dropped = written FLAT; the note's styling is too
            #     heavy and should be simplified. (A note too big even flat is
            #     not counted here — the fill guard skips it as `oversize`,
            #     meaning the CONTENT must be split, not the styling.)
            "formatting_compacted": formatting_compacted,
            "formatting_reduced": formatting_reduced,
            "formatting_dropped": formatting_dropped,
            # Verbatim-passthrough notes whose SOURCE (Word) styling had to be
            # stripped for size; they file with theme styling instead. Counted
            # separately from the tier counters because a destyled note can
            # re-land on any tier, including "full".
            "source_styling_dropped": source_styling_dropped,
            # Notes whose run-76 white-grid painting was dropped for size (TX
            # shows its default grey grid there). Independent of the tier —
            # a white-grid-dropped note can land on full/compact/lite.
            "white_grid_dropped": white_grid_dropped,
        },
    }
    return {"meta": meta, "footnotes": footnotes, "strict": strict}


def _resolve_note_html(
    raw: str, style: NotesTableStyle, decorate: bool,
) -> tuple[str, str, bool, bool]:
    """Pick the HTML to emit for one note, trading formatting for size only
    when forced. Returns ``(html, tier, source_styling_dropped,
    white_grid_dropped)`` where tier is one of ``full`` / ``compact`` /
    ``lite`` / ``flat`` / ``raw`` / ``oversize``. ``source_styling_dropped``
    is True only when the destyle retry below stripped the note's own
    (verbatim Word) styling to make it fit — the tier alone can't carry that:
    a destyled note re-lands on ``full``, which would otherwise read as
    "formatting fully intact" in the fill report while the operator's Word
    styling was silently replaced with the house theme. ``white_grid_dropped``
    is the same honesty for the cheaper loss: the run-76 white-grid painting
    was dropped for size, so TX shows its default grey grid on that note.

    Ladder — CONTENT is never lost to formatting:
      * ``full``    — decorated HTML fits Excel's cell limit.
      * ``compact`` — full is over, but the compact decoration (table-level
        attrs carry the grid/padding; per-cell styles only where cells differ
        — same visible formatting, ~1/3 the characters) fits. Roughly triples
        the fully-styled table ceiling (docs/PLAN-mtool-compact-decoration.md).
      * ``lite``    — compact is over too, but a lighter decoration (cosmetic
        props dropped, borders/font/alignment kept) fits.
      * *(white-grid fallback)* — the run-76 white grid
        (``fill_white_grid``, mtool/notes_decorate.py) costs ~27 chars per
        cell, which for a source-styled / border-none-theme table can be the
        difference between fitting and not. Before giving up styling entirely,
        the same full/compact/lite rungs are retried with the white grid OFF —
        exactly the pre-run-76 payload, so a note that used to fit a tier
        never regresses below it. The landed tier is reported with
        ``white_grid_dropped`` (TX will show its default grey grid on that
        note's undeclared edges — cosmetic, and honestly counted).
      * ``flat``    — even that is over, but the UNDECORATED HTML fits; the
        note renders plain but its content + the workbook stay intact.
      * *(destyle retry)* — when the note carries VERBATIM source styling
        (gotcha #16), ``raw`` itself can be over the limit, which strands every
        rung above: ``compact`` is inoperative (it slims decorator-added
        styling, and these cells own theirs) and ``flat`` == ``raw``. Stripping
        the inline styles — and the ``data-source-styled`` marker, whose
        "borders are the whole truth" premise died with them — then re-walking
        the ladder recovers a filable note. Measured on a 6-column Word table:
        100 rows went oversize → compact, 200 rows → flat. The reported tier
        is the one the retry landed on.
      * ``oversize`` — too big even flat AND after destyling: emit ``raw``
        (smallest, honest payload size) and let the fill's hard guard
        (:data:`mtool.offline_fill.EXCEL_CELL_CHAR_LIMIT`) skip + flag it — the
        signal that the CONTENT must be split, not the styling simplified.
      * ``raw``   — decoration disabled (the fill's "no styling" diagnostic
        toggle, plus tests / debug).
    Sizes use the exact wrapped payload (:func:`wrap_footnote_html`) so the
    wrap overhead + Excel's unescaped-length semantics are accounted for."""
    if not decorate:
        return raw, "raw", False, False

    def _fits(h: str) -> bool:
        return len(wrap_footnote_html(h)) <= EXCEL_CELL_CHAR_LIMIT

    def _walk(source: str) -> tuple[str, str, bool] | None:
        """One pass down the tiers for ``source``: full → compact → lite,
        then the same three with the white grid dropped, then flat. Returns
        ``(html, tier, white_grid_dropped)`` or None if nothing fits."""
        decorated = decorate_notes_html(source, style)
        if _fits(decorated):
            return decorated, "full", False
        compact = decorate_notes_html(source, style, compact=True)
        # Compact only helps when it actually differs from full — a compact-
        # INELIGIBLE note (user-styled cells / border-none theme) decorates
        # byte-identical to full, so it can't fit either; skip straight to lite
        # instead of re-testing the same over-limit payload under a "compact"
        # label.
        if compact != decorated and _fits(compact):
            return compact, "compact", False
        lite = decorate_notes_html(source, style, lite=True)
        if _fits(lite):
            return lite, "lite", False
        # White-grid fallback: retry the same tiers without the per-cell white
        # borders (only when they actually contributed — a bordered-theme note
        # decorates identically either way and skips straight to flat).
        nofill = decorate_notes_html(source, style, fill_white_grid=False)
        if nofill != decorated:
            if _fits(nofill):
                return nofill, "full", True
            nofill_compact = decorate_notes_html(
                source, style, compact=True, fill_white_grid=False)
            if nofill_compact != nofill and _fits(nofill_compact):
                return nofill_compact, "compact", True
            nofill_lite = decorate_notes_html(
                source, style, lite=True, fill_white_grid=False)
            if _fits(nofill_lite):
                return nofill_lite, "lite", True
        if _fits(source):
            return source, "flat", False
        return None

    landed = _walk(raw)
    if landed is not None:
        html, tier, white_dropped = landed
        return html, tier, False, white_dropped
    # Verbatim passthrough (gotcha #16, 2026-07-19) puts the SOURCE document's
    # own per-cell styling on `raw`, so for a big Word table `raw` is itself
    # over the limit and every rung above has already failed — including
    # `compact`, which is inoperative here because compaction only strips
    # DECORATOR-added styling and these cells own theirs. Measured on a 6-column
    # Word table: 50 rows -> lite, 100 rows -> nothing left.
    #
    # Stripping the source styling gives the ladder its rungs back: content is
    # preserved and the note files plain, instead of going `oversize` and being
    # skipped outright by the fill guard. Strictly better than the alternative —
    # a filed plain note beats a missing one.
    stripped = strip_inline_styles(raw)
    if stripped != raw:
        landed = _walk(stripped)
        if landed is not None:
            html, tier, white_dropped = landed
            return html, tier, True, white_dropped
    # Oversize emits `raw` untouched — nothing was actually dropped, the
    # note simply couldn't be made to fit; the fill guard skips + flags it.
    return raw, "oversize", False, False
