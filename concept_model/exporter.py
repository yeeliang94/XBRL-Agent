"""Phase 1 step 1.11-1.14 — DB-backed Excel exporter.

Reads facts from ``run_concept_facts`` and writes them into a fresh
copy of the template ``.xlsx``.  Three rules from the PRD that this
module owns:

* **Canonical label only in col A** — the UI's ``display_label`` is
  never exported (PRD §9 boundary).  We honour this by ignoring the
  display_label column entirely.
* **aggregate_only branch** — when a COMPUTED parent's
  ``children_status='aggregate_only'``, we replace the live formula
  with the literal value AND annotate the source column so M-Tool
  reviewers know why the breakdown is missing.
* **not_disclosed branch** — leaves marked ``not_disclosed`` stay
  blank in Excel; a side-channel JSON next to the xlsx documents
  which leaves were intentionally blank (so a downstream reviewer can
  distinguish "agent didn't try" from "agent confirmed missing").
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import openpyxl


_DEFAULT_SOURCE_COL = {
    "company": "D",   # MFRS/MPERS Company templates: D = Source
    "group": "F",     # Group templates: F = Source
}


def export_run_to_xlsx(
    db_path: str | Path,
    run_id: int,
    xlsx_path: str | Path,
    *,
    filing_level: str = "company",
    template_id: str | None = None,
) -> int:
    """Fill ``xlsx_path`` in-place with the canonical-mode facts.

    The template is expected to already exist at ``xlsx_path`` — the
    caller copies the master template into the run's output dir before
    invoking us.  This keeps the formula network intact for any rows we
    don't override.

    ``template_id`` scopes the fact query (and the unmapped-targets check)
    to a single template. The per-statement export pass passes it so one
    statement's unmapped Group/matrix fact can't fail every other
    statement's export, and so the returned count reflects only this
    template (peer-review findings 1 + 3). When ``None`` the whole run is
    exported (legacy callers / tests).

    Returns the number of facts that applied to a sheet present in this
    workbook — the caller uses a zero count to avoid repointing a download
    at a blank template.
    """
    db_path = str(db_path)
    xlsx_path = str(xlsx_path)
    source_col = _DEFAULT_SOURCE_COL.get(filing_level, "D")

    wb = openpyxl.load_workbook(xlsx_path, data_only=False)

    is_group = (filing_level or "").lower() == "group"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Pull every fact joined to its concept_node and template shape,
        # LEFT JOINing ``concept_targets`` so a (period, entity_scope)
        # dimension can route to its dedicated cell.  Routing then splits
        # three ways (resolved in Python below):
        #   * matrix templates (SOCIE)        → always via concept_targets,
        #     for both Company and Group filings (the period/scope
        #     dimension shifts the ROW for stacked blocks, or the COLUMN
        #     for MPERS Company);
        #   * linear Group filings            → via concept_targets (B/C/D/E
        #     column pairs), raise if a dimension is unmapped;
        #   * linear Company filings          → fall back to
        #     ``concept_nodes.render_col`` and only read CY/Company facts.
        rows = conn.execute(
            """
            SELECT f.concept_uuid, f.period, f.entity_scope,
                   f.value, f.value_status, f.children_status,
                   f.source, f.evidence,
                   n.canonical_label, n.kind, n.render_sheet,
                   n.render_row, n.render_col,
                   tpl.shape AS shape,
                   t.target_col AS target_col,
                   t.target_sheet AS target_sheet,
                   t.target_row AS target_row
            FROM run_concept_facts f
            JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid
            JOIN concept_templates tpl ON tpl.template_id = n.template_id
            LEFT JOIN concept_targets t
              ON t.concept_uuid = f.concept_uuid
              AND t.period = f.period
              AND t.entity_scope = f.entity_scope
            WHERE f.run_id = ?
              AND (? IS NULL OR n.template_id = ?)
            """,
            (run_id, template_id, template_id),
        ).fetchall()
    finally:
        conn.close()

    # Resolve each fact to a physical (sheet, row, col) target, applying
    # the three-way routing above. Facts that don't apply to this filing
    # level (e.g. PY/Group on a Company filing) are dropped.
    routed: list[dict[str, Any]] = []
    unmapped: list[tuple] = []
    for r in rows:
        is_matrix = r["shape"] == "matrix"
        if is_matrix or is_group:
            # Targets are mandatory; a NULL means import_group_targets /
            # the matrix importer didn't map this dimension.
            if r["target_col"] is None:
                unmapped.append((r["concept_uuid"], r["period"], r["entity_scope"]))
                continue
            sheet, row, col = r["target_sheet"], int(r["target_row"]), r["target_col"]
        else:
            # Linear Company filing: only CY/Company, render_col fallback.
            if r["period"] != "CY" or r["entity_scope"] != "Company":
                continue
            sheet, row, col = r["render_sheet"], int(r["render_row"]), r["render_col"]
        routed.append({
            "sheet": sheet, "row": row, "col": col,
            "concept_uuid": r["concept_uuid"],
            "canonical_label": r["canonical_label"],
            "kind": r["kind"],
            "shape": r["shape"],
            "value": r["value"],
            "value_status": r["value_status"],
            "children_status": r["children_status"],
            "source": r["source"],
        })

    if unmapped:
        raise ValueError(
            "Export found facts with no concept_targets mapping "
            f"(run {run_id}): {unmapped[:5]}"
            f"{'…' if len(unmapped) > 5 else ''}. Run import_group_targets "
            "(or re-import matrix templates) for every template in the run."
        )

    not_disclosed: list[dict[str, Any]] = []
    applied = 0

    for r in routed:
        sheet = r["sheet"]
        row = int(r["row"])
        col = r["col"] or "B"
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        # A fact whose sheet is in this workbook counts as applied — the
        # caller uses applied>0 to decide whether the export is worth
        # repointing the download at (vs keeping the agent's scratch xlsx).
        applied += 1

        # Column A: canonical label only.  This is idempotent — the
        # template already carries it — but we re-stamp so a Phase-5
        # template-schema drift wouldn't silently leak the old text.
        ws[f"A{row}"] = r["canonical_label"]

        if r["value_status"] == "not_disclosed":
            # Side-channel only; cell stays blank.  Don't touch
            # whatever formula was there (a downstream user might
            # re-fill it).  Preserve the formula by writing back its
            # original value verbatim.
            not_disclosed.append({
                "concept_uuid": r["concept_uuid"],
                "sheet": sheet,
                "row": row,
                "label": r["canonical_label"],
            })
            continue

        if (
            r["kind"] == "COMPUTED"
            and r["children_status"] == "aggregate_only"
            and r["value"] is not None
        ):
            # Replace the live formula with the literal value and
            # annotate the source column so reviewers know why the
            # underlying breakdown is missing.
            cell = ws[f"{col}{row}"]
            cell.value = float(r["value"])
            annotation = "aggregate_only"
            if r["source"]:
                annotation = f"{annotation} — {r['source']}"
            ws[f"{source_col}{row}"] = annotation
            continue

        if r["value"] is not None:
            target = ws[f"{col}{row}"]
            # Preserve live template formulas for *itemised* totals. The PRD
            # promise is "Excel formulas stay live for itemised concepts; only
            # aggregate_only (handled above) replaces a formula with a
            # literal". The cascade persists COMPUTED / matrix totals computed
            # from their children as observed + children_status='itemised' —
            # for those we leave the live formula so Excel/M-Tool recompute it
            # rather than clobbering it with a stale snapshot literal. A raw
            # observed value (children_status NULL — a direct override) or a
            # leaf still gets its literal written.
            existing = target.value
            is_formula_cell = isinstance(existing, str) and existing.startswith("=")
            if is_formula_cell and r["children_status"] == "itemised":
                pass
            else:
                target.value = float(r["value"])

        # Stamp the source column when the fact carries one — but NEVER on
        # matrix (SOCIE) templates. There the fixed source_col (D/F) lands
        # *inside* the equity-component value grid (MFRS spans B..X), so
        # stamping a provenance string would clobber a real Treasury-shares
        # / reserve value. SOCIE source + evidence stay in the DB
        # (run_concept_facts) — the xlsx is a flattened snapshot, the
        # canonical store is the source of truth (cf. gotcha #16 for notes).
        # Per-cell source on a 23-column matrix row is also semantically
        # ambiguous (which component does it describe?), so skipping is
        # both safe and correct.
        if (
            r["shape"] != "matrix"
            and r["source"]
            and r["children_status"] != "aggregate_only"
        ):
            ws[f"{source_col}{row}"] = r["source"]

    wb.save(xlsx_path)

    # Side-channel JSON for not_disclosed leaves.  Always written
    # (even if empty) so downstream tools don't have to special-case
    # absence.
    side = Path(xlsx_path + ".not_disclosed.json")
    side.write_text(
        json.dumps({"run_id": run_id, "entries": not_disclosed},
                   indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return applied
