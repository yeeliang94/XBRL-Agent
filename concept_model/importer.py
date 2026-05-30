"""Phase 1 — Phase-0 JSON → v4 DB importer.

Reads a concept-tree JSON (the shape emitted by
``concept_model.parser.ConceptTree.to_json``) and upserts:

* one row into ``concept_templates``,
* one row per unique concept into ``concept_nodes``,
* one edge row into ``concept_edges`` for every parent → child term.

UPSERT semantics mean re-importing the same JSON is a no-op — the
deterministic UUID5 keys from the parser are the natural identity.
``concept_targets`` is left empty in Phase 1 (Company-only filings
fall back to ``concept_nodes.render_*``); Phase 4 fills it for Group
templates.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def import_template(db_path: str | Path, json_path: str | Path) -> str:
    """Import a concept-tree JSON into the v4 DB tables.

    Returns the ``template_id`` for the caller's convenience (so the
    coordinator can immediately link a run to the template).
    """
    payload: dict[str, Any] = json.loads(Path(json_path).read_text(encoding="utf-8"))
    template_id: str = payload["template_id"]
    concepts: list[dict[str, Any]] = payload["concepts"]
    # "linear" (face statements) or "matrix" (SOCIE). Matrix templates
    # carry per-node render targets inline (Phase 5) and are written into
    # concept_targets during this import rather than via a separate
    # import_group_targets pass.
    shape: str = payload.get("shape", "linear")
    is_matrix = shape == "matrix"

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    # Self-referencing concept_nodes.parent_uuid can point at a sibling
    # that arrives later in iteration order (e.g. dedup picks the sub-
    # sheet row, but its parent abstract row is still on the face
    # sheet).  Deferring FK checks until COMMIT keeps the order-
    # independence invariant without forcing a two-pass insert.
    conn.execute("BEGIN")
    conn.execute("PRAGMA defer_foreign_keys = ON")
    try:
        # 1. template registry row.
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, imported_at, shape) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(template_id) DO UPDATE SET "
            "imported_at = excluded.imported_at, shape = excluded.shape",
            (template_id, str(json_path), _now(), shape),
        )

        # 2. Build a coord→uuid map first so resolving cross-sheet
        # parent/child refs is O(1).  Then upsert nodes.
        #
        # Sub-sheets (rendered as `*-Sub-*` or `*-Analysis-*`) are the
        # canonical home of the formula + edges for any concept that
        # also surfaces on a face sheet via `='Sub'!B39`.  We prefer
        # those rows when collapsing duplicates so concept_nodes anchors
        # at the formula-owning row.  The face row's display coordinate
        # is recoverable via the live xlsx formula at export time, so
        # nothing depends on its presence in concept_nodes for Phase 1.
        def _is_canonical_home(sheet: str) -> bool:
            s = (sheet or "").lower()
            return "sub-" in s or "analysis" in s

        seen: dict[str, dict[str, object]] = {}
        # Any concept whose render_key isn't picked as the primary lands
        # here as an alias. The motivating producer is the parser's
        # cross-sheet linkage step: a face row inherits the sub-sheet
        # concept's UUID but keeps its own render_key. Dedup picks the
        # sub-sheet entry as primary (it owns the formula); the face
        # entry's coords get preserved as an alias so the Review/Values
        # page can mirror the workbook (one face row + one sub row,
        # same concept) and so the edge-resolution coord map below can
        # still translate face refs to the canonical UUID.
        alias_records: list[tuple[str, str, int, str]] = []
        for c in concepts:
            uid = c["concept_uuid"]
            existing = seen.get(uid)
            if existing is None:
                seen[uid] = c
                continue
            this_sheet = (c.get("render_key") or {}).get("sheet", "")
            prev_sheet = (existing.get("render_key") or {}).get("sheet", "")
            if _is_canonical_home(this_sheet) and not _is_canonical_home(prev_sheet):
                # Promote sub-sheet entry to primary; demote the
                # previous (face) entry to an alias.
                demoted = existing
                seen[uid] = c
            else:
                demoted = c
            d_rk = (demoted.get("render_key") or {})
            alias_records.append((
                uid,
                d_rk.get("sheet", ""),
                int(d_rk.get("row", 0) or 0),
                d_rk.get("col", "B"),
            ))

        for c in seen.values():
            uid = c["concept_uuid"]
            rk = c.get("render_key") or {}
            conn.execute(
                """
                INSERT INTO concept_nodes(
                    concept_uuid, template_id, parent_uuid, kind,
                    canonical_label, render_sheet, render_row, render_col,
                    matrix_col, matrix_col_label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(concept_uuid) DO UPDATE SET
                    template_id = excluded.template_id,
                    parent_uuid = excluded.parent_uuid,
                    kind = excluded.kind,
                    canonical_label = excluded.canonical_label,
                    render_sheet = excluded.render_sheet,
                    render_row = excluded.render_row,
                    render_col = excluded.render_col,
                    matrix_col = excluded.matrix_col,
                    matrix_col_label = excluded.matrix_col_label
                """,
                (
                    uid,
                    template_id,
                    c.get("parent_uuid"),
                    c["kind"],
                    c["canonical_label"],
                    rk.get("sheet", ""),
                    int(rk.get("row", 0) or 0),
                    rk.get("col", "B"),
                    rk.get("matrix_col"),
                    rk.get("matrix_col_label"),
                ),
            )

        # 3a. Render aliases — flush + reinsert (same idempotency
        # discipline as edges). Scope the DELETE to this template by
        # joining through concept_nodes so a multi-template DB doesn't
        # cross-pollute.
        conn.execute(
            "DELETE FROM concept_render_aliases WHERE concept_uuid IN ("
            "SELECT concept_uuid FROM concept_nodes WHERE template_id = ?"
            ")",
            (template_id,),
        )
        for uid, a_sheet, a_row, a_col in alias_records:
            conn.execute(
                "INSERT OR IGNORE INTO concept_render_aliases("
                "concept_uuid, alias_sheet, alias_row, alias_col) "
                "VALUES (?, ?, ?, ?)",
                (uid, a_sheet, a_row, a_col),
            )

        # 3b. Edges — flush + reinsert is simpler than diffing. Because
        # the importer is idempotent on the same JSON, the result is
        # the same as the previous run.
        #
        # Edge ``ref`` coords may point at a FACE-sheet row whose
        # render_key was demoted to an alias above (the cross-sheet
        # rollup case). The dedup makes that face coord invisible to a
        # concept_nodes lookup, so the SQL fallback alone silently
        # dropped every cross-sheet child edge — face computed totals
        # then understated. Build a coord→uuid map from the FULL
        # concepts list (which still carries each face entry with the
        # shared canonical UUID) and check it first; the SQL fallback
        # below stays as a safety net for any non-cross-sheet edges the
        # JSON happens to omit from the in-memory map.
        coord_to_uuid: dict[tuple[str, int, str], str] = {}
        coord_to_uuid_no_col: dict[tuple[str, int], str] = {}
        for c in concepts:
            rk = c.get("render_key") or {}
            c_sheet = rk.get("sheet", "")
            c_row = int(rk.get("row", 0) or 0)
            c_col = rk.get("col", "B")
            c_uid = c["concept_uuid"]
            coord_to_uuid[(c_sheet, c_row, c_col)] = c_uid
            coord_to_uuid_no_col.setdefault((c_sheet, c_row), c_uid)

        conn.execute(
            "DELETE FROM concept_edges WHERE parent_uuid IN ("
            "SELECT concept_uuid FROM concept_nodes WHERE template_id = ?"
            ")",
            (template_id,),
        )
        for c in concepts:
            parent_uid = c["concept_uuid"]
            for edge in c.get("edges", []) or []:
                child_uuid = edge.get("child_uuid")
                if child_uuid is None:
                    ref = edge.get("ref") or {}
                    r_sheet = ref.get("sheet", "")
                    r_row = int(ref.get("row", 0) or 0)
                    r_col = ref.get("col", "")
                    if is_matrix:
                        # Matrix cells share (sheet, row) across columns —
                        # disambiguate by the formula's column so a
                        # within-column SOCIE sum (=B6+B7) wires to the
                        # right component cell, not a sibling column.
                        child_uuid = coord_to_uuid.get((r_sheet, r_row, r_col))
                        if child_uuid is None:
                            row = conn.execute(
                                "SELECT concept_uuid FROM concept_nodes "
                                "WHERE template_id = ? AND render_sheet = ? "
                                "AND render_row = ? AND render_col = ?",
                                (template_id, r_sheet, r_row, r_col),
                            ).fetchone()
                            if row is not None:
                                child_uuid = row[0]
                    else:
                        # Linear: try exact (sheet, row, col); fall back
                        # to (sheet, row) since linear concepts anchor
                        # at col B but a formula ref may carry whatever
                        # column letter the cell happened to use.
                        child_uuid = coord_to_uuid.get((r_sheet, r_row, r_col))
                        if child_uuid is None:
                            child_uuid = coord_to_uuid_no_col.get((r_sheet, r_row))
                        if child_uuid is None:
                            row = conn.execute(
                                "SELECT concept_uuid FROM concept_nodes "
                                "WHERE template_id = ? AND render_sheet = ? "
                                "AND render_row = ?",
                                (template_id, r_sheet, r_row),
                            ).fetchone()
                            if row is not None:
                                child_uuid = row[0]
                    if child_uuid is None:
                        continue
                if not child_uuid or child_uuid == parent_uid:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO concept_edges("
                    "parent_uuid, child_uuid, coefficient) "
                    "VALUES (?, ?, ?)",
                    (parent_uid, child_uuid,
                     float(edge.get("coefficient", 1.0))),
                )

        # 4. Matrix render targets — Phase 5. Each MATRIX_CELL carries an
        # inline `targets` list mapping every (period, entity_scope) to a
        # physical (sheet, row, col). For stacked SOCIE blocks the row
        # shifts per block; for MPERS Company the period shifts the column.
        # UNIQUE(concept_uuid, entity_scope, period) keeps re-imports a
        # no-op (idempotent, same as import_group_targets).
        if is_matrix:
            # Flush this template's existing targets first so a geometry
            # change (e.g. a regenerated SOCIE with a removed period/scope
            # dimension) can't leave orphaned rows behind. Same
            # DELETE-then-insert discipline the edges block uses; scoped to
            # this template's concepts so it never touches a linear Group
            # template's import_group_targets rows.
            conn.execute(
                "DELETE FROM concept_targets WHERE concept_uuid IN ("
                "SELECT concept_uuid FROM concept_nodes WHERE template_id = ?"
                ")",
                (template_id,),
            )
            for c in concepts:
                rk = c.get("render_key") or {}
                for t in rk.get("targets") or []:
                    conn.execute(
                        """
                        INSERT INTO concept_targets(
                            concept_uuid, entity_scope, period,
                            target_sheet, target_row, target_col
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(concept_uuid, entity_scope, period)
                        DO UPDATE SET
                            target_sheet = excluded.target_sheet,
                            target_row = excluded.target_row,
                            target_col = excluded.target_col
                        """,
                        (
                            c["concept_uuid"],
                            t["entity_scope"],
                            t["period"],
                            t.get("sheet", ""),
                            int(t.get("row", 0) or 0),
                            t.get("col", "B"),
                        ),
                    )

        conn.commit()
    finally:
        conn.close()

    return template_id


# ---------------------------------------------------------------------------
# Phase 4 step 4.2 — Group-template column targets.
# ---------------------------------------------------------------------------


# Per gotcha #12: Group templates have a fixed 6-column layout.
#   B = Group CY,   C = Group PY
#   D = Company CY, E = Company PY
#   F = Source (handled by exporter, not concept_targets)
#
# A future SOCIE template (Phase 5) breaks this scheme — it uses
# vertical row blocks rather than horizontal column pairs.  This
# helper is therefore restricted to NON-SOCIE Group templates today;
# Phase 5 will add the matrix variant.
_GROUP_COL_LAYOUT = [
    ("CY", "Group",   "B"),
    ("PY", "Group",   "C"),
    ("CY", "Company", "D"),
    ("PY", "Company", "E"),
]


def import_company_targets(db_path: str | Path, template_id: str) -> int:
    """Populate ``concept_targets`` for every LEAF + COMPUTED concept in a
    linear *Company* template (rewrite Phase 6.1).

    Company templates use a fixed 2-column value layout (gotcha #12):
    ``B = CY, C = PY`` (col A = label, col D = source). So each concept
    gets two target rows — ``(Company, CY) → render_col`` (the parser's
    value column, default B) and ``(Company, PY) → C`` — mirroring
    ``cell_resolver.resolve_cell``'s CY=col2 / PY=col3 convention so the
    write (extraction) and read (export) paths stay symmetric.

    Precomputing these lets the exporter route every fact via ONE
    ``concept_targets`` lookup instead of a render_col fallback branch
    (report §5.1). Idempotent (UNIQUE(concept_uuid, entity_scope, period)).
    Returns the number of target rows written.

    Iterates ``concept_nodes`` only — which holds each concept's PRIMARY
    render coord. A cross-sheet rolled-up concept's face *alias* coord
    lives in ``concept_render_aliases`` and is deliberately NOT given a
    target: that face cell holds a live cross-sheet formula and the
    exporter must never write a literal there (v11 contract, gotcha #21).

    Skips ABSTRACT concepts (never written) and SOCIE (matrix — its
    per-cell targets are written inline by :func:`import_template`).
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        rows = conn.execute(
            "SELECT concept_uuid, render_sheet, render_row, render_col "
            "FROM concept_nodes "
            "WHERE template_id = ? AND kind != 'ABSTRACT'",
            (template_id,),
        ).fetchall()
        if not rows:
            return 0

        written = 0
        for concept_uuid, sheet, row, render_col in rows:
            # SOCIE matrix carries per-cell targets from import_template.
            if "socie" in (sheet or "").lower():
                continue
            for period, col in (("CY", render_col or "B"), ("PY", "C")):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO concept_targets(
                        concept_uuid, entity_scope, period,
                        target_sheet, target_row, target_col
                    ) VALUES (?, 'Company', ?, ?, ?, ?)
                    """,
                    (concept_uuid, period, sheet, row, col),
                )
                written += 1
        conn.commit()
    finally:
        conn.close()
    return written


def import_group_targets(db_path: str | Path, template_id: str) -> int:
    """Populate ``concept_targets`` for every LEAF + COMPUTED concept in
    a Group template.

    Idempotent: the unique key on (concept_uuid, entity_scope, period)
    makes re-imports a no-op.  Returns the number of target rows
    written (useful for assertions in tests).

    Skips ABSTRACT concepts — they're never written to, so they don't
    need per-scope render coordinates.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        rows = conn.execute(
            "SELECT concept_uuid, render_sheet, render_row FROM concept_nodes "
            "WHERE template_id = ? AND kind != 'ABSTRACT'",
            (template_id,),
        ).fetchall()
        if not rows:
            return 0

        written = 0
        for concept_uuid, sheet, row in rows:
            # SOCIE skipped — Phase 5 territory.
            if "socie" in (sheet or "").lower():
                continue
            for period, scope, col in _GROUP_COL_LAYOUT:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO concept_targets(
                        concept_uuid, entity_scope, period,
                        target_sheet, target_row, target_col
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (concept_uuid, scope, period, sheet, row, col),
                )
                written += 1
        conn.commit()
    finally:
        conn.close()
    return written
