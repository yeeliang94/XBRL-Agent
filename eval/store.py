"""Benchmark library store — CRUD + the gold-fact grid.

The eval subsystem owns its own DB writes (the repository layer only persists
the per-run scorecard). This module is the single place that knows the
``eval_benchmarks`` / ``eval_benchmark_templates`` / ``gold_concept_facts``
shape, so the API router (``api/eval.py``) stays a thin HTTP shell.

The grid builder (:func:`benchmark_concepts`) returns the SAME view-row shape
as ``concept_model/concepts_routes.py``'s ``/api/runs/{id}/concepts`` so the
frontend ``ConceptsPage`` can render gold facts with zero new grid code (just a
``source`` prop — see docs/PLAN-eval-benchmark.md Step 9).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import openpyxl

from statement_types import VARIANTS, template_path
from concept_model.parser import _derive_template_id
from eval.ingest import ingest_workbook, IngestResult

# A run must have a real, finished extraction to be worth freezing as gold.
# draft/running have no facts; failed has only thrashed partial facts (the
# agent gave up mid-extraction); aborted is a Stop-All / cancellation partial
# merge — seeding gold from any of these is a footgun. Only the two genuine
# end-of-pipeline states qualify. (completed_with_errors still ran every agent;
# the user hand-corrects in the gold editor regardless.)
_SEEDABLE_RUN_STATUSES = ("completed", "completed_with_errors")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _candidate_templates(standard: str, level: str) -> list[tuple[str, str]]:
    """Every ``(template_id, statement_value)`` registered for a
    ``(standard, level)`` whose file exists on disk.

    Derived from the variant registry so the statement_type is authoritative
    (a slug like ``sore`` maps to statement ``SOCIE``). Used to auto-detect a
    benchmark's template set from an uploaded workbook.
    """
    out: list[tuple[str, str]] = []
    for (statement, variant_name) in VARIANTS:
        try:
            path = template_path(statement, variant_name, level, standard)
        except ValueError:
            continue  # NotPrepared / standard mismatch — no template
        if not path.exists():
            continue
        out.append((_derive_template_id(path), statement.value))
    return out


def _template_sheets(conn: sqlite3.Connection, template_id: str) -> set[str]:
    """Distinct render + alias sheet names a template occupies."""
    sheets = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT render_sheet FROM concept_nodes WHERE template_id = ?",
            (template_id,),
        ).fetchall()
    }
    sheets |= {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT a.alias_sheet FROM concept_render_aliases a "
            "JOIN concept_nodes n ON n.concept_uuid = a.concept_uuid "
            "WHERE n.template_id = ?",
            (template_id,),
        ).fetchall()
    }
    return sheets


def resolve_template_set(
    conn: sqlite3.Connection,
    standard: str,
    level: str,
    xlsx_path: str | Path,
) -> list[tuple[str, str]]:
    """Auto-detect which ``(standard, level)`` templates a workbook covers.

    Returns the ``(template_id, statement_value)`` pairs whose sheets appear in
    the uploaded workbook — the benchmark's explicit template set. Empty when
    no template matches (the caller rejects that loudly).
    """
    wb = openpyxl.load_workbook(str(xlsx_path), read_only=True)
    try:
        wb_sheets = set(wb.sheetnames)
    finally:
        wb.close()
    matched: list[tuple[str, str]] = []
    for template_id, statement in _candidate_templates(standard, level):
        if _template_sheets(conn, template_id) & wb_sheets:
            matched.append((template_id, statement))
    return matched


def create_benchmark_from_workbook(
    conn: sqlite3.Connection,
    *,
    name: str,
    document: Optional[str],
    filing_standard: str,
    filing_level: str,
    xlsx_path: str | Path,
) -> dict:
    """Create a benchmark, record its template set, and ingest gold facts.

    Auto-detects the template set from the workbook's sheets. Raises
    ``ValueError`` when no template matches (wrong file / wrong standard or
    level). The caller owns the commit so the whole create+ingest is one
    transaction — a failed ingest rolls back the half-made benchmark.
    """
    template_set = resolve_template_set(
        conn, filing_standard, filing_level, xlsx_path
    )
    if not template_set:
        raise ValueError(
            f"No {filing_standard.upper()} {filing_level} template matched the "
            "uploaded workbook's sheets. Check the filing standard / level and "
            "that this is an MBRS template workbook."
        )

    cur = conn.execute(
        "INSERT INTO eval_benchmarks(name, document, filing_standard, "
        "filing_level, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, document, filing_standard, filing_level, _now()),
    )
    benchmark_id = int(cur.lastrowid)
    for template_id, statement in template_set:
        conn.execute(
            "INSERT INTO eval_benchmark_templates(benchmark_id, template_id, "
            "statement_type) VALUES (?, ?, ?)",
            (benchmark_id, template_id, statement),
        )

    ingest: IngestResult = ingest_workbook(
        conn, benchmark_id, xlsx_path, [t for t, _ in template_set]
    )
    # Reject a benchmark that matched sheets but yielded no gold cells — it's a
    # useless, selectable artifact that would later grade to a meaningless 0/0.
    # The caller rolls back on this ValueError, discarding the half-made rows.
    if ingest.ingested == 0:
        if ingest.skipped_formula_cells:
            # All gradeable cells were un-recalculated formulas (a fully
            # machine-exported workbook) — name the real cause, not "empty".
            raise ValueError(
                f"All {ingest.skipped_formula_cells} gradeable cell(s) in the "
                f"matched worksheets ({', '.join(ingest.matched_sheets)}) are "
                "live formulas with no cached value, so none could be read. "
                "Seed the benchmark from a run instead, or open the workbook in "
                "Excel and re-save before uploading."
            )
        raise ValueError(
            "No numeric gold cells found in the matched worksheets "
            f"({', '.join(ingest.matched_sheets) or 'none'}). The workbook's "
            "value columns appear empty — fill the gold figures and re-upload."
        )
    return {
        "id": benchmark_id,
        "ingested": ingest.ingested,
        "skipped": ingest.skipped,
        "skipped_formula_cells": ingest.skipped_formula_cells,
        "matched_sheets": ingest.matched_sheets,
        "statements": sorted({s for _, s in template_set}),
        "template_ids": [t for t, _ in template_set],
        # Loud, actionable warning: a machine-exported workbook stores the
        # SOCIE matrix + cross-sheet face rollups as LIVE formulas with no
        # cached value. openpyxl(data_only=True) reads those as None, so they
        # silently never become gold (the 2026-06-05 sub-sheet-loss incident).
        # Seeding from a run (create_benchmark_from_run) avoids this entirely.
        "warning": (
            f"{ingest.skipped_formula_cells} gradeable cell(s) were skipped "
            "because the workbook's formulas were never recalculated (no "
            "cached value). Seed the benchmark from a run instead, or open the "
            "workbook in Excel and re-save before uploading."
            if ingest.skipped_formula_cells else None
        ),
    }


def create_benchmark_from_run(
    conn: sqlite3.Connection,
    *,
    name: str,
    run_id: int,
    document: Optional[str] = None,
) -> dict:
    """Create a benchmark seeded directly from a finished run's extracted facts.

    The lossless counterpart to :func:`create_benchmark_from_workbook`. Instead
    of reverse-ingesting an ``.xlsx`` (which drops un-recalculated formula
    cells — the SOCIE matrix + cross-sheet face rollups read as ``None`` under
    ``data_only=True``), this copies ``run_concept_facts`` straight into
    ``gold_concept_facts``. Every LEAF / MATRIX_CELL the run wrote — including
    all sub-sheet and matrix leaves — is captured exactly, ready to be
    hand-corrected in the gold editor.

    The template SET is the distinct set of templates the run actually wrote
    facts to (each ``template_id`` already encodes standard×level×variant, so
    the set is inherently scoped — gotcha #21/#23). COMPUTED totals are skipped
    (formula-derived; grading excludes them anyway).

    Raises ``ValueError`` when the run is missing, isn't in a seedable terminal
    state, or yielded zero gradeable facts. The caller owns the commit.
    """
    # Reuse the repository's run reader (it parses run_config_json + restores
    # row_factory) rather than re-implementing the SQL + JSON decode here.
    from db import repository as repo

    run = repo.fetch_run(conn, run_id)
    if run is None:
        raise ValueError(f"Run {run_id} not found.")
    status = run.status
    if status not in _SEEDABLE_RUN_STATUSES:
        raise ValueError(
            f"Run {run_id} has status '{status}'. Seed a benchmark only from a "
            "finished run (completed / completed_with_errors) — a draft, "
            "running, failed, or aborted run has no reliable, complete "
            "extraction to freeze as gold."
        )
    cfg = run.config or {}
    standard = (cfg.get("filing_standard") or "mfrs").lower()
    level = (cfg.get("filing_level") or "company").lower()

    # The template set = templates the run actually wrote gradeable facts to.
    # Map each back to its statement_type via the registry (authoritative — a
    # 'sore' slug is statement SOCIE), so eval_benchmark_templates is correct.
    statement_by_template = {
        tid: stmt for tid, stmt in _candidate_templates(standard, level)
    }
    template_rows = conn.execute(
        "SELECT DISTINCT n.template_id "
        "FROM run_concept_facts f "
        "JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
        "WHERE f.run_id = ? AND n.kind IN ('LEAF','MATRIX_CELL')",
        (run_id,),
    ).fetchall()
    template_set = [
        (r[0], statement_by_template[r[0]])
        for r in template_rows
        if r[0] in statement_by_template
    ]
    if not template_set:
        raise ValueError(
            f"Run {run_id} has no gradeable extracted facts to freeze as gold "
            "(no LEAF/MATRIX_CELL facts in a registered "
            f"{standard.upper()} {level} template)."
        )

    now = _now()
    cur = conn.execute(
        "INSERT INTO eval_benchmarks(name, document, filing_standard, "
        "filing_level, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, document or f"seeded from run {run_id}", standard, level, now),
    )
    benchmark_id = int(cur.lastrowid)
    for template_id, statement in template_set:
        conn.execute(
            "INSERT INTO eval_benchmark_templates(benchmark_id, template_id, "
            "statement_type) VALUES (?, ?, ?)",
            (benchmark_id, template_id, statement),
        )

    template_ids = [t for t, _ in template_set]
    placeholders = ",".join("?" for _ in template_ids)
    source = f"seeded from run {run_id}"
    cur2 = conn.execute(
        f"INSERT INTO gold_concept_facts(benchmark_id, concept_uuid, period, "
        f"entity_scope, value, value_status, source, updated_at) "
        f"SELECT ?, f.concept_uuid, f.period, f.entity_scope, f.value, "
        f"       f.value_status, ?, ? "
        f"FROM run_concept_facts f "
        f"JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
        f"WHERE f.run_id = ? AND n.kind IN ('LEAF','MATRIX_CELL') "
        f"  AND n.template_id IN ({placeholders}) "
        f"ON CONFLICT(benchmark_id, concept_uuid, period, entity_scope) "
        f"DO UPDATE SET value = excluded.value, "
        f"  value_status = excluded.value_status, source = excluded.source, "
        f"  updated_at = excluded.updated_at",
        (benchmark_id, source, now, run_id, *template_ids),
    )
    ingested = cur2.rowcount if cur2.rowcount and cur2.rowcount > 0 else conn.execute(
        "SELECT COUNT(*) FROM gold_concept_facts WHERE benchmark_id = ?",
        (benchmark_id,),
    ).fetchone()[0]

    # Reject the useless 0/0 benchmark — same guard the workbook path enforces.
    # ``ingested`` counts COPIED rows, but the grader's DENOMINATOR excludes
    # ``not_disclosed`` and blank-value facts (grader._present_number → None),
    # so a run whose gradeable facts are all not-disclosed/blank would copy
    # rows yet grade 0/0. Count denominator-gradeable rows with grader-
    # equivalent semantics: explicit_zero counts; anything not not_disclosed
    # with a non-null value counts (NULL status behaves like 'observed').
    gradeable = conn.execute(
        "SELECT COUNT(*) FROM gold_concept_facts "
        "WHERE benchmark_id = ? AND ("
        "  value_status = 'explicit_zero' "
        "  OR (COALESCE(value_status, '') != 'not_disclosed' "
        "      AND value IS NOT NULL))",
        (benchmark_id,),
    ).fetchone()[0]
    if gradeable == 0:
        raise ValueError(
            f"Run {run_id}'s gradeable facts are all blank or not-disclosed — "
            "the benchmark would grade 0/0. Seed from a run that extracted "
            "real values."
        )
    return {
        "id": benchmark_id,
        "ingested": int(ingested),
        "source_run_id": run_id,
        "source_run_status": status,
        "statements": sorted({s for _, s in template_set}),
        "template_ids": template_ids,
    }


def benchmark_template_ids(conn: sqlite3.Connection, benchmark_id: int) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT template_id FROM eval_benchmark_templates WHERE benchmark_id = ?",
            (benchmark_id,),
        ).fetchall()
    ]


def list_benchmarks(conn: sqlite3.Connection) -> list[dict]:
    """Every benchmark with its template/statement set + gold cell count."""
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, document, filing_standard, filing_level, "
            "created_at FROM eval_benchmarks ORDER BY created_at DESC, id DESC"
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            bid = r["id"]
            statements = [
                t[0]
                for t in conn.execute(
                    "SELECT DISTINCT statement_type FROM eval_benchmark_templates "
                    "WHERE benchmark_id = ? ORDER BY statement_type",
                    (bid,),
                ).fetchall()
            ]
            gold_count = conn.execute(
                "SELECT COUNT(*) FROM gold_concept_facts WHERE benchmark_id = ?",
                (bid,),
            ).fetchone()[0]
            out.append({
                "id": bid,
                "name": r["name"],
                "document": r["document"],
                "filing_standard": r["filing_standard"],
                "filing_level": r["filing_level"],
                "created_at": r["created_at"],
                "statements": statements,
                "gold_cell_count": int(gold_count),
            })
        return out
    finally:
        conn.row_factory = prior


def get_benchmark(conn: sqlite3.Connection, benchmark_id: int) -> Optional[dict]:
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT id, name, document, filing_standard, filing_level, "
            "created_at FROM eval_benchmarks WHERE id = ?",
            (benchmark_id,),
        ).fetchone()
        if r is None:
            return None
        templates = [
            {"template_id": t["template_id"], "statement_type": t["statement_type"]}
            for t in conn.execute(
                "SELECT template_id, statement_type FROM eval_benchmark_templates "
                "WHERE benchmark_id = ? ORDER BY statement_type",
                (benchmark_id,),
            ).fetchall()
        ]
        gold_count = conn.execute(
            "SELECT COUNT(*) FROM gold_concept_facts WHERE benchmark_id = ?",
            (benchmark_id,),
        ).fetchone()[0]
        return {
            "id": r["id"],
            "name": r["name"],
            "document": r["document"],
            "filing_standard": r["filing_standard"],
            "filing_level": r["filing_level"],
            "created_at": r["created_at"],
            "templates": templates,
            "statements": sorted({t["statement_type"] for t in templates}),
            "gold_cell_count": int(gold_count),
        }
    finally:
        conn.row_factory = prior


def delete_benchmark(conn: sqlite3.Connection, benchmark_id: int) -> bool:
    """Hard-delete a benchmark; CASCADE sweeps its templates + gold + scores.
    Also nulls ``runs.benchmark_id`` on any run that graded against it (the
    runs FK is ON DELETE SET NULL)."""
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.execute("DELETE FROM eval_benchmarks WHERE id = ?", (benchmark_id,))
    return cur.rowcount > 0


def _gold_number(value, value_status: Optional[str]) -> Optional[float]:
    """The numeric a gold fact carries, or ``None``. Mirrors
    ``grader._present_number``: ``not_disclosed`` reads as absent,
    ``explicit_zero`` as ``0.0``."""
    if value_status == "not_disclosed":
        return None
    if value_status == "explicit_zero":
        return 0.0
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def gold_display_totals(
    conn: sqlite3.Connection, benchmark_id: int, template_ids: list[str]
) -> dict[tuple[str, str, str], float]:
    """Derive COMPUTED / MATRIX total values from a benchmark's gold LEAVES,
    for DISPLAY ONLY (read-only; nothing is persisted, grading is untouched).

    The gold table stores only leaf observations (ingest skips COMPUTED, and a
    benchmark's totals carry no gold row), so the gold editor would otherwise
    render every total blank. This mirrors the run cascade's edge-sum +
    blank-child semantics (``concept_model/cascade.py``) — once at least one
    child of a formula has a number, missing siblings count as spreadsheet
    zeroes; a formula with no numeric child stays blank. It deliberately omits
    the run cascade's conflict / partial_state machinery: gold parents are
    purely derived, there is no observed total to reconcile against.

    Returns ``{(concept_uuid, period, entity_scope): value}`` for the cells the
    cascade FILLED. Any coordinate that already carries a gold value (e.g. a
    SOCIE MATRIX total whose cached formula value was ingested) is left
    authoritative and excluded — the human's figure wins over the re-derivation.
    """
    if not template_ids:
        return {}
    placeholders = ",".join("?" for _ in template_ids)
    # Parents whose value is derived from children — COMPUTED rows, plus SOCIE
    # MATRIX_CELL totals that carry edges (mirrors the run cascade's gate).
    parents = {
        r[0]
        for r in conn.execute(
            f"SELECT concept_uuid FROM concept_nodes n "
            f"WHERE n.template_id IN ({placeholders}) AND ("
            f"  n.kind = 'COMPUTED' OR (n.kind = 'MATRIX_CELL' AND EXISTS("
            f"    SELECT 1 FROM concept_edges e "
            f"    WHERE e.parent_uuid = n.concept_uuid)))",
            tuple(template_ids),
        ).fetchall()
    }
    if not parents:
        return {}

    edges_by_parent: dict[str, list[tuple[str, float]]] = {}
    for parent_uuid, child_uuid, coef in conn.execute(
        "SELECT parent_uuid, child_uuid, coefficient FROM concept_edges"
    ).fetchall():
        if parent_uuid in parents:
            edges_by_parent.setdefault(parent_uuid, []).append(
                (child_uuid, float(coef))
            )
    if not edges_by_parent:
        return {}

    # Seed the working set with every gold leaf value, and remember which
    # coordinates are gold-authoritative so the cascade never overwrites them.
    facts: dict[tuple[str, str, str], Optional[float]] = {}
    gold_keys: set[tuple[str, str, str]] = set()
    scope_pairs: set[tuple[str, str]] = set()
    for uuid, period, scope, value, status in conn.execute(
        "SELECT concept_uuid, period, entity_scope, value, value_status "
        "FROM gold_concept_facts WHERE benchmark_id = ?",
        (benchmark_id,),
    ).fetchall():
        num = _gold_number(value, status)
        facts[(uuid, period, scope)] = num
        if num is not None:
            gold_keys.add((uuid, period, scope))
        scope_pairs.add((period, scope))

    # Fixed-point sum per (period, scope) — a later pass sees an earlier pass's
    # recomputed parent, so nested totals (parent-of-parent) converge.
    computed: dict[tuple[str, str, str], float] = {}
    for period, scope in scope_pairs:
        changed = True
        passes = 50
        while changed and passes > 0:
            changed = False
            passes -= 1
            for parent_uuid, edges in edges_by_parent.items():
                key = (parent_uuid, period, scope)
                if key in gold_keys:
                    continue  # human gold wins; don't re-derive over it
                total = 0.0
                has_numeric_child = False
                for child_uuid, coef in edges:
                    v = facts.get((child_uuid, period, scope))
                    if v is None:
                        continue
                    has_numeric_child = True
                    total += coef * v
                if not has_numeric_child:
                    continue
                total = round(total, 2)
                prev = facts.get(key)
                if prev is None or abs(prev - total) > 0.01:
                    facts[key] = total
                    computed[key] = total
                    changed = True
    return computed


def benchmark_concepts(conn: sqlite3.Connection, benchmark_id: int) -> list[dict]:
    """Concept grid for a benchmark, with gold facts embedded.

    Mirrors ``/api/runs/{id}/concepts``'s view-row shape (so ``ConceptsPage``
    renders it unchanged) but joins ``gold_concept_facts`` instead of
    ``run_concept_facts``. Only LEAF / MATRIX_CELL leaves are editable; alias
    rows are read-only (the workbook's cross-sheet formula owns the value).

    COMPUTED / MATRIX totals carry no gold row, so their values are derived
    on-read from the gold leaves (:func:`gold_display_totals`) — display only,
    nothing persisted — so the editor shows totals instead of blanks.
    """
    template_ids = benchmark_template_ids(conn, benchmark_id)
    if not template_ids:
        return []
    placeholders = ",".join("?" for _ in template_ids)
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT n.concept_uuid, n.parent_uuid, n.kind,
                   n.canonical_label, n.display_label,
                   n.render_sheet, n.render_row, n.render_col,
                   n.matrix_col, n.matrix_col_label, n.template_id, tpl.shape,
                   g.value, g.value_status, g.source,
                   (SELECT COUNT(*) FROM concept_edges e
                      WHERE e.parent_uuid = n.concept_uuid) AS edge_count
            FROM concept_nodes n
            JOIN concept_templates tpl ON tpl.template_id = n.template_id
            LEFT JOIN gold_concept_facts g
              ON g.concept_uuid = n.concept_uuid
              AND g.benchmark_id = ?
              AND g.period = 'CY'
              AND g.entity_scope = 'Company'
            WHERE n.template_id IN ({placeholders})
            ORDER BY n.template_id, n.render_sheet, n.render_row, n.render_col
            """,
            (benchmark_id, *template_ids),
        ).fetchall()

        all_facts = conn.execute(
            "SELECT concept_uuid, period, entity_scope, value "
            "FROM gold_concept_facts WHERE benchmark_id = ?",
            (benchmark_id,),
        ).fetchall()
        scope_facts: dict[str, dict] = {}
        for f in all_facts:
            bucket = scope_facts.setdefault(f["concept_uuid"], {})
            bucket.setdefault(f["entity_scope"], {})[f["period"]] = f["value"]

        # Derive COMPUTED / MATRIX totals from the gold leaves (display only)
        # and merge them into both the scope_facts map (so every scope/period
        # column shows its total) and the CY/Company primary `value` below.
        display_totals = gold_display_totals(conn, benchmark_id, template_ids)
        for (uuid, period, scope), val in display_totals.items():
            scope_facts.setdefault(uuid, {}).setdefault(scope, {})[period] = val

        out: list[dict] = []
        for r in rows:
            out.append({
                "concept_uuid": r["concept_uuid"],
                "parent_uuid": r["parent_uuid"],
                "kind": r["kind"],
                "canonical_label": r["canonical_label"],
                "display_label": r["display_label"],
                "render_sheet": r["render_sheet"],
                "render_row": r["render_row"],
                "render_col": r["render_col"],
                "matrix_col": r["matrix_col"],
                "matrix_col_label": r["matrix_col_label"],
                "shape": r["shape"],
                "template_id": r["template_id"],
                # Gold leaf value, or the on-read derived total for a
                # COMPUTED / MATRIX row that has no gold row of its own.
                "value": display_totals.get(
                    (r["concept_uuid"], "CY", "Company"), r["value"]
                ),
                "value_status": r["value_status"],
                "children_status": None,
                "source": r["source"],
                "evidence": None,
                "editable": (
                    r["kind"] in ("LEAF", "MATRIX_CELL")
                    and (r["edge_count"] or 0) == 0
                ),
                "is_alias": False,
                "scope_facts": scope_facts.get(r["concept_uuid"], {}),
            })
        return out
    finally:
        conn.row_factory = prior


def patch_gold_fact(
    conn: sqlite3.Connection,
    benchmark_id: int,
    concept_uuid: str,
    *,
    period: str,
    entity_scope: str,
    value: Optional[float],
) -> dict:
    """Upsert a single gold value (the spot-edit path after import).

    Validates the concept exists, belongs to the benchmark's template set, and
    is a gradeable leaf (LEAF / MATRIX_CELL). Returns the stored fact dict.
    Raises ``ValueError`` on a bad target so the route can map it to a 4xx.
    """
    if period not in ("CY", "PY"):
        raise ValueError(f"period must be CY or PY, got {period!r}")
    if entity_scope not in ("Company", "Group"):
        raise ValueError(
            f"entity_scope must be Company or Group, got {entity_scope!r}"
        )
    template_ids = set(benchmark_template_ids(conn, benchmark_id))
    node = conn.execute(
        "SELECT template_id, kind FROM concept_nodes WHERE concept_uuid = ?",
        (concept_uuid,),
    ).fetchone()
    if node is None:
        raise ValueError("Concept not found")
    if node[0] not in template_ids:
        raise ValueError("Concept is not part of this benchmark's templates")
    if node[1] not in ("LEAF", "MATRIX_CELL"):
        raise ValueError("Only LEAF / MATRIX_CELL gold cells are editable")

    # An empty value clears the gold cell entirely (the human marks it blank).
    if value is None:
        conn.execute(
            "DELETE FROM gold_concept_facts WHERE benchmark_id = ? "
            "AND concept_uuid = ? AND period = ? AND entity_scope = ?",
            (benchmark_id, concept_uuid, period, entity_scope),
        )
        return {"benchmark_id": benchmark_id, "concept_uuid": concept_uuid,
                "period": period, "entity_scope": entity_scope, "value": None}

    conn.execute(
        "INSERT INTO gold_concept_facts(benchmark_id, concept_uuid, period, "
        "entity_scope, value, value_status, source, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'observed', 'user_edit', ?) "
        "ON CONFLICT(benchmark_id, concept_uuid, period, entity_scope) "
        "DO UPDATE SET value = excluded.value, "
        "value_status = excluded.value_status, "
        "source = excluded.source, updated_at = excluded.updated_at",
        (benchmark_id, concept_uuid, period, entity_scope, float(value), _now()),
    )
    return {"benchmark_id": benchmark_id, "concept_uuid": concept_uuid,
            "period": period, "entity_scope": entity_scope, "value": float(value)}
