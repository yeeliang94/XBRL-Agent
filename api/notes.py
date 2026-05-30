"""Notes-cell + edited-count routes.

Endpoints:
  ``GET   /api/runs/{run_id}/notes_cells``                 — cells grouped by sheet
  ``PATCH /api/runs/{run_id}/notes_cells/{sheet}/{row}``   — edit one cell's HTML
  ``GET   /api/runs/{run_id}/notes_cells/edited_count``    — post-run notes edits
  ``GET   /api/runs/{run_id}/facts/edited_count``          — post-run fact edits

Step 8 (docs/Archive/PLAN-NOTES-RICH-EDITOR.md): the post-run editor reads rich
HTML payloads per cell via GET (grouped by sheet) and saves edits via PATCH. The
wire contract is asserted in tests/test_server_notes_cells_api.py — every endpoint
goes through ``server._open_audit_conn`` so the same DB/WAL pragmas apply.
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

import server

logger = logging.getLogger("server")

router = APIRouter()


@router.get("/api/runs/{run_id}/notes_cells")
async def list_notes_cells_endpoint(run_id: int):
    """Return every notes cell for ``run_id`` grouped by sheet.

    Shape:
        {
            "sheets": [
                {"sheet": "Notes-CI", "rows": [
                    {"row": 4, "label": ..., "html": ..., "evidence": ...,
                     "source_pages": [...], "updated_at": "..."},
                ]},
                ...
            ]
        }

    404 if the run does not exist (distinguishable from "run exists but
    has no notes yet" — the latter returns an empty sheets array).
    """
    from db import repository as repo
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        cells = repo.list_notes_cells_for_run(conn, run_id)
    finally:
        conn.close()

    # Group by sheet while preserving the (sheet, row) order from
    # list_notes_cells_for_run. A small dict-ordered walk is cheaper than
    # itertools.groupby for the expected payload size (< ~200 cells/run).
    sheets: dict[str, list[dict]] = {}
    for cell in cells:
        sheets.setdefault(cell.sheet, []).append({
            "row": cell.row,
            "label": cell.label,
            "html": cell.html,
            "evidence": cell.evidence,
            "source_pages": cell.source_pages,
            "updated_at": cell.updated_at,
        })
    return {
        "sheets": [
            {"sheet": sheet, "rows": rows}
            for sheet, rows in sheets.items()
        ],
    }


class _NotesCellPatch(BaseModel):
    """PATCH body — only ``html`` is editable.

    ``evidence`` and ``source_pages`` are deliberately omitted: the
    editor treats them as read-only audit data. `extra="forbid"`
    returns a 422 if a caller sends an unknown field — catches
    client-side typos like ``htmll`` early, and makes any future
    attempt to sneak an ``evidence`` override explicit instead of
    silently dropped.
    """
    model_config = ConfigDict(extra="forbid")

    html: str


@router.patch("/api/runs/{run_id}/notes_cells/{sheet}/{row}")
async def patch_notes_cell_endpoint(
    run_id: int, sheet: str, row: int, body: _NotesCellPatch,
):
    """Update one cell's HTML. Sanitises the payload and enforces the
    30k rendered-char cap server-side so the editor cannot bypass it.

    * 404 — no cell at (run_id, sheet, row).
    * 413 — sanitised HTML renders to more than 30 000 characters.
    * 200 — updated row returned in the same shape as GET list rows.

    **Concurrency note:** the SELECT-then-UPSERT here is not wrapped
    in a single transaction. Two concurrent PATCHes against the same
    cell from two browser tabs resolve as last-write-wins at commit
    time. This is intentionally left as the simple-single-user
    trade-off: the deployment target is a desktop tool for one
    accountant per machine (see CLAUDE.md), so cross-tab races are
    vanishingly rare and data loss is bounded to "the newer tab's
    edit wins, which is what the user would expect anyway".

    A parallel race exists between a live PATCH and the coordinator's
    ``persist_notes_cells`` during a regenerate: the regenerate
    clobbers, so any PATCH that raced with it silently loses. This
    is the documented semantics of regenerate (see CLAUDE.md gotcha
    #16) — not a bug.
    """
    from db import repository as repo
    from notes.html_sanitize import sanitize_notes_html
    from notes.html_to_text import rendered_length
    from notes.writer import CELL_CHAR_LIMIT

    # Pre-sanitise size guard (peer-review #4). Reject absurd-length
    # bodies before the sanitiser parses them — a megabyte of tags
    # would cost ~50ms of BeautifulSoup CPU per request and never
    # produce a valid cell. ~7x the rendered cap leaves plenty of
    # headroom for legitimate tag overhead on the 30k rendered limit
    # while cutting off the DOS avenue. Distinct detail string so
    # the pre-guard and post-cap rejections are distinguishable in
    # server logs.
    PRESANITIZE_HTML_CAP = 200_000
    if len(body.html) > PRESANITIZE_HTML_CAP:
        raise HTTPException(
            status_code=413,
            detail=(
                f"HTML too large (pre-sanitiser): {len(body.html):,} > "
                f"{PRESANITIZE_HTML_CAP:,} characters."
            ),
        )
    # Sanitise first so the cap is measured against the stored form.
    cleaned_html, warnings = sanitize_notes_html(body.html)
    if rendered_length(cleaned_html) > CELL_CHAR_LIMIT:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Rendered text exceeds the {CELL_CHAR_LIMIT:,} character "
                "limit. Shorten the cell before saving."
            ),
        )

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        # Peer-review I-3: SELECT+UPSERT must run inside a single write
        # transaction so a concurrent regenerate (which does
        # delete_notes_cells_for_run_sheet + re-INSERT) can't interleave
        # between our existence check and our write. BEGIN IMMEDIATE
        # upgrades the connection to a writer lock immediately; other
        # writers block (busy_timeout=5000ms) until this commit. Without
        # this wrap the PATCH can overwrite a freshly-regenerated row and
        # defeat the "regenerate clobbers" contract documented in CLAUDE.md
        # gotcha #16.
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Locate the existing row first so a PATCH against a non-existent
            # cell is a 404, not a silent insert. The editor only ever edits
            # cells it already listed via GET — phantom inserts would orphan
            # content from the template walk.
            existing = conn.execute(
                "SELECT id, label, evidence, source_pages FROM notes_cells "
                "WHERE run_id = ? AND sheet = ? AND row = ?",
                (run_id, sheet, row),
            ).fetchone()
            if existing is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Notes cell not found")

            # Round-trip source_pages so the upsert preserves them unchanged.
            # The column is JSON; list_notes_cells_for_run decodes it on read
            # but the upsert helper re-encodes from a Python list.
            from db.repository import decode_source_pages as _decode_pages
            pages = _decode_pages(existing["source_pages"])

            repo.upsert_notes_cell(
                conn,
                run_id=run_id,
                sheet=sheet,
                row=row,
                label=existing["label"],
                html=cleaned_html,
                evidence=existing["evidence"],
                source_pages=pages,
            )
            conn.commit()
        except HTTPException:
            # Already rolled back above — re-raise so FastAPI returns
            # the intended status/detail to the client.
            raise
        except Exception:
            conn.rollback()
            raise

        # Read back so the client sees the persisted updated_at.
        row_back = conn.execute(
            "SELECT label, html, evidence, source_pages, updated_at "
            "FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
            (run_id, sheet, row),
        ).fetchone()
    finally:
        conn.close()

    from db.repository import decode_source_pages
    return {
        "sheet": sheet,
        "row": row,
        "label": row_back["label"],
        "html": row_back["html"],
        "evidence": row_back["evidence"],
        "source_pages": decode_source_pages(row_back["source_pages"]),
        "updated_at": row_back["updated_at"] or "",
        # Peer-review #7: surface what the sanitiser removed so the
        # editor can tell the user "we dropped a <script> from your
        # paste" instead of silently swapping content. Empty list when
        # the sanitiser was a no-op — always present so clients can
        # treat it as a stable field.
        "sanitizer_warnings": warnings,
    }


@router.get("/api/runs/{run_id}/notes_cells/edited_count")
async def notes_cells_edited_count_endpoint(run_id: int):
    """Step 12 of docs/Archive/PLAN-NOTES-RICH-EDITOR.md — count how many
    ``notes_cells`` rows were touched *after* the run finished.

    The Regenerate-notes confirm dialog opens only when this returns
    ``count > 0``. Comparing ``updated_at > runs.ended_at`` is the
    cheap proxy for "user edited this cell post-run" — the writer
    never updates cells after the run's terminal event, so any later
    ``updated_at`` came from the PATCH endpoint.

    404 if the run does not exist. For runs that are still executing
    (``ended_at`` is NULL), we report 0 — there's nothing to lose
    because the agent is still the canonical source.
    """
    from db import repository as repo
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if not run.ended_at:
            return {"count": 0}
        row = conn.execute(
            "SELECT COUNT(*) FROM notes_cells "
            "WHERE run_id = ? AND updated_at > ?",
            (run_id, run.ended_at),
        ).fetchone()
    finally:
        conn.close()
    return {"count": int(row[0]) if row else 0}


@router.get("/api/runs/{run_id}/facts/edited_count")
async def facts_edited_count_endpoint(run_id: int):
    """Phase 2.3 — count face-statement values the user edited after the
    run finished (the face-statement analogue of notes_cells/edited_count).

    Mirrors the notes contract: a re-run / correction pass clobbers user
    edits, so the confirm dialog opens only when this returns ``count > 0``.
    A user edit is a ``run_concept_facts`` row stamped ``source='manual edit'``
    (set only by ``patch_fact_value``) whose ``updated_at`` is after the run's
    terminal event. Keying on ``source`` rather than ``value_status`` catches
    BOTH a typed override (``user_override``) and a cleared cell
    (``not_disclosed``) — keying on ``user_override`` alone silently missed
    clears. The extraction writer/cascade use other source tags, so this can't
    false-positive. Running runs (no ``ended_at``) report 0.
    """
    from db import repository as repo
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if not run.ended_at:
            return {"count": 0}
        row = conn.execute(
            "SELECT COUNT(*) FROM run_concept_facts "
            "WHERE run_id = ? AND source = 'manual edit' "
            "AND updated_at > ?",
            (run_id, run.ended_at),
        ).fetchone()
    finally:
        conn.close()
    return {"count": int(row[0]) if row else 0}
