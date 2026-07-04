"""Phase 2 — facts → mTool fill instructions (docs/PLAN.md).

Turns a completed run's canonical facts (``run_concept_facts`` joined to
``concept_nodes``) into the fill-instruction document that
``mtool.offline_fill`` consumes. This is the app-side half of the bridge: it
knows the extraction's variant, signs, and scale; the fill tool stays
variant-neutral and just writes cells.

What this module owns (mirrors the plan's Key Decisions):

* **Source = ``run_concept_facts`` only** — the reviewed canonical store,
  never the scratch xlsx (gotcha #21).
* **LEAF only** — ABSTRACT section headers and COMPUTED totals are excluded;
  mTool derives totals with its own template formulas (the fill tool's formula
  guard is the second line of defence). MATRIX_CELL (SOCIE) is deferred and
  **counted**, never silently dropped.
* **Semantic, not physical** — writes carry a ``column_role``
  (current_year / prior_year / group_* / company_*), NOT a physical column
  letter. mTool's real column layout (observed: labels col D, values E/F —
  different from ours) is resolved against the actual template at fill time
  via :func:`apply_column_map`, not baked in here.
* **Scale/sign translation is explicit and defaults to identity** — see
  :func:`build_fill_doc`'s ``scale`` argument. We do NOT guess a scale factor;
  emitting the DB value verbatim (scale=1) is the only safe default until the
  Windows recon confirms whether mTool stores the full unscaled figure or the
  thousands figure (docs/MTOOL-ZIP-RECON-BRIEF.md Task 3.6).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

# Only these value_statuses carry a figure we should write. ``not_disclosed``
# is an intentional blank (counted, not written); the rest either have no
# value or are headers/totals excluded by the kind filter.
_WRITABLE_STATUSES = {"observed", "explicit_zero", "user_override", "conflict"}


def _column_role(period: str, entity_scope: str, filing_level: str) -> str | None:
    """Map a fact's (period, entity_scope) to a semantic column role.

    Company filing renders only Company scope (current/prior year). Group
    filing renders four value columns: Group CY/PY then Company CY/PY. A fact
    whose scope has no column on this filing level returns ``None`` (dropped).
    """
    period = (period or "").upper()
    scope = (entity_scope or "").capitalize()
    level = (filing_level or "company").lower()
    if period not in ("CY", "PY"):
        return None
    suffix = "current_year" if period == "CY" else "prior_year"
    if level == "group":
        if scope == "Group":
            return f"group_{suffix}"
        if scope == "Company":
            return f"company_{suffix}"
        return None
    # Company filing: only Company scope has a home.
    if scope == "Company":
        return suffix
    return None


def build_fill_doc(
    db_path: str | Path,
    run_id: int,
    *,
    filing_standard: str,
    filing_level: str,
    denomination: str | None = None,
    scale: float = 1.0,
    strict: bool = True,
) -> dict[str, Any]:
    """Build the semantic mTool fill document for a run.

    ``scale`` multiplies every emitted value. **Default 1.0 = emit the DB
    value verbatim.** Only pass a non-1 scale once the Windows recon has
    confirmed the unit mTool expects vs. the unit the facts are stored in
    (``denomination``); a wrong scale silently 1000×-inflates every figure.

    The returned doc is ``mtool.offline_fill``-shaped but with an unresolved
    ``sheets`` block (physical columns are ``None`` — the layout of the
    operator's actual mTool template isn't known here). Call
    :func:`apply_column_map` with a resolved column map before handing it to
    ``run_fill``. Structure::

        {
          "meta": {run_id, filing_standard, filing_level, denomination,
                   scale, sheets_covered, excluded, counts},
          "sheets": {sheet: {"label_column": None,
                             "columns": {role: None, ...}}},
          "writes": [{sheet, label, column_role, value}, ...],
          "strict": bool,
        }
    """
    family_prefix = f"{filing_standard.lower()}-{filing_level.lower()}-"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT f.concept_uuid, f.period, f.entity_scope, f.value,
                   f.value_status, n.canonical_label, n.kind, n.render_sheet,
                   tpl.shape AS shape
            FROM run_concept_facts f
            JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid
            JOIN concept_templates tpl ON tpl.template_id = n.template_id
            WHERE f.run_id = ?
              AND n.template_id LIKE ?
            ORDER BY n.render_sheet, n.render_row, f.entity_scope, f.period
            """,
            (run_id, family_prefix + "%"),
        ).fetchall()
    finally:
        conn.close()

    writes: list[dict[str, Any]] = []
    sheets: dict[str, dict[str, Any]] = {}
    excluded_matrix = 0
    excluded_not_disclosed = 0
    excluded_no_value = 0
    excluded_out_of_scope = 0
    # De-dup: a concept surfacing on multiple physical coords (cross-sheet
    # alias) shares one uuid; keyed on (uuid, period, scope) so each fact is
    # emitted once.
    seen: set[tuple[str, str, str]] = set()

    for r in rows:
        if r["shape"] == "matrix" or r["kind"] == "MATRIX_CELL":
            excluded_matrix += 1
            continue
        if r["kind"] != "LEAF":
            continue  # ABSTRACT header or COMPUTED total — not fillable
        role = _column_role(r["period"], r["entity_scope"], filing_level)
        if role is None:
            excluded_out_of_scope += 1
            continue
        if r["value_status"] == "not_disclosed":
            excluded_not_disclosed += 1
            continue
        if r["value_status"] not in _WRITABLE_STATUSES or r["value"] is None:
            excluded_no_value += 1
            continue
        key = (r["concept_uuid"], r["period"], r["entity_scope"])
        if key in seen:
            continue
        seen.add(key)

        sheet = r["render_sheet"]
        writes.append({
            "sheet": sheet,
            "label": r["canonical_label"],
            "column_role": role,
            "value": _scaled(r["value"], scale),
        })
        sheet_cfg = sheets.setdefault(sheet, {"label_column": None,
                                              "columns": {}})
        sheet_cfg["columns"].setdefault(role, None)

    meta = {
        "run_id": run_id,
        "filing_standard": filing_standard.lower(),
        "filing_level": filing_level.lower(),
        "denomination": denomination,
        "scale": scale,
        "sheets_covered": sorted(sheets),
        "counts": {
            "writes": len(writes),
            "excluded_matrix_socie": excluded_matrix,
            "excluded_not_disclosed": excluded_not_disclosed,
            "excluded_no_value": excluded_no_value,
            "excluded_out_of_scope": excluded_out_of_scope,
        },
        "columns_unresolved": True,
    }
    return {"meta": meta, "sheets": sheets, "writes": writes, "strict": strict}


