"""Notes-cell + edited-count routes.

Endpoints:
  ``GET   /api/runs/{run_id}/notes_cells``                 — cells grouped by sheet
  ``PATCH /api/runs/{run_id}/notes_cells/{sheet}/{row}``   — edit one cell's HTML
  ``GET   /api/runs/{run_id}/notes_cells/edited_count``    — post-run notes edits
  ``GET   /api/runs/{run_id}/facts/edited_count``          — post-run fact edits
  ``GET   /api/runs/{run_id}/notes_tables``                — every table, for review

Step 8 (docs/Archive/PLAN-NOTES-RICH-EDITOR.md): the post-run editor reads rich
HTML payloads per cell via GET (grouped by sheet) and saves edits via PATCH. The
wire contract is asserted in tests/test_server_notes_cells_api.py — every endpoint
goes through ``server._open_audit_conn`` so the same DB/WAL pragmas apply.
"""
import logging
import sqlite3
from typing import Optional

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
        "WHERE template_id = ? AND kind = 'LEAF' AND slot_role = 'INPUT' "
        "ORDER BY row",
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
            # v29: styling provenance — null on a blank/unfilled row.
            "style_source": None,
            # v37: the optimistic version token. Null on a row with no cell
            # yet — there is nothing to conflict with.
            "content_revision": None,
            "invalid_target": False,
            "invalid_target_reason": None,
        }

    for c in conn.execute(
        "SELECT row, label, html, evidence, source_pages, updated_at, "
        "style_source, content_revision, concept_uuid, invalid_target, "
        "invalid_target_reason "
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
                "style_source": None,
                "content_revision": None,
                "invalid_target": True,
                "invalid_target_reason": (
                    "This content is stored on a heading or an unknown legacy row."
                ),
            }
            by_row[c["row"]] = base
        base["html"] = c["html"]
        base["evidence"] = c["evidence"]
        base["source_pages"] = decode_source_pages(c["source_pages"])
        base["updated_at"] = c["updated_at"] or ""
        base["style_source"] = c["style_source"]
        base["content_revision"] = c["content_revision"]
        identity_mismatch = bool(
            base.get("node_uuid")
            and c["concept_uuid"] != base["node_uuid"]
        )
        base["invalid_target"] = (
            bool(c["invalid_target"]) or base["invalid_target"] or identity_mismatch
        )
        base["invalid_target_reason"] = (
            c["invalid_target_reason"]
            or (
                "This content is not linked to this run's writable filing field."
                if identity_mismatch else None
            )
            or base["invalid_target_reason"]
        )

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
    # Optimistic version token — the `updated_at` the client last read.
    # Optional so existing callers keep working; when sent, a concurrent
    # change returns 409 instead of silently overwriting. Plan Step 5.2.
    expected_updated_at: Optional[str] = None
    # The real version token (schema v37) — a monotonic counter rather than a
    # second-precision timestamp two writes could share.
    expected_revision: Optional[int] = None


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
    from notes import lineage
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
            # Plan Step 5.2 — optimistic version check. The last-write-wins
            # note above was an acceptable trade when an edit only lost text;
            # it is not once an edit also decides whether a cell still counts
            # as accounted for against its source.
            try:
                lineage.check_version(
                    conn, run_id, sheet, row, body.expected_updated_at,
                    expected_revision=body.expected_revision,
                )
            except lineage.StaleCellError as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail=str(exc))

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

            config = run.config or {}
            standard = config.get("filing_standard", "mfrs")
            level = config.get("filing_level", "company")
            family_prefix = f"{str(standard).lower()}-{str(level).lower()}-"
            registry_available = bool(conn.execute(
                "SELECT 1 FROM notes_nodes WHERE template_id LIKE ? LIMIT 1",
                (family_prefix + "%",),
            ).fetchone())
            template = next(
                (
                    e for e in _notes_template_index(standard, level)
                    if e["sheet"] == sheet
                ),
                None,
            )
            if registry_available and template is None:
                conn.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown notes sheet {sheet!r} for this run.",
                )
            if registry_available and template["is_numeric"]:
                conn.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Numeric notes are edited through the facts API, "
                        "not this endpoint."
                    ),
                )
            node = None
            if registry_available:
                node = conn.execute(
                    "SELECT label, node_uuid FROM notes_nodes "
                    "WHERE template_id = ? AND row = ? AND kind = 'LEAF' "
                    "AND slot_role = 'INPUT'",
                    (template["template_id"], row),
                ).fetchone()
            if registry_available and node is None:
                conn.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Row {row} is a heading or another non-entry row of "
                        f"sheet {sheet!r}. Choose a writable field instead."
                    ),
                )

            from db.repository import decode_source_pages as _decode_pages

            if existing is not None:
                # Update path — preserve the existing label/evidence/pages and
                # swap the HTML while upgrading any legacy field identity to
                # the exact template node (evidence stays read-only, gotcha #16).
                upsert_label = (
                    node["label"] if node is not None else existing["label"]
                )
                upsert_evidence = existing["evidence"]
                upsert_pages = _decode_pages(existing["source_pages"])
                upsert_concept_uuid = (
                    node["node_uuid"] if node is not None else None
                )
            else:
                # Insert path — the row must be a fillable prose registry node.
                if node is None:
                    conn.rollback()
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "The notes field catalog is unavailable; rebuild it "
                            "before adding a new cell."
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
            # Same transaction as the content write, deliberately: a lineage
            # update that ran afterwards would leave a window in which the
            # cell looks source-exact and is not (plan Step 5.2).
            lineage.mark_human_edit(conn, run_id, sheet, row, cleaned_html)
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
            "SELECT label, html, evidence, source_pages, updated_at, "
            "content_revision "
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
        # The refreshed version token — the client must send it on the NEXT
        # save or it would 409 against its own write.
        "content_revision": row_back["content_revision"],
        # Peer-review #7: surface what the sanitiser removed so the
        # editor can tell the user "we dropped a <script> from your
        # paste" instead of silently swapping content. Empty list when
        # the sanitiser was a no-op — always present so clients can
        # treat it as a stable field.
        "sanitizer_warnings": warnings,
    }


