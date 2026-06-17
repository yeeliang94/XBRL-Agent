"""Phase 1 step 1.17-1.20 — concepts page + reconciliation queue API.

Endpoints registered against the main FastAPI app:

* GET    /api/runs/{run_id}/concepts            — tree for the run
* PATCH  /api/concepts/{uuid}/display_label     — UI override
* GET    /api/runs/{run_id}/conflicts           — open reconciliation
                                                  queue items
* POST   /api/conflicts/{id}/resolve            — close a queue item

All endpoints stay neutral to canonical_mode — they read from the v4
tables which exist regardless of which pipeline filled them.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class DisplayLabelPatch(BaseModel):
    display_label: Optional[str] = None


class ConflictResolution(BaseModel):
    action: str  # 'resolved' | 'dismissed'


def register_concept_routes(app, audit_db_getter) -> None:
    def _conn():
        c = sqlite3.connect(str(audit_db_getter()))
        c.execute("PRAGMA foreign_keys = ON")
        c.row_factory = sqlite3.Row
        return c

    @app.get("/api/runs/{run_id}/concepts")
    def get_concepts(run_id: int):
        """Return every concept linked to a run plus its current fact.

        Phase 1 keeps this simple: we return ALL concepts for every
        template the run touched, with the CY/Company fact embedded so
        the UI can render a tree-with-values in a single round-trip.
        """
        conn = _conn()
        try:
            run = conn.execute(
                "SELECT id FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")

            # Peer-review #3: scope concept_nodes to ONLY the templates
            # this run actually touched.  Without this filter a
            # long-lived DB with many imported templates would leak
            # every template's concepts (with null values) into every
            # run's response.  We infer the template set from the
            # concepts that carry facts for this run — the canonical
            # signal of "what did this run extract".
            # Numeric notes (sheets 13/14) also live in concept_nodes +
            # run_concept_facts (PLAN-notes-template-registry Track B), but
            # they are reviewed in the Notes tab, not here — exclude their
            # template_ids so they don't duplicate into the Values tree
            # (decision §9.3). Scope by the exact notes template_id set (not a
            # '%-notes-%' LIKE) so a face slug containing "notes" can't be
            # mis-excluded.
            from notes_types import notes_template_ids

            notes_ids = sorted(notes_template_ids())
            not_in = (
                f"AND n.template_id NOT IN ({','.join('?' * len(notes_ids))})"
                if notes_ids
                else ""
            )
            template_ids = [
                r[0] for r in conn.execute(
                    f"""
                    SELECT DISTINCT n.template_id
                    FROM run_concept_facts f
                    JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid
                    WHERE f.run_id = ?
                      {not_in}
                    """,
                    (run_id, *notes_ids),
                ).fetchall()
            ]
            if not template_ids:
                # No facts yet → nothing to show.  Return empty rather
                # than leaking the whole DB.
                return {"run_id": run_id, "concepts": []}

            placeholders = ",".join("?" for _ in template_ids)

            # Phase 4 step 4.12: collect every (period, entity_scope)
            # fact per concept so Group runs can render their toggle.
            # The CY/Company fact is embedded directly on the top
            # level for backward compatibility with Phase 1's UI; the
            # full scope_facts map sits beside it.
            rows = conn.execute(
                f"""
                SELECT n.concept_uuid, n.parent_uuid, n.kind,
                       n.canonical_label, n.display_label,
                       n.render_sheet, n.render_row, n.render_col,
                       n.matrix_col, n.matrix_col_label, n.template_id, tpl.shape,
                       f.value, f.value_status, f.children_status,
                       f.source, f.evidence,
                       (SELECT COUNT(*) FROM concept_edges e
                          WHERE e.parent_uuid = n.concept_uuid) AS edge_count
                FROM concept_nodes n
                JOIN concept_templates tpl ON tpl.template_id = n.template_id
                LEFT JOIN run_concept_facts f
                  ON f.concept_uuid = n.concept_uuid
                  AND f.run_id = ?
                  AND f.period = 'CY'
                  AND f.entity_scope = 'Company'
                WHERE n.template_id IN ({placeholders})
                ORDER BY n.template_id, n.render_sheet, n.render_row, n.render_col
                """,
                (run_id, *template_ids),
            ).fetchall()

            # Second pass: every fact for this run, grouped by
            # (concept_uuid, entity_scope, period).  Returned as
            # ``scope_facts`` on each concept.
            all_facts = conn.execute(
                """
                SELECT concept_uuid, period, entity_scope, value
                FROM run_concept_facts WHERE run_id = ?
                """,
                (run_id,),
            ).fetchall()
            scope_facts_by_uuid: dict[str, dict] = {}
            for f in all_facts:
                bucket = scope_facts_by_uuid.setdefault(
                    f["concept_uuid"], {}
                )
                scope = bucket.setdefault(f["entity_scope"], {})
                scope[f["period"]] = f["value"]

            # Aliases — secondary physical render coords for a single
            # canonical concept. The cross-sheet rollup case (face row
            # pointing at a sub-sheet *Total via formula) demotes the
            # face coord to an alias; emitting one extra view-row per
            # alias makes the Review/Values page mirror the workbook
            # (one face row + one sub row, same concept). The alias row
            # carries the SAME concept payload (value, scope_facts,
            # editability) but with render coords swapped to the alias
            # location and ``is_alias: true`` so the UI can label it.
            alias_rows = conn.execute(
                f"""
                SELECT a.concept_uuid, a.alias_sheet, a.alias_row, a.alias_col
                FROM concept_render_aliases a
                JOIN concept_nodes n ON n.concept_uuid = a.concept_uuid
                WHERE n.template_id IN ({placeholders})
                """,
                tuple(template_ids),
            ).fetchall()
            aliases_by_uuid: dict[str, list[sqlite3.Row]] = {}
            for ar in alias_rows:
                aliases_by_uuid.setdefault(ar["concept_uuid"], []).append(ar)

            def _to_view(r, *, alias=None) -> dict:
                rs = r["render_sheet"] if alias is None else alias["alias_sheet"]
                rr = r["render_row"] if alias is None else int(alias["alias_row"])
                rc = r["render_col"] if alias is None else alias["alias_col"]
                return {
                    "concept_uuid": r["concept_uuid"],
                    "parent_uuid": r["parent_uuid"],
                    "kind": r["kind"],
                    "canonical_label": r["canonical_label"],
                    "display_label": r["display_label"],
                    "render_sheet": rs,
                    "render_row": rr,
                    "render_col": rc,
                    "matrix_col": r["matrix_col"],
                    "matrix_col_label": r["matrix_col_label"],
                    "shape": r["shape"],
                    "template_id": r["template_id"],
                    "value": r["value"],
                    "value_status": r["value_status"],
                    "children_status": r["children_status"],
                    "source": r["source"],
                    "evidence": r["evidence"],
                    # A cell is user-editable when it's a data-entry
                    # node (LEAF or a MATRIX_CELL with no outgoing
                    # edges). Mirrors the facts-API PATCH guard so the
                    # UI only offers an input where the backend will
                    # accept the write. Alias rows are NEVER editable:
                    # the workbook's cross-sheet formula owns the value
                    # at the alias coord, so writing there would clash
                    # with the formula on the next export.
                    "editable": (
                        alias is None
                        and r["kind"] in ("LEAF", "MATRIX_CELL")
                        and (r["edge_count"] or 0) == 0
                    ),
                    "is_alias": alias is not None,
                    "scope_facts": scope_facts_by_uuid.get(
                        r["concept_uuid"], {}
                    ),
                }

            concept_views: list[dict] = []
            for r in rows:
                concept_views.append(_to_view(r))
                for a in aliases_by_uuid.get(r["concept_uuid"], []):
                    concept_views.append(_to_view(r, alias=a))
            # Resort so alias rows land in their face-sheet section
            # naturally (same ORDER BY as the SQL query).
            concept_views.sort(key=lambda v: (
                v["template_id"], v["render_sheet"], v["render_row"],
                v["render_col"] or "",
            ))

            return {"run_id": run_id, "concepts": concept_views}
        finally:
            conn.close()

    @app.get("/api/templates")
    def list_templates():
        """Phase 5.1 — every imported template, run-independent.

        Powers the global template-settings page: the user picks a template
        and renames its display_labels once, applying the change to all
        future runs (display_label lives on concept_nodes, not per-run). The
        template_id encodes standard/level/statement so the UI can group.
        """
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT template_id, shape FROM concept_templates "
                "ORDER BY template_id"
            ).fetchall()
            return {"templates": [dict(r) for r in rows]}
        finally:
            conn.close()

    @app.get("/api/templates/{template_id}/concepts")
    def template_concepts(template_id: str):
        """Phase 5.1 — concept rows for one template, with NO run scope.

        Distinct from GET /api/runs/{id}/concepts: that endpoint embeds a
        run's facts (values); this is the bare template vocabulary for the
        global settings page, so it carries labels only — never values."""
        conn = _conn()
        try:
            exists = conn.execute(
                "SELECT 1 FROM concept_templates WHERE template_id = ?",
                (template_id,),
            ).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="Template not found")
            rows = conn.execute(
                "SELECT concept_uuid, parent_uuid, kind, canonical_label, "
                "display_label, render_sheet, render_row, render_col, "
                "matrix_col, matrix_col_label FROM concept_nodes WHERE template_id = ? "
                "ORDER BY render_sheet, render_row, render_col",
                (template_id,),
            ).fetchall()
            return {
                "template_id": template_id,
                "concepts": [dict(r) for r in rows],
            }
        finally:
            conn.close()

    @app.patch("/api/concepts/{concept_uuid}/display_label")
    def patch_display_label(concept_uuid: str, body: DisplayLabelPatch):
        """UI-only override.  PRD §9 — never leaves the system; the
        exporter ignores it and writes the canonical label."""
        conn = _conn()
        try:
            existing = conn.execute(
                "SELECT concept_uuid FROM concept_nodes "
                "WHERE concept_uuid = ?", (concept_uuid,)
            ).fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Concept not found")
            conn.execute(
                "UPDATE concept_nodes SET display_label = ? "
                "WHERE concept_uuid = ?",
                (body.display_label, concept_uuid),
            )
            conn.commit()
            return {"ok": True, "concept_uuid": concept_uuid,
                    "display_label": body.display_label}
        finally:
            conn.close()

    @app.get("/api/runs/{run_id}/conflicts")
    def get_conflicts(run_id: int):
        conn = _conn()
        try:
            rows = conn.execute(
                """
                SELECT c.id, c.concept_uuid, c.period, c.entity_scope,
                       c.kind, c.residual, c.detail, c.status,
                       c.created_at, c.resolved_at,
                       n.canonical_label, n.render_sheet, n.render_row
                FROM run_concept_conflicts c
                LEFT JOIN concept_nodes n ON n.concept_uuid = c.concept_uuid
                WHERE c.run_id = ?
                ORDER BY c.created_at DESC
                """,
                (run_id,),
            ).fetchall()
            return {"run_id": run_id,
                    "conflicts": [dict(r) for r in rows]}
        finally:
            conn.close()

    @app.post("/api/conflicts/{conflict_id}/resolve")
    def resolve_conflict(conflict_id: int, body: ConflictResolution):
        if body.action not in {"resolved", "dismissed"}:
            raise HTTPException(
                status_code=400,
                detail=f"action must be 'resolved' or 'dismissed', "
                       f"got {body.action!r}",
            )
        conn = _conn()
        try:
            existing = conn.execute(
                "SELECT id FROM run_concept_conflicts WHERE id = ?",
                (conflict_id,),
            ).fetchone()
            if existing is None:
                raise HTTPException(status_code=404,
                                     detail="Conflict not found")
            conn.execute(
                "UPDATE run_concept_conflicts SET status = ?, "
                "resolved_at = ? WHERE id = ?",
                (body.action, _now(), conflict_id),
            )
            conn.commit()
            return {"ok": True, "id": conflict_id, "status": body.action}
        finally:
            conn.close()