def _scaled(value, scale: float):
    """Apply the scale multiplier, keeping ints int where the result is whole."""
    scaled = value * scale
    if isinstance(scaled, float) and scaled.is_integer():
        return int(scaled)
    return scaled


def apply_column_map(
    doc: dict[str, Any],
    column_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve a doc's placeholder ``sheets`` block against a real column map.

    ``column_map`` maps each sheet to ``{"label_column": "D",
    "columns": {"current_year": "E", ...}}`` — the physical layout of the
    operator's mTool template (from ``inspect`` / auto-detection). Returns a
    NEW ready-to-run doc; raises ``ValueError`` if any sheet or role the
    writes need is missing from the map, so an incomplete map fails loudly
    rather than writing to a ``None`` column.
    """
    resolved_sheets: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for sheet, cfg in doc.get("sheets", {}).items():
        provided = column_map.get(sheet)
        if provided is None:
            missing.append(f"sheet {sheet!r}")
            continue
        label_col = provided.get("label_column")
        if not label_col:
            missing.append(f"{sheet!r}.label_column")
        cols: dict[str, Any] = {}
        for role in cfg["columns"]:
            phys = provided.get("columns", {}).get(role)
            if not phys:
                missing.append(f"{sheet!r}.columns.{role}")
            else:
                cols[role] = phys
        resolved_sheets[sheet] = {"label_column": label_col, "columns": cols}
    if missing:
        raise ValueError(
            "column_map is missing physical columns for: "
            + ", ".join(missing))
    out = dict(doc)
    out["sheets"] = resolved_sheets
    meta = dict(out.get("meta", {}))
    meta["columns_unresolved"] = False
    out["meta"] = meta
    return out
