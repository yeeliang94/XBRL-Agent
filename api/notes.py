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
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

import server

logger = logging.getLogger("server")

router = APIRouter()


def _notes_template_index(standard: str, level: str) -> list[dict]:
    """Resolve the run's (standard, level) to its notes templates, in MBRS
    slot order. Each entry: template_type, sheet, is_numeric, template_id.

    Skips templates that don't resolve for the (standard, level) pair.
    """
    from notes_types import NOTES_REGISTRY, notes_template_path
    from concept_model.parser import _derive_template_id

    out: list[dict] = []
    for ttype, entry in NOTES_REGISTRY.items():
        try:
            path = notes_template_path(ttype, level=level, standard=standard)
        except ValueError:
            continue
        out.append({
            "template_type": ttype,
            "sheet": entry.sheet_name,
            "is_numeric": entry.is_numeric,
            "template_id": _derive_template_id(path),
        })
    return out


def _prose_sheet_rows(conn, run_id: int, template_id: str, sheet: str) -> list[dict]:
    """Full prose template for one sheet: every LEAF row in template order,
    with the run's filled `notes_cells` overlaid (blank where unfilled).

    A filled cell whose row isn't a registry LEAF (off-template / legacy) is
    still surfaced — appended by row — so no authored content is ever hidden.
    If the registry is empty (template not imported), this degrades to the
    legacy "filled rows only" view.
    """
    from db.repository import decode_source_pages

    by_row: dict[int, dict] = {}
    for n in conn.execute(
        "SELECT row, label, node_uuid, xbrl_concept_id FROM notes_nodes "
        "WHERE template_id = ? AND kind = 'LEAF' ORDER BY row",
        (template_id,),
    ).fetchall():
        by_row[n["row"]] = {
            "row": n["row"],
            "label": n["label"],
            "kind": "prose",
            "node_uuid": n["node_uuid"],
            "xbrl_concept_id": n["xbrl_concept_id"],
            "html": "",
            "evidence": None,
            "source_pages": [],
            "updated_at": "",
        }

    for c in conn.execute(
        "SELECT row, label, html, evidence, source_pages, updated_at "
        "FROM notes_cells WHERE run_id = ? AND sheet = ?",
        (run_id, sheet),
    ).fetchall():
        base = by_row.get(c["row"])
        if base is None:
            base = {
                "row": c["row"],
                "label": c["label"],
                "kind": "prose",
                "node_uuid": None,
                "xbrl_concept_id": None,
                "html": "",
                "evidence": None,
                "source_pages": [],
                "updated_at": "",
            }
            by_row[c["row"]] = base
        base["html"] = c["html"]
        base["evidence"] = c["evidence"]
        base["source_pages"] = decode_source_pages(c["source_pages"])
        base["updated_at"] = c["updated_at"] or ""

    return [by_row[r] for r in sorted(by_row)]


def _numeric_sheet_rows(
    conn, run_id: int, template_id: str, sheet: str, level: str
) -> list[dict]:
    """Full numeric template for one sheet: every LEAF concept row in template
    order, with the run's `run_concept_facts` values shaped per filing level
    (Company → cy/py; Group → group_cy/py + company_cy/py). Blank where the
    run has no fact for that cell.
    """
    nodes = conn.execute(
        "SELECT render_row AS row, canonical_label, display_label, concept_uuid "
        "FROM concept_nodes "
        "WHERE template_id = ? AND render_sheet = ? AND kind = 'LEAF' "
        "ORDER BY render_row",
        (template_id, sheet),
    ).fetchall()
    if not nodes:
        return []

    facts: dict[str, dict] = {}
    for f in conn.execute(
        "SELECT concept_uuid, period, entity_scope, value "
        "FROM run_concept_facts WHERE run_id = ?",
        (run_id,),
    ).fetchall():
        facts.setdefault(f["concept_uuid"], {}).setdefault(
            f["entity_scope"], {}
        )[f["period"]] = f["value"]

    rows: list[dict] = []
    for n in nodes:
        scope = facts.get(n["concept_uuid"], {})
        if level == "group":
            values = {
                "group_cy": scope.get("Group", {}).get("CY"),
                "group_py": scope.get("Group", {}).get("PY"),
                "company_cy": scope.get("Company", {}).get("CY"),
                "company_py": scope.get("Company", {}).get("PY"),
            }
        else:
            values = {
                "cy": scope.get("Company", {}).get("CY"),
                "py": scope.get("Company", {}).get("PY"),
            }
        rows.append({
            "row": n["row"],
            "label": n["display_label"] or n["canonical_label"],
            "kind": "numeric",
            "concept_uuid": n["concept_uuid"],
            "values": values,
            "updated_at": "",
        })
    return rows