@router.delete("/api/runs/{run_id}/notes_cells/{sheet}/{row}")
async def remove_invalid_notes_cell(run_id: int, sheet: str, row: int):
    """Remove quarantined legacy content after an explicit operator choice."""
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT concept_uuid, invalid_target FROM notes_cells "
            "WHERE run_id = ? AND sheet = ? AND row = ?",
            (run_id, sheet, row),
        ).fetchone()
        if existing is None:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Notes cell not found")

        config = run.config or {}
        standard = config.get("filing_standard", "mfrs")
        level = config.get("filing_level", "company")
        template = next(
            (entry for entry in _notes_template_index(standard, level)
             if entry["sheet"] == sheet),
            None,
        )
        writable = False
        node = None
        if template is not None and not template["is_numeric"]:
            node = conn.execute(
                "SELECT node_uuid FROM notes_nodes "
                "WHERE template_id = ? AND sheet = ? AND row = ? "
                "AND kind = 'LEAF' AND slot_role = 'INPUT'",
                (template["template_id"], sheet, row),
            ).fetchone()
            writable = node is not None
        identity_valid = bool(
            writable and node is not None and existing["concept_uuid"] == node["node_uuid"]
        )
        if identity_valid and not existing["invalid_target"]:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail="This is a valid filing field; clear it in the editor instead.",
            )

        conn.execute(
            "DELETE FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
            (run_id, sheet, row),
        )
        repo.add_notes_tombstone(
            conn,
            run_id=run_id,
            sheet=sheet,
            row=row,
        )
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"removed": True, "sheet": sheet, "row": row}


# --------------------------------------------------------------------------
# Source lineage — plan Phase 5, Step 5.3
# --------------------------------------------------------------------------

