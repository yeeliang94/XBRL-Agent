"""Phase 2 — facts → mTool fill instructions
(docs/PLAN-mtool-fill-pipeline.md).

Turns a completed run's canonical facts (``run_concept_facts`` joined to
``concept_nodes``) into the fill-instruction document that
``mtool.offline_fill`` consumes. This is the app-side half of the bridge: it
knows the extraction's variant, units, and signs; the fill tool stays
variant-neutral and just writes cells.

What this module owns (mirrors the plan's Key Decisions):

* **Source = ``run_concept_facts`` only** — the reviewed canonical store,
  never the scratch xlsx (gotcha #21). Read through
  ``mtool.receipt.snapshot_facts``, so one fill corresponds to ONE revision of
  the data even though a completed run stays editable.
* **LEAF only** — ABSTRACT section headers and COMPUTED totals are excluded;
  mTool derives totals with its own template formulas (the fill tool's formula
  guard is the second line of defence). MATRIX_CELL (SOCIE) is deferred and
  **counted**, never silently dropped.
* **Semantic, not physical** — writes carry a ``column_role``
  (current_year / prior_year / group_* / company_*), NOT a physical column
  letter. mTool's real column layout (observed: labels col D, values E/F —
  different from ours) is resolved against the actual template at fill time
  via :func:`apply_column_map`, not baked in here.
* **Unit-aware translation** — see :mod:`mtool.translation`. The shipped
  manifest is identity, so today's doc carries the DB value verbatim; a
  non-identity manifest scales money and leaves share counts alone, and
  refuses any row whose unit class it can't establish.

WHAT OUR STORED VALUES MEAN (plan Step 5 — pinned by
``tests/test_mtool_value_conventions.py``)

Read this before writing any translation rule. These are conventions of the
extraction prompts and the live template formulas, not of this module:

* **Unit.** Facts are stored in the unit shown on the face of the source
  statement — the run's ``denomination`` ("thousands", "units", …) describes
  it and is surfaced in the doc's ``meta``. Nothing rescales at extraction
  time, so a statement printed in RM'000 stores 1,595 for RM 1,595,000.
* **Sign — SOPL / SOPL-Analysis.** Expenses and losses are stored as POSITIVE
  magnitudes (finance costs, tax expense, impairment losses, depreciation):
  the template's subtotal formula does the subtracting (``prompts/_base.md``).
* **Sign — SOCF.** Signs follow cash-flow direction: receipts and inflows
  positive, payments and outflows NEGATIVE, indirect-method add-backs
  positive.
* **Sign — SOCIE / SoRE.** ``Dividends paid`` is stored POSITIVE because every
  SOCIE/SoRE template's "Total increase (decrease) in equity" formula
  SUBTRACTS the row (ADR-002, gotcha #15). Rows the formula ADDS take negative
  inputs when they represent a reduction.
* **Sign — OCI / SOCI.** Losses are genuine negative movements, unlike SOPL
  expense rows.

The practical consequence: our stored sign is already the sign the SSM
template's own formulas expect, so the identity manifest's ``sign=+1``
everywhere is a claim about mTool matching SSM's formula conventions — which
is precisely what the Windows acceptance run (Step 7) has to confirm before
any sign rule is added here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mtool.receipt import snapshot_facts
from mtool.translation import IDENTITY, TranslationManifest, UnknownUnitClass
from mtool.units import unit_class_for_label
from notes.labels import normalize_label

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
    manifest: TranslationManifest = IDENTITY,
    strict: bool = True,
) -> dict[str, Any]:
    """Build the semantic mTool fill document for a run.

    ``manifest`` is the unit/sign translation applied on the way out; it
    defaults to identity (emit the DB value verbatim). A non-identity manifest
    raises :class:`mtool.translation.UnknownUnitClass` on the first row whose
    unit class it cannot establish — a missing rule is never a silent
    pass-through (finding 2).

    The returned doc is ``mtool.offline_fill``-shaped but with an unresolved
    ``sheets`` block (physical columns are ``None`` — the layout of the
    operator's actual mTool template isn't known here). Call
    :func:`apply_column_map` with a resolved column map before handing it to
    ``run_fill``. Structure::

        {
          "meta": {run_id, generated_at, translation_version, snapshot,
                   filing_standard, filing_level, denomination,
                   sheets_covered, counts, unit_classes, columns_unresolved},
          "sheets": {sheet: {"label_column": None,
                             "columns": {role: None, ...}}},
          "writes": [{sheet, label, column_role, value}, ...],
          "strict": bool,
        }
    """
    rows, snapshot = snapshot_facts(
        db_path, run_id,
        filing_standard=filing_standard, filing_level=filing_level)

    # Every conflict in the SAME snapshot the writes come from, so the
    # preflight verdict and the receipt describe one revision of the facts
    # (peer review, 2026-08-05). Independent of the LEAF/scope filters below —
    # the preflight decides which conflicts block and which merely warn.
    snapshot_conflicts = [
        {
            "canonical_label": r["canonical_label"],
            "render_sheet": r["render_sheet"],
            "kind": r["kind"],
            "period": r["period"],
            "entity_scope": r["entity_scope"],
        }
        for r in rows
        if r["value_status"] == "conflict"
    ]

    writes: list[dict[str, Any]] = []
    sheets: dict[str, dict[str, Any]] = {}
    excluded_matrix = 0
    excluded_not_disclosed = 0
    excluded_no_value = 0
    excluded_out_of_scope = 0
    # Conflict-status facts still carry a value and ARE written (blanking a
    # cell the operator can't see is worse than an unresolved one they can),
    # but the count is surfaced so a conflicted figure never flows into a
    # filing silently. The preflight (mtool/preflight.py) BLOCKS on these —
    # this count is what the operator reads before overriding.
    conflict_writes = 0
    # Per-unit-class tally + the rows whose unit the taxonomy doesn't know.
    # Reported, not hidden: with an identity manifest these are harmless, and
    # under any other manifest they are exactly what would have gone wrong.
    unit_counts: dict[str, int] = {}
    unknown_units: list[dict[str, str]] = []
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
        if r["value_status"] == "conflict":
            conflict_writes += 1

        sheet = r["render_sheet"]
        label = r["canonical_label"]
        unit_class = unit_class_for_label(label, filing_standard)
        unit_counts[unit_class or "unknown"] = (
            unit_counts.get(unit_class or "unknown", 0) + 1)
        if unit_class is None:
            unknown_units.append({"sheet": sheet, "label": label})
        value = manifest.translate(
            r["value"], unit_class=unit_class, label=label, sheet=sheet,
            template_id=r["template_id"],
            label_normalized=normalize_label(label))

        writes.append({
            "sheet": sheet,
            "label": label,
            "column_role": role,
            "value": _whole(value),
        })
        sheet_cfg = sheets.setdefault(sheet, {"label_column": None,
                                              "columns": {}})
        sheet_cfg["columns"].setdefault(role, None)

    meta = {
        "run_id": run_id,
        # A fill doc must be self-describing: WHEN it was built and under WHICH
        # translation rules (finding 8). Without these, a doc found on disk
        # can't be told apart from one built under different rules.
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "translation_version": manifest.version,
        # Identity for the fact revision this doc was built from, so two fills
        # of the same still-editable run are distinguishable (Step 19).
        "snapshot": snapshot,
        # Conflicts as of THAT snapshot — the preflight evaluates these rather
        # than re-reading the DB, so its verdict cannot describe a different
        # revision than the writes (peer review, 2026-08-05).
        "conflicts": snapshot_conflicts,
        "filing_standard": filing_standard.lower(),
        "filing_level": filing_level.lower(),
        "denomination": denomination,
        "sheets_covered": sorted(sheets),
        "counts": {
            "writes": len(writes),
            "conflict_writes": conflict_writes,
            "excluded_matrix_socie": excluded_matrix,
            "excluded_not_disclosed": excluded_not_disclosed,
            "excluded_no_value": excluded_no_value,
            "excluded_out_of_scope": excluded_out_of_scope,
        },
        "unit_classes": unit_counts,
        "unit_class_unknown": unknown_units,
        "columns_unresolved": True,
    }
    return {"meta": meta, "sheets": sheets, "writes": writes, "strict": strict}


def _whole(value):
    """Keep ints int where the translated result is a whole number.

    mTool cells hold plain numbers; writing ``1595.0`` where the source said
    ``1595`` is a gratuitous difference in the filed instance.
    """
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


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


__all__ = ["build_fill_doc", "apply_column_map", "UnknownUnitClass"]
