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
        for c in concepts:
            uid = c["concept_uuid"]
            existing = seen.get(uid)
            if existing is None:
                seen[uid] = c
                continue
            this_sheet = (c.get("render_key") or {}).get("sheet", "")
            prev_sheet = (existing.get("render_key") or {}).get("sheet", "")
            if _is_canonical_home(this_sheet) and not _is_canonical_home(prev_sheet):
                seen[uid] = c

        for c in seen.values():
            uid = c["concept_uuid"]
            rk = c.get("render_key") or {}
            conn.execute(
                """
                INSERT INTO concept_nodes(
                    concept_uuid, template_id, parent_uuid, kind,
                    canonical_label, render_sheet, render_row, render_col,
                    matrix_col
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(concept_uuid) DO UPDATE SET
                    template_id = excluded.template_id,
                    parent_uuid = excluded.parent_uuid,
                    kind = excluded.kind,
                    canonical_label = excluded.canonical_label,
                    render_sheet = excluded.render_sheet,
                    render_row = excluded.render_row,
                    render_col = excluded.render_col,
                    matrix_col = excluded.matrix_col
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
                ),
            )

        # 3. Edges — flush + reinsert is simpler than diffing.  Because
        # the importer is idempotent on the same JSON, the result is
        # the same as the previous run.
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
                    # Phase-0 parser uses "ref" coordinates for the
                    # majority of edges; we resolve them to UUIDs by
                    # looking up (sheet, row, col) in concept_nodes.
                    ref = edge.get("ref") or {}
                    if is_matrix:
                        # Matrix cells share (sheet, row) across columns —
                        # disambiguate by the formula's column so a
                        # within-column SOCIE sum (=B6+B7) wires to the
                        # right component cell, not a sibling column.
                        row = conn.execute(
                            "SELECT concept_uuid FROM concept_nodes "
                            "WHERE template_id = ? AND render_sheet = ? "
                            "AND render_row = ? AND render_col = ?",
                            (template_id, ref.get("sheet", ""),
                             int(ref.get("row", 0) or 0), ref.get("col", "")),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            "SELECT concept_uuid FROM concept_nodes "
                            "WHERE template_id = ? AND render_sheet = ? "
                            "AND render_row = ?",
                            (template_id, ref.get("sheet", ""),
                             int(ref.get("row", 0) or 0)),
                        ).fetchone()
                    if row is None:
                        continue
                    child_uuid = row[0]
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