def _render_from_recorded_blocks(conn, run_id: int, sheet: str, row: int):
    """Re-render a cell from the source parts it was recorded as using.

    Returns ``(rendered_cell, generation_id)`` or ``(None, None)`` when this
    cell has no source lineage — an authored cell or a pre-feature run, which
    is a legitimate state and not an error.
    """
    from notes import source_render
    from notes import source_repository as srepo
    from notes.source_models import SourceBlock

    gen = srepo.active_generation(conn, run_id)
    if gen is None:
        return None, None
    used = conn.execute(
        "SELECT block_id FROM notes_block_usages "
        "WHERE generation_id = ? AND sheet = ? AND row = ? "
        "ORDER BY block_id",
        (gen["id"], sheet, row),
    ).fetchall()
    if not used:
        return None, None
    rows = srepo.fetch_blocks(conn, gen["id"])
    available = [
        SourceBlock(
            block_id=r["block_id"], block_kind=r["block_kind"],
            reading_order=r["reading_order"], canonical_html=r["canonical_html"] or "",
            table_group_id=r["table_group_id"],
        )
        for r in rows
    ]
    return (
        source_render.render_blocks(
            available, [u["block_id"] for u in used],
            row_label=f"{sheet} row {row}",
        ),
        gen["id"],
    )


@router.get("/api/runs/{run_id}/notes_cells/{sheet}/{row}/source-compare")
async def notes_cell_source_compare(run_id: int, sheet: str, row: int):
    """The stored cell beside what its source parts produce.

    A cell with no lineage returns `source_html: null` and `restorable:
    false` — the honest answer, rather than an empty diff that reads as
    "identical".
    """
    from notes import lineage

    conn = server._open_audit_conn()
    try:
        cell = conn.execute(
            "SELECT html, updated_at FROM notes_cells "
            "WHERE run_id = ? AND sheet = ? AND row = ?",
            (run_id, sheet, row),
        ).fetchone()
        if cell is None:
            raise HTTPException(status_code=404, detail="No such cell")
        state = lineage.read_lineage(conn, run_id, sheet, row) or lineage.LineageState()
        rendered, _gen = _render_from_recorded_blocks(conn, run_id, sheet, row)
    finally:
        conn.close()

    return {
        "sheet": sheet,
        "row": row,
        "current_html": cell["html"] or "",
        "updated_at": cell["updated_at"] or "",
        "source_html": rendered.html if rendered else None,
        "content_origin": state.content_origin,
        "diverged": state.diverged,
        "diverged_at": state.source_diverged_at,
        "restorable": bool(rendered and rendered.usable),
    }


class _RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_at: Optional[str] = None