@router.get("/api/runs/{run_id}/notes_cells")
async def list_notes_cells_endpoint(run_id: int):
    """Return the FULL notes template for ``run_id`` grouped by sheet.

    Each targeted notes sheet is projected in template (M-tool) order with
    every fillable row present — blanks included — so a reviewer can locate an
    extracted note relative to the whole template and copy it into the M-tool.
    Two row shapes (PLAN-notes-template-registry):

      * prose   — {row, label, kind:"prose", node_uuid, xbrl_concept_id,
                   html, evidence, source_pages, updated_at}
      * numeric — {row, label, kind:"numeric", concept_uuid, values, updated_at}

    A sheet is "targeted" when it's in the run's ``notes_to_run`` OR already
    carries data (prose cells / numeric facts) — so a run shows exactly the
    notes it asked for (or produced), never the whole catalogue.

    404 if the run does not exist; an empty ``sheets`` array means the run
    targeted no notes.
    """
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        # Row access by column name for the raw projection queries below.
        conn.row_factory = sqlite3.Row

        config = run.config or {}
        standard = config.get("filing_standard", "mfrs")
        level = config.get("filing_level", "company")

        # What the run explicitly asked for.
        from notes_types import NotesTemplateType
        requested: set = set()
        for v in (config.get("notes_to_run") or []):
            try:
                requested.add(NotesTemplateType(v))
            except ValueError:
                # Unknown value in a legacy/hand-rolled config — ignore.
                continue

        # Sheets that already carry prose data (covers legacy runs whose
        # config has no notes_to_run, and seeded test fixtures).
        prose_data_sheets = {
            r["sheet"]
            for r in conn.execute(
                "SELECT DISTINCT sheet FROM notes_cells WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        # Template_ids that already carry facts (covers numeric notes).
        fact_template_ids = {
            r["template_id"]
            for r in conn.execute(
                "SELECT DISTINCT n.template_id FROM run_concept_facts f "
                "JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
                "WHERE f.run_id = ?",
                (run_id,),
            ).fetchall()
        }

        sheets_out: list[dict] = []
        for entry in _notes_template_index(standard, level):
            ttype = entry["template_type"]
            targeted = ttype in requested
            if entry["is_numeric"]:
                targeted = targeted or entry["template_id"] in fact_template_ids
                if not targeted:
                    continue
                rows = _numeric_sheet_rows(
                    conn, run_id, entry["template_id"], entry["sheet"], level,
                )
            else:
                targeted = targeted or entry["sheet"] in prose_data_sheets
                if not targeted:
                    continue
                rows = _prose_sheet_rows(
                    conn, run_id, entry["template_id"], entry["sheet"],
                )
            if not rows:
                continue
            sheets_out.append({
                "sheet": entry["sheet"],
                "kind": "numeric" if entry["is_numeric"] else "prose",
                "rows": rows,
            })
        return {"sheets": sheets_out}
    finally:
        conn.close()


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
            # An edit can target either a row already in notes_cells (update)
            # or a blank registry row the GET projection surfaced (insert).
            # The editor only ever offers cells that came from the projection,
            # so an insert is restricted to rows that exist in notes_nodes —
            # a PATCH to an unknown row is a 400, never a phantom insert.
            existing = conn.execute(
                "SELECT id, label, evidence, source_pages FROM notes_cells "
                "WHERE run_id = ? AND sheet = ? AND row = ?",
                (run_id, sheet, row),
            ).fetchone()

            from db.repository import decode_source_pages as _decode_pages

            if existing is not None:
                # Update path — preserve the existing label/evidence/pages and
                # only swap the HTML (evidence stays read-only, gotcha #16).
                upsert_label = existing["label"]
                upsert_evidence = existing["evidence"]
                upsert_pages = _decode_pages(existing["source_pages"])
                upsert_concept_uuid = None  # keep current identity (decision §9.5)
            else:
                # Insert path — the row must be a fillable prose registry node.
                config = run.config or {}
                standard = config.get("filing_standard", "mfrs")
                level = config.get("filing_level", "company")
                template = next(
                    (
                        e for e in _notes_template_index(standard, level)
                        if e["sheet"] == sheet
                    ),
                    None,
                )
                if template is None:
                    conn.rollback()
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unknown notes sheet {sheet!r} for this run.",
                    )
                if template["is_numeric"]:
                    conn.rollback()
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Numeric notes are edited through the facts API, "
                            "not this endpoint."
                        ),
                    )
                node = conn.execute(
                    "SELECT label, node_uuid FROM notes_nodes "
                    "WHERE template_id = ? AND row = ? AND kind = 'LEAF'",
                    (template["template_id"], row),
                ).fetchone()
                if node is None:
                    conn.rollback()
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Row {row} is not a fillable row of sheet "
                            f"{sheet!r}."
                        ),
                    )
                # New write: stamp the template-scoped node_uuid as the cell's
                # concept_uuid so it links to the registry (decision §9.2).
                upsert_label = node["label"]
                upsert_evidence = None
                upsert_pages = []
                upsert_concept_uuid = node["node_uuid"]

            repo.upsert_notes_cell(
                conn,
                run_id=run_id,
                sheet=sheet,
                row=row,
                label=upsert_label,
                html=cleaned_html,
                evidence=upsert_evidence,
                source_pages=upsert_pages,
                concept_uuid=upsert_concept_uuid,
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


@router.get("/api/runs/{run_id}/notes-coverage")
async def notes_coverage_endpoint(run_id: int):
    """The holistic notes coverage checklist for a run
    (docs/PLAN-notes-coverage-and-routing.md Phase 7).

    Returns the FINAL (post-reviewer) checklist: one entry per top-level note
    with its status, placements, sub-ref detail, and reviewer overlay, plus a
    summary and the banner state:

      * ``reviewed`` — the notes reviewer pass completed; this is the final list.
      * ``not_reviewed`` — the reviewer pass failed/crashed; draft shown.
      * ``inventory_unavailable`` — scout inventory empty/failed (loud, never a
        silent green).
      * ``pre_feature`` — a legacy run with no coverage rows at all.

    404 if the run does not exist.
    """
    from db import repository as repo
    from server import COVERAGE_META_NOTE
    from notes.coverage_checklist import row_is_unresolved

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        db_rows = repo.fetch_notes_coverage(conn, run_id)
    finally:
        conn.close()

    # The banner meta row (note_num == COVERAGE_META_NOTE) carries the banner in
    # its `status`; its absence means the feature never ran for this run.
    banner = "pre_feature"
    content = []
    for r in db_rows:
        if r["note_num"] == COVERAGE_META_NOTE:
            banner = r["status"] or "pre_feature"
        else:
            content.append(r)

    # Nest sub-ref child rows under their top-level parent (fetch orders the
    # top-level row before its children within each note_num).
    parents: dict[int, dict] = {}
    order: list[int] = []
    for r in content:
        n = r["note_num"]
        if r["subnote_ref"] is None:
            parents[n] = {
                "note_num": n,
                "title": r["title"],
                "status": r["status"],
                "reason": r["reason"],
                "placements": r["placements"],
                "reviewer_added": r["reviewer_added"],
                "reviewer_verdict": r["reviewer_verdict"],
                "page_lo": r["page_lo"],
                "page_hi": r["page_hi"],
                "subnotes": [],
            }
            order.append(n)
        else:
            parents.setdefault(n, {
                "note_num": n, "title": "", "status": "", "reason": "",
                "placements": [], "reviewer_added": False,
                "reviewer_verdict": None, "page_lo": None, "page_hi": None,
                "subnotes": [],
            })
            if n not in order:
                order.append(n)
            parents[n]["subnotes"].append({
                "subnote_ref": r["subnote_ref"],
                "state": r["status"],
                "reason": r["reason"],
            })

    rows = [parents[n] for n in order]

    summary = {"placed": 0, "missing": 0, "skipped": 0, "suspected_gap": 0,
               "total": len(rows), "unresolved": 0}
    for row in rows:
        if row["status"] in summary:
            summary[row["status"]] += 1
        if row_is_unresolved(
            row["status"], row.get("reviewer_verdict"),
            [s["state"] for s in row["subnotes"]],
        ):
            summary["unresolved"] += 1

    return {
        "run_id": run_id,
        "banner": banner,
        "inventory_available": banner != "inventory_unavailable",
        "rows": rows,
        "summary": summary,
    }
