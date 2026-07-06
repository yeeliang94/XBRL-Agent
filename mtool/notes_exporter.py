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

from mtool.notes_decorate import DEFAULT_STYLE, NotesTableStyle, decorate_notes_html


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
    for r in rows:
        html = (r["html"] or "").strip()
        label = (r["label"] or "").strip()
        if not html:
            skipped_empty += 1
            continue
        if not label:
            skipped_no_label += 1
            continue
        footnotes.append({
            "label": label,
            # Decorate the style-free DB HTML so mTool's TX27 editor renders
            # the formatting (borders/fills/font/alignment) — see the module
            # docstring. `decorate=False` keeps the raw HTML (tests / debug).
            "html": decorate_notes_html(r["html"], style) if decorate
            else r["html"],
            "source_sheet": r["sheet"],
            "source_row": r["row"],
        })

    meta = {
        "run_id": run_id,
        "counts": {
            "notes": len(footnotes),
            "skipped_empty": skipped_empty,
            "skipped_no_label": skipped_no_label,
        },
    }
    return {"meta": meta, "footnotes": footnotes, "strict": strict}