@router.post("/api/runs/{run_id}/notes_cells/{sheet}/{row}/restore-source")
async def notes_cell_restore_source(
    run_id: int, sheet: str, row: int, body: _RestoreBody | None = None,
):
    """Put back what the source parts produce, keeping the audit history.

    The disposition events are append-only, so restoring does not erase the
    record that a person edited the cell — it adds to it.
    """
    from db import repository as repo
    from notes import lineage

    body = body or _RestoreBody()
    conn = server._open_audit_conn()
    try:
        existing = conn.execute(
            "SELECT label, evidence, source_pages FROM notes_cells "
            "WHERE run_id = ? AND sheet = ? AND row = ?",
            (run_id, sheet, row),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="No such cell")

        conn.execute("BEGIN IMMEDIATE")
        try:
            try:
                lineage.check_version(
                    conn, run_id, sheet, row, body.expected_updated_at
                )
            except lineage.StaleCellError as exc:
                conn.rollback()
                raise HTTPException(status_code=409, detail=str(exc))

            rendered, gen_id = _render_from_recorded_blocks(
                conn, run_id, sheet, row
            )
            if rendered is None:
                conn.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This cell was not built from the source document, so "
                        "there is nothing to restore it to."
                    ),
                )
            if not rendered.usable:
                conn.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The source parts for this cell no longer fit in one "
                        "cell, so restoring would cut the note short."
                    ),
                )
            repo.upsert_notes_cell(
                conn, run_id=run_id, sheet=sheet, row=row,
                label=existing["label"], html=rendered.html,
                evidence=existing["evidence"],
                source_pages=repo.decode_source_pages(existing["source_pages"]),
                style_source=rendered.style_source,
            )
            lineage.mark_source_render(
                conn, run_id, sheet, row,
                generation_id=gen_id,
                rendered_sha256=rendered.source_rendered_sha256,
            )
            conn.commit()
        except HTTPException:
            raise
        except Exception:
            conn.rollback()
            raise

        back = conn.execute(
            "SELECT html, updated_at FROM notes_cells "
            "WHERE run_id = ? AND sheet = ? AND row = ?",
            (run_id, sheet, row),
        ).fetchone()
    finally:
        conn.close()

    return {
        "sheet": sheet, "row": row,
        "html": back["html"], "updated_at": back["updated_at"] or "",
        "restored": True,
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


# --------------------------------------------------------------------------
# Source integrity — plan Phase 8
# --------------------------------------------------------------------------

_LEGACY_STATE = "legacy"


@router.get("/api/runs/{run_id}/notes_integrity")
async def notes_integrity(run_id: int):
    """Coverage of the source document, per note, plus the open items.

    Three states this endpoint must keep distinct, because collapsing any two
    of them invents a fact:

    * `legacy` — the run predates the feature. It has no items, and inventing
      an empty checklist would read as "nothing was missed".
    * `off` — the feature existed and was switched off for this run.
    * a real verdict — with the rules and mode that produced it.
    """
    from db import repository as repo
    from notes import integrity_runner
    from notes import source_repository as srepo

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        mode = repo.notes_integrity_mode(conn, run_id)
        gen = srepo.active_generation(conn, run_id)
        if gen is None:
            return {
                "run_id": run_id,
                "state": _LEGACY_STATE if mode is None else (mode or "off"),
                "mode": mode,
                "notes": [],
                "summary": None,
                "findings": [],
                "input_kind": None,
            }

        gen_id = gen["id"]
        counts = srepo.coverage_counts(conn, gen_id)
        stored = integrity_runner.latest_result(conn, run_id) or {}
        usages = {u["block_id"]: u for u in srepo.fetch_usages(conn, gen_id)}
        blocks = srepo.fetch_blocks(conn, gen_id)
        notes_rows = srepo.fetch_notes(conn, gen_id)
        cells = {
            (r["sheet"], r["row"]): r["label"]
            for r in conn.execute(
                "SELECT sheet, row, label FROM notes_cells WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
    finally:
        conn.close()

    from notes.source_models import Disposition, is_resolved
    from notes.source_snippets import _block_text

    per_note: dict[str, list] = {}
    for b in blocks:
        if b["source_note_id"]:
            per_note.setdefault(b["source_note_id"], []).append(b)

    def _item(b) -> dict:
        u = usages.get(b["block_id"])
        disposition = (u["disposition"] if u else Disposition.UNRESOLVED.value)
        reason = u["reason_code"] if u else None
        try:
            resolved = is_resolved(Disposition(disposition), reason)
        except ValueError:
            resolved = False
        placed = None
        if u and u["sheet"] is not None and u["row"] is not None:
            placed = {
                "sheet": u["sheet"], "row": u["row"],
                "label": cells.get((u["sheet"], u["row"])),
            }
        return {
            "block_id": b["block_id"],
            "kind": b["block_kind"],
            "preview": _block_text(b["canonical_html"] or "")[:160],
            "disposition": disposition,
            "reason_code": reason,
            "resolved": resolved,
            "placed_at": placed,
            "locator": b["locator_json"],
            "page": b["page"],
            "table_group_id": b["table_group_id"],
        }

    notes_out = []
    for n in notes_rows:
        items = [_item(b) for b in per_note.get(n["source_note_id"], [])]
        unresolved = sum(1 for i in items if not i["resolved"])
        notes_out.append({
            "source_note_id": n["source_note_id"],
            "note_num": n["top_note_num"],
            "title": n["title"],
            # ONE status per note (review finding 6). The older
            # placed/missing/skipped wording is not shown alongside it.
            "status": "complete" if not unresolved else "needs_review",
            "items_total": len(items),
            "items_unresolved": unresolved,
            "items": items,
        })

    return {
        "run_id": run_id,
        "state": "reviewed" if stored else (mode or "off"),
        "mode": stored.get("mode") or mode,
        "rule_version": stored.get("rule_version"),
        "checked_at": stored.get("created_at"),
        # Word runs navigate by DOM locator, PDF runs by page — peer finding 4.
        # `ingest/word_convert.py` makes a separate PDF with no DOM-to-page map,
        # so a Word item must never offer a PDF page control it cannot honour.
        "input_kind": gen["input_kind"],
        "notes": notes_out,
        "summary": {
            **counts,
            "notes_total": len(notes_out),
            "notes_needing_review": sum(
                1 for n in notes_out if n["status"] == "needs_review"
            ),
            "requires_review": bool(stored.get("requires_review")),
        },
        "findings": stored.get("findings", []),
    }


class _DispositionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_ids: list[str]
    disposition: str
    reason_code: Optional[str] = None
    note: Optional[str] = None
    # Optimistic version per block: the disposition the caller believed was
    # current. A remediation that silently overwrites a decision somebody else
    # just made is the same defect the cell editor had (plan Step 8.3).
    expected_dispositions: Optional[dict[str, str]] = None
    # `attach` places blocks into a cell; `route` records a destination sheet.
    # Without these the panel could only ever exclude, which is one third of
    # the promised remediation set.
    target_sheet: Optional[str] = None
    target_row: Optional[int] = None


@router.post("/api/runs/{run_id}/notes_integrity/disposition")
async def notes_integrity_disposition(run_id: int, body: _DispositionBody):
    """Record what happened to one or more source items, as a person.

    There is no generic dismiss (Step 8.3): a reason comes from the approved
    list, and `UNREADABLE_NEEDS_REVIEW` deliberately does not settle an item.
    The response carries the recomputed counts so the caller sees the effect
    of the change rather than guessing at it (Step 7.4).
    """
    from db import repository as repo
    from notes import integrity_runner
    from notes import source_repository as srepo
    from notes.source_models import (
        EXCLUSION_REASONS, Disposition, IntegrityMode,
    )

    try:
        disposition = Disposition(body.disposition)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{body.disposition!r} is not one of: "
                + ", ".join(d.value for d in Disposition)
            ),
        )
    if disposition is Disposition.EXCLUDED and body.reason_code not in EXCLUSION_REASONS:
        raise HTTPException(
            status_code=422,
            detail=(
                "Leaving an item out needs a reason from the approved list: "
                + ", ".join(sorted(EXCLUSION_REASONS))
            ),
        )
    if not body.block_ids:
        raise HTTPException(status_code=422, detail="No items were named.")

    conn = server._open_audit_conn()
    task_id = None
    try:
        gen = srepo.active_generation(conn, run_id)
        if gen is None:
            raise HTTPException(
                status_code=409,
                detail="This run has no frozen source reading.",
            )
        # Durable, interlocked slot (plan Step 8.3). A remediation writes
        # dispositions and can rewrite a cell, so it must not run while the
        # reviewer or the formatter holds the same run.
        task_id = repo.claim_notes_integrity_task(
            conn, run_id, action=body.disposition,
        )
        if task_id is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Another pass is working on this run's notes. Wait for it "
                    "to finish, then try again."
                ),
            )

        # Version check per block, before any write.
        if body.expected_dispositions:
            current = {
                u["block_id"]: u["disposition"]
                for u in srepo.fetch_usages(conn, gen["id"])
            }
            for bid, expected in body.expected_dispositions.items():
                if current.get(bid, "unresolved") != expected:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Item {bid} changed since you opened this page. "
                            "Reload and re-apply your decision."
                        ),
                    )

        if disposition is Disposition.INCLUDED:
            # `attach` — put the items into a cell. Goes through the one
            # shared writer, so it validates its target and records lineage
            # and placements exactly as an agent write does.
            from notes import source_write

            if not body.target_sheet or body.target_row is None:
                raise HTTPException(
                    status_code=422,
                    detail="Attaching an item needs a destination row.",
                )
            run = repo.fetch_run(conn, run_id)
            cfg = (run.config or {}) if run else {}
            prefix = (
                f"{cfg.get('filing_standard', 'mfrs')}-"
                f"{cfg.get('filing_level', 'company')}-"
            )
            existing = [
                p["block_id"] for p in srepo.active_placements(conn, gen["id"])
                if (p["sheet"], p["row"]) == (body.target_sheet, body.target_row)
            ]
            try:
                source_write.write_cell_from_blocks(
                    conn, run_id=run_id, generation_id=gen["id"],
                    sheet=body.target_sheet, row=body.target_row,
                    block_ids=existing + [
                        b for b in body.block_ids if b not in existing
                    ],
                    actor="human", template_prefix=prefix,
                )
            except source_write.SourceWriteError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        else:
            for bid in body.block_ids:
                try:
                    srepo.record_disposition(
                        conn, run_id, gen["id"], bid, disposition,
                        reason_code=body.reason_code, actor="human",
                        actor_detail="notes integrity panel", note=body.note,
                        sheet=body.target_sheet, row=body.target_row,
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc))

        counts = srepo.coverage_counts(conn, gen["id"])
        mode = IntegrityMode(repo.notes_integrity_mode(conn, run_id) or "off")
        result = integrity_runner.run_and_store(
            conn, run_id, gen["id"], mode=mode,
            attempt=(integrity_runner.latest_result(conn, run_id) or {})
            .get("attempt", 0) + 1,
        )
    finally:
        if task_id is not None:
            try:
                repo.finish_notes_integrity_task(conn, task_id)
            except Exception:  # noqa: BLE001 — never double-fault (gotcha #10)
                logger.warning("could not release the remediation slot",
                               exc_info=True)
        conn.close()

    return {
        "run_id": run_id,
        "updated": len(body.block_ids),
        "summary": counts,
        "requires_review": result.requires_review,
    }


@router.get("/api/runs/{run_id}/notes_integrity/source/{block_id}")
async def notes_integrity_source_block(run_id: int, block_id: str):
    """The full content of one source item, for the side-by-side preview."""
    from notes import source_repository as srepo

    conn = server._open_audit_conn()
    try:
        gen = srepo.active_generation(conn, run_id)
        if gen is None:
            raise HTTPException(status_code=404, detail="No source reading")
        row = conn.execute(
            "SELECT block_id, block_kind, canonical_html, locator_json, page "
            "FROM notes_source_blocks WHERE generation_id = ? AND block_id = ?",
            (gen["id"], block_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="No such source item")
    return {
        "block_id": row["block_id"],
        "kind": row["block_kind"],
        "html": row["canonical_html"] or "",
        "locator": row["locator_json"],
        "page": row["page"],
    }


@router.get("/api/runs/{run_id}/notes_integrity/events")
async def notes_integrity_events(run_id: int, limit: int = 200):
    """The append-only history of every decision made about a source item."""
    conn = server._open_audit_conn()
    try:
        rows = conn.execute(
            "SELECT block_id, from_disposition, to_disposition, reason_code, "
            "actor, actor_detail, note, created_at "
            "FROM notes_disposition_events WHERE run_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (run_id, max(1, min(limit, 1000))),
        ).fetchall()
    finally:
        conn.close()
    return {"run_id": run_id, "events": [dict(r) for r in rows]}


@router.get("/api/runs/{run_id}/export_preflight")
async def export_preflight(run_id: int, include_size: bool = True):
    """What a download or an mTool fill will lose — plan Phase 9, Step 9.1.

    Both exits degrade quietly today: the size ladder reports what it dropped
    only DURING the fill, by which point the operator is committed, and
    neither exit says anything about a note whose source is half accounted
    for. This says both, before either action.

    Advisory: it never blocks. `blocking_count` means "a person should look",
    not "the button is disabled" — partial output stays downloadable
    (Step 7.3).
    """
    from db import repository as repo
    from notes import export_preflight as preflight

    conn = server._open_audit_conn()
    try:
        if repo.fetch_run(conn, run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        result = preflight.run_preflight(
            conn, server.AUDIT_DB_PATH, run_id, include_size=include_size,
        )
    finally:
        conn.close()
    return result.as_dict()


@router.get("/api/runs/{run_id}/notes_tables")
async def notes_tables(run_id: int):
    """Every table across the run's prose notes sheets, for the review surface.

    PLAN-notes-source-integrity-build Phase 2, Steps 2.1 / 2.4.

    Two honesty constraints are baked into the response shape:

    * ``style_state`` is derived PER TABLE from the markup, because
      ``notes_cells.style_source`` is one verdict for the whole cell. A cell
      holding a copied Word table beside a plain one would otherwise report a
      single style for both.
    * ``source_pages`` is returned under ``cell_evidence`` and labelled
      ``"cell"`` — it is the evidence cited for the CELL, not proof of where
      this particular table came from. Per-table provenance needs the source
      blocks (plan Phase 4); claiming it now would be inventing lineage.

    ``table_index`` is the same zero-based document-order index
    ``format_ops`` targets, so "table 2" means the same table in the review
    surface and in a restyle request.
    """
    from db.repository import decode_source_pages
    from notes.table_index import index_tables

    conn = server._open_audit_conn()
    try:
        from db import repository as repo

        if repo.fetch_run(conn, run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")

        rows = conn.execute(
            "SELECT sheet, row, label, html, source_pages, style_source, updated_at "
            "FROM notes_cells WHERE run_id = ? ORDER BY sheet, row",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    tables: list[dict] = []
    for c in rows:
        html = c["html"] or ""
        if "<table" not in html.lower():
            continue
        pages = decode_source_pages(c["source_pages"])
        for entry in index_tables(html):
            item = entry.to_dict()
            item.update({
                "sheet": c["sheet"],
                "row": c["row"],
                "label": c["label"],
                # Stable selection key: unique across the run and stable across
                # reloads as long as the cell's table order is unchanged.
                "table_id": f"{c['sheet']}:{c['row']}:{entry.table_index}",
                "cell_style_source": c["style_source"],
                "cell_evidence": {"kind": "cell", "source_pages": pages},
                "updated_at": c["updated_at"],
            })
            tables.append(item)

    # Step 1.5 lives here rather than in its own endpoint: the counts are
    # exactly what this walk already computes, and a second source of the same
    # number is a second thing to keep in step.
    #
    # TWO different counts, deliberately, because they answer different
    # questions and conflating them is how "3 unstyled" came to mean one cell:
    #   * `plain` counts TABLES whose markup carries no visible formatting;
    #   * `cells_unstyled` counts CELLS the writer marked `unstyled` (or the
    #     legacy `floor`) — the same set the Notes-tab StyleSourceChip shows,
    #     and the unit a formatter pass operates on.
    # A cell holding three plain tables contributes 3 to the first and 1 to
    # the second.
    unstyled_cells = {
        (t["sheet"], t["row"]) for t in tables
        if t["cell_style_source"] in ("unstyled", "floor")
    }
    summary = {
        "tables": len(tables),
        "plain": sum(1 for t in tables if t["style_state"] == "plain"),
        "styled": sum(1 for t in tables if t["style_state"] == "styled"),
        "source": sum(1 for t in tables if t["style_state"] == "source"),
        "flagged": sum(1 for t in tables if t["flags"]),
        "cells_with_tables": len({(t["sheet"], t["row"]) for t in tables}),
        "cells_unstyled": len(unstyled_cells),
    }
    return {"run_id": run_id, "tables": tables, "summary": summary}
