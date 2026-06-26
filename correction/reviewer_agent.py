"""Reviewer agent (docs/Archive/PLAN-reviewer-agent.md, Phase 3).

The reviewer replaces the autonomous canonical correction pass. Instead
of nudging totals to balance, it *investigates the root cause* of a
cross-check failure by walking the face → sub-sheet → PDF chain, applies
grounded fixes into the live (reviewer) version of the run, and FLAGS
only the cases it's stuck on or where it disputes an earlier agent.

Safety is structural, not behavioural:

* **Versioning** — the caller snapshots the original facts before the
  pass (see ``concept_model/versioning.py``); the reviewer writes freely
  and the user can one-click revert.
* **A deterministic no-plug guard** on the only write path
  (:func:`apply_reviewer_fix`) — ungrounded writes and residual plugs
  into catch-all / abstract rows are refused in CODE (invariant #17),
  not merely discouraged in the prompt. The guard is a *best-effort
  backstop*, not a hard security boundary: it keys on the agent-supplied
  ``evidence`` string and a fixed catch-all label list, so a determined or
  hallucinating model could still slip a plug past it (e.g. by fabricating a
  PDF cite, or naming a residual row the list doesn't match). The real safety
  net is the versioning above — every write is reversible — so the guard's
  job is to catch the *accidental* plug, not to be unbypassable.

The standalone helpers (``trace_cascade_source``, ``apply_reviewer_fix``,
``raise_reviewer_flag``) are pure functions so they can be unit-tested
without standing up the full pydantic-ai agent; the agent factory just
registers thin ``@agent.tool`` wrappers around them.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

# Module-scope so pydantic-ai can resolve the RunContext annotation on the
# tool wrappers (lazy eval looks in module globals).
from pydantic_ai import Agent, RunContext

from tools.calculator import calculator_result_json as _calculator_impl
from concept_model.definitions import lookup_as_json as _lookup_definitions_impl


logger = logging.getLogger("server")

_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "reviewer.md"
)
# Light-mode spot-check (clean-run sanity pass) system prompt. The FULL spot
# check reuses reviewer.md; only LIGHT swaps to this tighter body.
_SPOT_CHECK_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "spot_check.md"
)


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _open_conn(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    return conn


def load_open_conflicts(
    db_path: str | Path, run_id: int
) -> list[dict[str, Any]]:
    """Return the run's still-open cascade conflicts, newest-render first.

    Relocated from the now-deleted ``correction/canonical_agent.py`` (rewrite
    Phase 1, step 1.2): the reviewer pass is the only live consumer, so the
    helper now lives in the reviewer-owned module. Joins each
    ``run_concept_conflicts`` row to its ``concept_nodes`` label/render coord
    so callers can describe the conflict without a second lookup.
    """
    conn = _open_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.concept_uuid, c.period, c.entity_scope,
                   c.kind, c.residual, c.detail,
                   n.canonical_label, n.render_sheet, n.render_row
            FROM run_concept_conflicts c
            LEFT JOIN concept_nodes n ON n.concept_uuid = c.concept_uuid
            WHERE c.run_id = ? AND c.status = 'open'
            ORDER BY c.created_at
            """,
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Step 5 — trace_cascade_source: walk DOWN from a failing face cell.
# ---------------------------------------------------------------------------


def _resolve_concept(
    conn: sqlite3.Connection,
    *,
    concept_uuid: Optional[str] = None,
    sheet: Optional[str] = None,
    row: Optional[int] = None,
    template_prefix: Optional[str] = None,
) -> Optional[sqlite3.Row]:
    """Find a concept by UUID, or by a physical (sheet, row) coordinate.

    A face row that cross-rolls-up from a sub-sheet total shares ONE
    concept_uuid with that sub total (gotcha #21); its primary render
    coord lives on ``concept_nodes`` (anchored at the sub-sheet), and the
    demoted face coord lives in ``concept_render_aliases``. So a lookup by
    the face (sheet, row) must consult the alias table too — otherwise the
    reviewer couldn't trace down from a failing FACE cell.

    ``template_prefix`` scopes a (sheet, row) lookup to ONE template family
    (e.g. ``"mfrs-group-"``). ``concept_nodes`` holds every imported
    standard×level (the bootstrap imports them all), and uuids are minted
    per ``(template_id, sheet, row, label)`` — so the SAME (sheet, row)
    exists under MFRS/MPERS × Company/Group with DIFFERENT uuids. Without
    the prefix, the unqualified lookup below resolves an arbitrary
    template's concept (and the reviewer would trace/write the wrong run's
    tree). The canonical write path (``cell_resolver.resolve_cell``) scopes
    by ``template_id`` for exactly this reason; we mirror it. The prefix is
    optional so the pure-function unit tests (single-template DBs) keep
    their terse calls.
    """
    if concept_uuid:
        return conn.execute(
            "SELECT concept_uuid, kind, canonical_label, render_sheet, "
            "render_row, render_col FROM concept_nodes WHERE concept_uuid = ?",
            (concept_uuid,),
        ).fetchone()
    if sheet is not None and row is not None:
        like = f"{template_prefix}%" if template_prefix else None
        if like is not None:
            node = conn.execute(
                "SELECT concept_uuid, kind, canonical_label, render_sheet, "
                "render_row, render_col FROM concept_nodes "
                "WHERE render_sheet = ? AND render_row = ? AND template_id LIKE ?",
                (sheet, int(row), like),
            ).fetchone()
        else:
            node = conn.execute(
                "SELECT concept_uuid, kind, canonical_label, render_sheet, "
                "render_row, render_col FROM concept_nodes "
                "WHERE render_sheet = ? AND render_row = ?",
                (sheet, int(row)),
            ).fetchone()
        if node is not None:
            return node
        # Alias fallback — the face coord points at a sub-sheet total.
        if like is not None:
            alias = conn.execute(
                "SELECT n.concept_uuid, n.kind, n.canonical_label, "
                "n.render_sheet, n.render_row, n.render_col "
                "FROM concept_render_aliases a "
                "JOIN concept_nodes n ON n.concept_uuid = a.concept_uuid "
                "WHERE a.alias_sheet = ? AND a.alias_row = ? "
                "AND n.template_id LIKE ?",
                (sheet, int(row), like),
            ).fetchone()
        else:
            alias = conn.execute(
                "SELECT n.concept_uuid, n.kind, n.canonical_label, "
                "n.render_sheet, n.render_row, n.render_col "
                "FROM concept_render_aliases a "
                "JOIN concept_nodes n ON n.concept_uuid = a.concept_uuid "
                "WHERE a.alias_sheet = ? AND a.alias_row = ?",
                (sheet, int(row)),
            ).fetchone()
        return alias
    return None


def trace_cascade_source(
    db_path: str | Path,
    run_id: int,
    *,
    concept_uuid: Optional[str] = None,
    sheet: Optional[str] = None,
    row: Optional[int] = None,
    period: str = "CY",
    entity_scope: str = "Company",
    template_prefix: Optional[str] = None,
) -> dict[str, Any]:
    """Walk down from a face cell to the sub-sheet total + children feeding it.

    Returns ``{found, concept, children, children_sum, parent_value}``.
    ``concept`` carries the resolved source concept (its primary render
    coord is the sub-sheet total when the face row is an alias). ``children``
    lists each summand with its signed coefficient and current value so the
    reviewer can see WHICH leaf is wrong. ``found`` is False when the cell
    can't be resolved to a concept.
    """
    conn = _open_conn(db_path)
    try:
        concept = _resolve_concept(
            conn, concept_uuid=concept_uuid, sheet=sheet, row=row,
            template_prefix=template_prefix,
        )
        if concept is None:
            return {"found": False, "concept": None, "children": [],
                    "children_sum": None, "parent_value": None}

        cu = concept["concept_uuid"]
        # Where else does this concept physically render? (face alias coords)
        aliases = [
            {"sheet": a["alias_sheet"], "row": a["alias_row"],
             "col": a["alias_col"]}
            for a in conn.execute(
                "SELECT alias_sheet, alias_row, alias_col "
                "FROM concept_render_aliases WHERE concept_uuid = ?",
                (cu,),
            ).fetchall()
        ]

        edges = conn.execute(
            "SELECT e.child_uuid, e.coefficient, n.canonical_label, n.kind, "
            "n.render_sheet, n.render_row "
            "FROM concept_edges e "
            "JOIN concept_nodes n ON n.concept_uuid = e.child_uuid "
            "WHERE e.parent_uuid = ?",
            (cu,),
        ).fetchall()

        children: list[dict[str, Any]] = []
        total = 0.0
        any_numeric = False
        for e in edges:
            fact = conn.execute(
                "SELECT value, value_status, source, evidence "
                "FROM run_concept_facts WHERE run_id = ? AND concept_uuid = ? "
                "AND period = ? AND entity_scope = ?",
                (run_id, e["child_uuid"], period, entity_scope),
            ).fetchone()
            val = fact["value"] if fact else None
            if val is not None:
                any_numeric = True
                total += float(e["coefficient"]) * float(val)
            children.append({
                "concept_uuid": e["child_uuid"],
                "label": e["canonical_label"],
                "kind": e["kind"],
                "coefficient": float(e["coefficient"]),
                "render_sheet": e["render_sheet"],
                "render_row": e["render_row"],
                "value": val,
                "value_status": fact["value_status"] if fact else None,
                "source": fact["source"] if fact else None,
                "evidence": fact["evidence"] if fact else None,
            })

        parent_fact = conn.execute(
            "SELECT value, value_status FROM run_concept_facts "
            "WHERE run_id = ? AND concept_uuid = ? AND period = ? "
            "AND entity_scope = ?",
            (run_id, cu, period, entity_scope),
        ).fetchone()

        return {
            "found": True,
            "concept": {
                "concept_uuid": cu,
                "kind": concept["kind"],
                "label": concept["canonical_label"],
                "render_sheet": concept["render_sheet"],
                "render_row": concept["render_row"],
                "render_col": concept["render_col"],
                "aliases": aliases,
            },
            "children": children,
            "children_sum": round(total, 2) if any_numeric else None,
            "parent_value": parent_fact["value"] if parent_fact else None,
        }
    finally:
        conn.close()


def _format_trace(trace: dict[str, Any]) -> str:
    """Render a trace_cascade_source result as readable text for the agent."""
    if not trace["found"]:
        return ("No concept found at that location. Pass a concept_uuid, or a "
                "(sheet, row) that exists in the template.")
    c = trace["concept"]
    lines = [
        f"{c['label']} ({c['kind']}, {c['render_sheet']} row {c['render_row']})",
    ]
    if c["aliases"]:
        coords = ", ".join(f"{a['sheet']}!{a['col']}{a['row']}" for a in c["aliases"])
        lines.append(f"  also renders at (cross-sheet rollup): {coords}")
    lines.append(f"  parent value = {trace['parent_value']}")
    if not trace["children"]:
        lines.append("  (this is a leaf / data-entry cell — no children)")
    else:
        lines.append("  children feeding this total:")
        for ch in trace["children"]:
            lines.append(
                f"    - {ch['label']} ({ch['kind']}, coef "
                f"{ch['coefficient']:+g}) = {ch['value']} "
                f"[{ch['value_status']}] uuid={ch['concept_uuid']}"
            )
        lines.append(f"  children signed sum = {trace['children_sum']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 5b — list_run_facts: HOLISTIC sight across every filled statement.
#
# `read_facts`/`trace_cascade_source` are point lookups — they need a
# concept_uuid or (sheet, row) you already suspect. The holistic auditor
# (docs/PLAN-reviewer-holistic-audit.md, Phase 1) instead needs to *browse*
# the whole filled run to spot a value duplicated across rows/sheets (the
# over-count failure mode) or sitting on the wrong statement. This is the
# read surface that makes "look at all the statements" possible.
# ---------------------------------------------------------------------------


def list_run_facts(
    db_path: str | Path,
    run_id: int,
    *,
    sheet: Optional[str] = None,
    template_prefix: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Enumerate every fact written for a run, joined to its concept.

    Family-scoped via ``template_prefix`` (same reason as
    :func:`_resolve_concept` — ``concept_nodes`` holds all four
    standard×level families and the join would otherwise pull facts whose
    concept happens to share a uuid across families; in practice facts are
    keyed to one family's uuids, but the scope keeps the listing honest).
    Pass ``sheet`` to narrow to one render sheet. Read-only.
    """
    conn = _open_conn(db_path)
    try:
        sql = (
            "SELECT n.render_sheet, n.render_row, n.canonical_label, n.kind, "
            "f.concept_uuid, f.period, f.entity_scope, f.value, "
            "f.value_status, f.source, f.evidence "
            "FROM run_concept_facts f "
            "JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
            "WHERE f.run_id = ?"
        )
        params: list[Any] = [run_id]
        if template_prefix:
            sql += " AND n.template_id LIKE ?"
            params.append(f"{template_prefix}%")
        if sheet:
            sql += " AND n.render_sheet = ?"
            params.append(sheet)
        sql += (
            " ORDER BY n.render_sheet, n.render_row, f.period, f.entity_scope"
        )
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _normalize_for_match(label: str) -> str:
    """Lowercase + collapse whitespace for fuzzy label matching (item 25)."""
    return " ".join((label or "").strip().lstrip("*").lower().split())


def find_candidate_rows(
    db_path: str | Path,
    run_id: int,
    *,
    value: float,
    label_hint: str = "",
    template_prefix: Optional[str] = None,
    entity_scope: Optional[str] = None,
    tolerance: float = 1.0,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Reverse-lookup: given a PDF figure, which template rows could it be?

    The reviewer can trace a row DOWN to its source, but not the inverse —
    "I see 1,595k on page 30, where should it live?". This matches the run's
    facts by value (within ``tolerance``, ±1 mirrors the verifier convention)
    and/or by a fuzzy label match against ``concept_nodes.canonical_label``.

    Family-scoped via ``template_prefix`` (gotcha #21 — the SAME (sheet, row)
    exists under each standard×level with a different uuid, so an unscoped
    lookup resolves an arbitrary template's concept). On a Group filing pass
    ``entity_scope`` to narrow to the relevant column. Returns ≤``limit``
    candidates, value-matches first. Read-only.
    """
    import difflib

    conn = _open_conn(db_path)
    try:
        sql = (
            "SELECT n.render_sheet, n.render_row, n.canonical_label, n.kind, "
            "f.concept_uuid, f.period, f.entity_scope, f.value, f.value_status "
            "FROM run_concept_facts f "
            "JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
            "WHERE f.run_id = ?"
        )
        params: list[Any] = [run_id]
        if template_prefix:
            sql += " AND n.template_id LIKE ?"
            params.append(f"{template_prefix}%")
        if entity_scope:
            sql += " AND f.entity_scope = ?"
            params.append(entity_scope)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    hint = _normalize_for_match(label_hint)
    scored: list[tuple[int, float, dict[str, Any]]] = []
    for r in rows:
        v = r["value"]
        value_match = (
            isinstance(v, (int, float))
            and abs(float(v) - float(value)) <= tolerance
        )
        label_ratio = 0.0
        if hint:
            lab = _normalize_for_match(r["canonical_label"])
            if hint in lab or lab in hint:
                label_ratio = 1.0
            else:
                label_ratio = difflib.SequenceMatcher(None, hint, lab).ratio()
        label_match = label_ratio >= 0.6
        if not (value_match or label_match):
            continue
        # Rank: a value match is the strongest signal (tier 0); a label-only
        # match is tier 1, ordered by descending label similarity.
        tier = 0 if value_match else 1
        scored.append((tier, -label_ratio, {
            "sheet": r["render_sheet"], "row": r["render_row"],
            "label": r["canonical_label"], "kind": r["kind"],
            "concept_uuid": r["concept_uuid"], "period": r["period"],
            "entity_scope": r["entity_scope"], "current_value": r["value"],
            "value_status": r["value_status"],
        }))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in scored[:limit]]


def _repeated_values(facts: Sequence[dict[str, Any]]) -> dict[float, list[str]]:
    """Group non-zero numeric LEAF values that appear on >1 distinct (sheet,row).

    A value disclosed once in the PDF but written to several rows/sheets is
    the signature of a double/triple-count (run 153's FVTPL written 3×). We
    surface these so the reviewer interrogates them — it's advisory, not a
    verdict.

    Restricted to ``kind == "LEAF"`` deliberately: the genuine double-count
    is a leaf written twice, whereas the legitimate cross-statement equalities
    the cross-checks assert (Total equity = SOCIE equity-at-end, SOPL profit =
    SOCIE profit, SOCF cash = SOFP cash, …) are equal COMPUTED totals on two
    sheets. Including totals would flag every balanced pair as a "double-count"
    and bury the real signal — exactly the noise that wastes the reviewer's
    turn budget and tempts an ungrounded blank.
    """
    seen: dict[float, set[str]] = {}
    for f in facts:
        if (f.get("kind") or "").upper() != "LEAF":
            continue
        val = f.get("value")
        if val is None or float(val) == 0.0:
            continue
        coord = f"{f['render_sheet']}!row{f['render_row']} ({f['canonical_label']})"
        seen.setdefault(float(val), set()).add(coord)
    return {v: sorted(c) for v, c in seen.items() if len(c) > 1}


def _format_fact_listing(facts: Sequence[dict[str, Any]]) -> str:
    """Render :func:`list_run_facts` output as a compact scannable table."""
    if not facts:
        return "(no facts written for this run yet)"
    lines: list[str] = []
    current_sheet = None
    for f in facts:
        if f["render_sheet"] != current_sheet:
            current_sheet = f["render_sheet"]
            lines.append(f"\n=== {current_sheet} ===")
        lines.append(
            f"  row {f['render_row']:>3} {f['canonical_label']!r} "
            f"[{f['kind']}] {f['period']}/{f['entity_scope']}: "
            f"{f['value']} ({f['value_status']}) uuid={f['concept_uuid']}"
        )
    repeats = _repeated_values(facts)
    if repeats:
        lines.append(
            "\n⚠ Repeated values (a value disclosed once but written to "
            "several rows is a possible double-count — investigate each):"
        )
        for val, coords in sorted(repeats.items()):
            lines.append(f"  {val} appears at: {'; '.join(coords)}")
    return "\n".join(lines).lstrip("\n")


# ---------------------------------------------------------------------------
# Step 6 — apply_fix: the only write path, with a deterministic no-plug guard.
# ---------------------------------------------------------------------------


# Catch-all / residual rows agents must never plug to force a balance
# (gotcha #17). Detection is on the canonical label, mirroring the wording
# the no-residual-plug prompt rule names.
_CATCHALL_PREFIXES = ("other ", "miscellaneous", "sundry ")
_CATCHALL_EXACT = {"other", "others", "administrative expenses", "miscellaneous"}


def _is_catchall_label(label: str | None) -> bool:
    """True when a label names a catch-all / residual row (gotcha #17)."""
    if not label:
        return False
    norm = label.strip().lstrip("*").strip().lower()
    if norm in _CATCHALL_EXACT:
        return True
    return any(norm.startswith(p) for p in _CATCHALL_PREFIXES)


def _is_arithmetic_only_evidence(evidence: str | None) -> bool:
    """True when the grounding is a pure arithmetic reconciliation, not a PDF cite.

    The prompt asks the agent to write ``evidence="arithmetic: <expr>"`` when a
    value is derived by reconciling already-grounded cells rather than read off
    the PDF. That marker is exactly the shape of a *residual plug* — so it is
    the signal the no-plug guard keys on for catch-all rows (gotcha #17). A
    write that cites a real page (``"page 30: Other receivables 5,000"``) is
    NOT arithmetic-only and is a legitimate grounded fix.
    """
    if not evidence:
        return False
    return evidence.strip().lower().startswith("arithmetic")


# Machine-readable rejection kinds (item 14 — apply_fix rejection telemetry).
# These ride into ``outcome["fix_rejections"]`` so a pass reports how many
# fixes it refused and why, queryable from the re-review status endpoint.
#   ungrounded       — no PDF/arithmetic grounding
#   abstract_row     — write to an ABSTRACT section header (gotcha #17)
#   catchall_plug    — arithmetic residual plugged into a catch-all row (#17)
#   computed_override — bare observed write to a formula concept (facts_api)
REJECTION_KINDS = (
    "ungrounded", "abstract_row", "catchall_plug", "computed_override",
)


def classify_apply_fix_guard(
    concept: sqlite3.Row | dict,
    *,
    evidence: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Run the no-plug guard, returning ``(kind, message)``.

    ``kind`` is one of :data:`REJECTION_KINDS` (the machine-readable tag for
    telemetry); ``message`` is the same model-facing ``"rejected: …"`` string
    the guard has always produced. Both are ``None`` when the write passes.
    The message text is the model-facing contract — do not change it without
    updating the guard-behaviour tests.
    """
    kind = concept["kind"]
    label = concept["canonical_label"]
    sheet = concept["render_sheet"]
    row = concept["render_row"]

    if not (evidence and str(evidence).strip()):
        return "ungrounded", (
            "rejected: ungrounded write refused — cite the PDF page + the "
            "figure you read in `evidence` (or 'arithmetic: <expr>' when the "
            "value is a pure reconciliation of already-grounded cells). The "
            "reviewer never writes a number it can't ground."
        )

    if kind == "ABSTRACT":
        return "abstract_row", (
            f"rejected: {sheet} row {row} ({label!r}) is an ABSTRACT section "
            f"header — never writable (invariant #17). Write a leaf row "
            f"inside the section instead."
        )

    if _is_catchall_label(label) and _is_arithmetic_only_evidence(evidence):
        return "catchall_plug", (
            f"rejected: {sheet} row {row} ({label!r}) is a catch-all / "
            f"residual row, and an arithmetic-only value is a balancing plug. "
            f"Never plug a residual into a catch-all to force a balance "
            f"(invariant #17). Fix the real leaf, or leave the imbalance "
            f"flagged. (A PDF-cited disclosure on this row is fine — cite the "
            f"page instead of an arithmetic expression.)"
        )

    return None, None


def evaluate_apply_fix_guard(
    concept: sqlite3.Row | dict,
    *,
    evidence: Optional[str],
) -> Optional[str]:
    """Deterministic no-plug guard. Returns a rejection string or None.

    Refuses the write when:

    (a) **No grounding** — ``evidence`` is empty. Every reviewer fix must
        cite the PDF page + quote it read, or carry the ``arithmetic``
        marker (a non-empty evidence string is enough; the prompt asks the
        agent to write ``arithmetic: …`` when the fix is a pure
        reconciliation derived from already-grounded cells).
    (b) **Plug into an abstract row** — the target is an ABSTRACT section
        header (never writable, gotcha #17).
    (c) **Arithmetic plug into a catch-all row** — the target is a
        catch-all "Other …" / "Miscellaneous" / "Administrative expenses"
        row AND the grounding is the arithmetic-reconciliation marker (a
        balancing residual). A PDF-cited write to such a row is a genuine
        disclosure ("Other receivables 5,000" off page 30) and is allowed —
        only the residual *plug* is refused, so the reviewer can still fix
        a real leaf that happens to be named "Other …".

    The guard runs BEFORE ``apply_fact`` and returns the same
    ``"rejected: …"`` shape the facts API produces, so the agent reads one
    consistent failure contract and re-investigates. Thin wrapper over
    :func:`classify_apply_fix_guard` (item 14) — it drops the telemetry kind.
    """
    return classify_apply_fix_guard(concept, evidence=evidence)[1]


def _tally_rejection(rejections: Optional[dict], kind: str, label: Optional[str]) -> None:
    """Bump a per-kind rejection counter + log one WARN (item 14).

    ``rejections`` is the dict carried on ``ReviewerDeps``; ``None`` (the
    pure-function unit-test call shape) skips telemetry. The WARN makes a
    refused fix visible in logs even when nobody reads the outcome dict.
    """
    logger.warning("apply_fix rejected (%s) for concept %r", kind, label)
    if rejections is None:
        return
    rejections[kind] = rejections.get(kind, 0) + 1


def apply_reviewer_fix(
    db_path: str | Path,
    run_id: int,
    fact,
    *,
    template_prefix: Optional[str] = None,
    rejections: Optional[dict] = None,
) -> str:
    """Run the no-plug guard, then write one reviewer fix through apply_fact.

    ``fact`` is a ``FactWrite`` (actor should be ``"reviewer"``). Returns a
    short status string for the agent: ``ok: …`` on success, ``rejected: …``
    when the guard or the facts API refuses. Never raises — a rejection is
    reported so the agent can pick a different target.

    ``template_prefix`` scopes the write to the run's template family
    (``"{standard}-{level}-"``). ``concept_uuid`` is a globally-unique PK, so
    the lookup always finds the *right* concept — but ``concept_nodes`` holds
    every imported standard×level (gotcha #21), and a write to an off-family
    concept lands an unrenderable ``run_concept_facts`` row that the exporter
    silently drops (cascade noise + a confusing diff). So we mirror the read
    path (:func:`_resolve_concept`) and refuse a ``concept_uuid`` whose
    ``template_id`` doesn't belong to this run's family. The prefix is
    optional so the pure-function unit tests (single-template DBs) keep their
    terse calls.

    Does NOT cascade — the pass recomputes once after the agent finishes
    (Step 9), so individual writes stay cheap.
    """
    from fastapi import HTTPException
    from concept_model.facts_api import apply_fact

    conn = _open_conn(db_path)
    try:
        if not fact.concept_uuid:
            return "rejected: a reviewer fix requires a concept_uuid."
        concept = conn.execute(
            "SELECT concept_uuid, kind, canonical_label, render_sheet, "
            "render_row, template_id FROM concept_nodes WHERE concept_uuid = ?",
            (fact.concept_uuid,),
        ).fetchone()
        if concept is None:
            return f"rejected: unknown concept_uuid {fact.concept_uuid!r}."

        # Template-family scope FIRST — refuse an off-family concept_uuid so a
        # hallucinated id can't write an unrenderable fact row (mirrors the
        # read path's template_prefix scoping; gotcha #21).
        if template_prefix and not str(
            concept["template_id"] or ""
        ).startswith(template_prefix):
            return (
                f"rejected: concept {fact.concept_uuid!r} belongs to template "
                f"{concept['template_id']!r}, not this run's family "
                f"({template_prefix!r}). Trace the failing cell in THIS run "
                f"with trace_cascade_source_tool to get the right concept_uuid."
            )

        # Deterministic guard NEXT — invariant #17 + grounding. Use the
        # classifier so a refusal is tallied per kind for telemetry (item 14).
        kind, rejection = classify_apply_fix_guard(concept, evidence=fact.evidence)
        if rejection is not None:
            _tally_rejection(rejections, kind, concept["canonical_label"])
            return rejection

        apply_fact(conn, run_id, fact)  # commits on success

        # The reviewer acted on this concept — close any open conflict rows
        # for it so the reconciliation queue reflects the resolution.
        if fact.concept_uuid:
            conn.execute(
                "UPDATE run_concept_conflicts SET status = 'resolved', "
                "resolved_at = ? WHERE run_id = ? AND concept_uuid = ? "
                "AND status = 'open'",
                (_now(), run_id, fact.concept_uuid),
            )
            conn.commit()
        return (
            f"ok: {fact.value_status} "
            f"{fact.value if fact.value is not None else ''} on "
            f"{fact.concept_uuid} ({fact.period}/{fact.entity_scope})"
        ).strip()
    except HTTPException as exc:
        conn.rollback()
        # facts_api refuses a bare observed write to a formula concept
        # (COMPUTED / matrix total) — tally it as computed_override (item 14).
        detail = str(exc.detail or "")
        if "formula concept" in detail:
            _tally_rejection(
                rejections, "computed_override", concept["canonical_label"]
            )
        return f"rejected: {exc.detail}"
    except Exception as exc:  # noqa: BLE001 — report, don't crash the loop
        conn.rollback()
        return f"error: {type(exc).__name__}: {exc}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Step 7 — raise_flag: the narrow user-facing "needs attention" list.
# ---------------------------------------------------------------------------


_FLAG_CATEGORIES = {"stuck", "disputes_prior"}


def raise_reviewer_flag(
    db_path: str | Path,
    run_id: int,
    *,
    category: str,
    reasoning: str,
    concept_uuid: Optional[str] = None,
    target_sheet: Optional[str] = None,
    target_row: Optional[int] = None,
    pdf_page: Optional[int] = None,
    applied_fix: Optional[str] = None,
) -> str:
    """Insert one row into ``reviewer_flags``. Returns a status string.

    ``category`` must be ``stuck`` (can't reconcile/ground) or
    ``disputes_prior`` (believes an earlier agent erred). ``applied_fix``
    links to a change the reviewer made alongside a dispute (set when it
    both fixed and flagged). Returns ``ok: flag <id> …`` or ``rejected: …``.
    """
    if category not in _FLAG_CATEGORIES:
        return (
            f"rejected: category must be one of {sorted(_FLAG_CATEGORIES)}, "
            f"got {category!r}."
        )
    if not (reasoning and reasoning.strip()):
        return "rejected: a flag requires a non-empty reasoning."

    conn = _open_conn(db_path)
    try:
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO reviewer_flags(
                run_id, concept_uuid, target_sheet, target_row, category,
                reasoning, pdf_page, applied_fix, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                run_id, concept_uuid, target_sheet,
                int(target_row) if target_row is not None else None,
                category, reasoning.strip(),
                int(pdf_page) if pdf_page is not None else None,
                applied_fix, now, now,
            ),
        )
        conn.commit()
        return f"ok: flag {cur.lastrowid} raised ({category})"
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return f"error: {type(exc).__name__}: {exc}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Step 8 — agent factory + prompt + turn cap.
# ---------------------------------------------------------------------------


@dataclass
class ReviewerDeps:
    """Run context carried through the reviewer agent's tools."""
    db_path: str
    run_id: int
    filing_level: str = "company"
    filing_standard: str = "mfrs"
    pdf_path: Optional[str] = None
    # Counters surfaced to the orchestrator so it knows whether to re-export.
    writes_performed: int = 0
    flags_raised: int = 0
    # Item 14: per-kind apply_fix rejection tally (REJECTION_KINDS), surfaced as
    # outcome["fix_rejections"] so a pass reports how many fixes it refused +
    # why — without softening any guard. Empty until the first refusal.
    rejections: dict = field(default_factory=dict)
    # The cross-check names that were FAILING when this pass started. The
    # ``verify_fixes`` tool uses this to tell the reviewer which post-fix
    # failures it was asked to resolve (STILL FAILING) versus a failure it
    # newly INTRODUCED (was green before its edits) — the regression the
    # one-shot pass used to ship silently.
    original_failed_names: set = field(default_factory=set)
    # The run's succeeded statements as ``(statement_type_value, variant)``
    # pairs. ``verify_fixes`` passes this straight to ``run_verification_checks``
    # so the self-verifier scopes the cross-checks off the SAME in-memory
    # succeeded set the pipeline's cross-check pass uses. The INLINE reviewer
    # runs BEFORE the extraction ``run_agents`` rows are finalized to
    # 'succeeded' in the DB, so a DB-status scope would see zero statements and
    # silently verify nothing (the run-58 false "all 0 PASS"). None → the
    # manual /re-review path, where the DB rows ARE terminal, so it falls back
    # to the DB read.
    verify_scope: Optional[list] = None


def _family_prefix(filing_standard: str, filing_level: str) -> str:
    """The ``concept_nodes.template_id`` prefix for a run's template family.

    Template ids are ``{standard}-{level}-{slug}-v1`` (parser._derive_template_id),
    so ``"mfrs-group-"`` scopes a (sheet, row) lookup to exactly that family.
    """
    return f"{(filing_standard or 'mfrs').lower()}-{(filing_level or 'company').lower()}-"


def render_reviewer_prompt(
    *,
    db_path: str | Path,
    run_id: int,
    failed_checks: Optional[Sequence[dict[str, Any]]] = None,
    conflicts: Optional[Sequence[dict[str, Any]]] = None,
    guidance: Optional[str] = None,
    filing_level: str = "company",
    filing_standard: str = "mfrs",
    spot_check_mode: Optional[str] = None,
) -> str:
    """Compose the reviewer system prompt.

    Body comes from ``prompts/reviewer.md``; we append a structured review
    packet listing the failing cross-checks + open conflicts the reviewer
    should investigate, plus any free-text human guidance from a re-review.
    The packet leads with the filing context (standard + level) so the
    reviewer reads/writes the right ``entity_scope`` on Group filings.

    ``spot_check_mode`` (``"light"`` / ``"full"`` / ``None``) drives the
    clean-run spot-check: when set, there are NO failing checks/conflicts, so
    the packet is replaced with a spot-check framing ("everything passed —
    sanity-check the high-value figures"). ``light`` also swaps the body to the
    tighter ``prompts/spot_check.md``; ``full`` keeps the holistic reviewer body.
    """
    mode = (spot_check_mode or "").lower() or None
    if mode == "light":
        body = _SPOT_CHECK_PROMPT_PATH.read_text(encoding="utf-8").strip()
    else:
        body = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    # Holistic sight (Phase 1): start the reviewer with the whole filled
    # picture so it can spot duplicates/misclassifications across statements,
    # not just the cell a check named. Best-effort — a summary failure must
    # never block the review (the agent can still call list_facts directly).
    fact_summary = ""
    try:
        facts = list_run_facts(
            db_path, run_id,
            template_prefix=_family_prefix(filing_standard, filing_level),
        )
        fact_summary = _format_fact_summary(facts)
    except Exception:  # noqa: BLE001 — summary is advisory, never fatal
        fact_summary = ""
    # Phase 4: pre-compute the cascade trace for each failing check's target
    # cell and inline it in the packet. The reviewer's biggest budget sink is
    # rediscovering — via 2-3 trace_cascade_source_tool round-trips — what the
    # server already knows (the children feeding a failing total + their signed
    # sum). Computing it once here lets the reviewer go straight to the PDF /
    # the fix. Best-effort and per-check guarded: a trace failure yields "".
    if mode is not None:
        # Clean-run spot-check: no failing checks / conflicts to inline. The
        # packet just orients the reviewer with the filing context + the
        # fact summary and tells it this is a sanity pass.
        packet = _format_spot_check_packet(
            guidance, filing_level=filing_level,
            filing_standard=filing_standard, fact_summary=fact_summary,
            mode=mode,
        )
        return f"{body}\n\n{packet}"
    prefix = _family_prefix(filing_standard, filing_level)
    check_traces: list[str] = []
    for c in (failed_checks or []):
        try:
            check_traces.append(_trace_for_check(db_path, run_id, c, template_prefix=prefix))
        except Exception:  # noqa: BLE001 — pre-computed trace is advisory
            check_traces.append("")
    packet = _format_review_packet(
        failed_checks or [], conflicts or [], guidance,
        filing_level=filing_level, filing_standard=filing_standard,
        fact_summary=fact_summary, check_traces=check_traces,
    )
    return f"{body}\n\n{packet}"


def _format_spot_check_packet(
    guidance: Optional[str],
    *,
    filing_level: str,
    filing_standard: str,
    fact_summary: str,
    mode: str,
) -> str:
    """Render the clean-run spot-check packet (no failing checks/conflicts).

    Mirrors the head of ``_format_review_packet`` — filing context first so the
    reviewer reads/writes the right ``entity_scope`` on Group filings — then the
    WHAT WAS FILLED summary and a short "everything passed; sanity-check the
    high-value figures" instruction in place of the failing-check list.
    """
    std = (filing_standard or "mfrs").upper()
    lvl = (filing_level or "company").capitalize()
    lines = [
        "=== SPOT-CHECK PACKET ===",
        f"Filing: {std} · {lvl}.",
        (
            "All cross-checks PASSED and there are NO open conflicts. This is "
            f"a {'FULL holistic' if mode == 'full' else 'LIGHT'} spot-check — "
            "a sanity pass over the figures cross-checks can't catch (wrong "
            "value against the PDF, flipped sign, 1000× scale slip, "
            "misplacement, balancing double-count)."
        ),
    ]
    if lvl.lower() == "group":
        lines.append(
            "GROUP filing: figures exist under BOTH Group and Company scopes — "
            "name the entity_scope when you read/write."
        )
    lines.append("")
    lines.append("WHAT WAS FILLED:")
    lines.append(fact_summary or "(no facts filled yet)")
    if guidance:
        lines.append("")
        lines.append("HUMAN GUIDANCE (focus your spot-check here):")
        lines.append(guidance.strip())
    return "\n".join(lines)


def _trace_for_check(
    db_path: str | Path,
    run_id: int,
    check: dict[str, Any],
    *,
    template_prefix: Optional[str],
) -> str:
    """Pre-compute the cascade trace(s) for a failing check's target cell(s).

    Resolves the check's ``target_sheet``/``target_row`` (and, failing that, its
    comparand coords) down to the children feeding the total, honouring the
    Group/Company scope tag in the check name. Returns rendered trace text, or
    "" when nothing decomposes (a bare leaf adds nothing the comparand line
    didn't already say). CY only — the common failing dimension; the reviewer
    can still trace PY explicitly via the tool.
    """
    scope = _scope_from_check_name(check.get("name") or check.get("check_name")) or "Company"
    coords: list[tuple[str, int]] = []
    ts, tr = check.get("target_sheet"), check.get("target_row")
    if ts and tr:
        coords.append((ts, int(tr)))
    for cm in (check.get("comparands") or []):
        g = cm if isinstance(cm, dict) else dataclasses.asdict(cm)
        if g.get("sheet") and g.get("row"):
            coords.append((g["sheet"], int(g["row"])))

    seen: set[tuple[str, int]] = set()
    blocks: list[str] = []
    for sheet, row in coords:
        key = (sheet, row)
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > 4:  # keep the packet focused
            break
        try:
            trace = trace_cascade_source(
                db_path, run_id, sheet=sheet, row=row,
                entity_scope=scope, template_prefix=template_prefix,
            )
        except Exception:  # noqa: BLE001 — advisory
            continue
        # Only inline a trace that actually decomposes a total.
        if trace.get("found") and trace.get("children"):
            blocks.append(_format_trace(trace))
    return "\n".join(blocks)


def _scope_from_check_name(name: Optional[str]) -> Optional[str]:
    """Read the Group cross-check scope tag (``… [group]`` / ``… [company]``).

    group_checks.py runs each check twice and rides the scope in the name
    suffix. Returns the canonical ``entity_scope`` (``"Group"`` / ``"Company"``)
    so the packet can tell the reviewer WHICH scope to read + write — the tools
    default to Company, which is wrong for a [group] failure.
    """
    if not name:
        return None
    low = name.lower()
    if low.endswith("[group]"):
        return "Group"
    if low.endswith("[company]"):
        return "Company"
    return None


def _format_fact_summary(facts: Sequence[dict[str, Any]]) -> str:
    """Compact per-sheet roll-up + repeated-value warning for the packet.

    Lighter than :func:`_format_fact_listing` (which the ``list_facts`` tool
    returns in full) — this just orients the reviewer up front: how much was
    filled where, and which values look double-counted.
    """
    if not facts:
        return "(no facts filled yet)"
    by_sheet: dict[str, int] = {}
    for f in facts:
        by_sheet[f["render_sheet"]] = by_sheet.get(f["render_sheet"], 0) + 1
    lines = ["Filled facts by sheet (call list_facts for the detail):"]
    for sheet in sorted(by_sheet):
        lines.append(f"  - {sheet}: {by_sheet[sheet]} facts")
    repeats = _repeated_values(facts)
    if repeats:
        lines.append(
            "Repeated values across rows (a value disclosed once but written "
            "to several rows is a possible double-count — verify each):"
        )
        for val, coords in sorted(repeats.items()):
            lines.append(f"  - {val}: {'; '.join(coords)}")
    return "\n".join(lines)


def _format_review_packet(
    failed_checks: Sequence[dict[str, Any]],
    conflicts: Sequence[dict[str, Any]],
    guidance: Optional[str],
    *,
    filing_level: str = "company",
    filing_standard: str = "mfrs",
    fact_summary: str = "",
    check_traces: Sequence[str] = (),
) -> str:
    is_group = (filing_level or "").lower() == "group"
    lines = ["=== REVIEW PACKET ===", ""]
    lines.append(
        f"Filing: {(filing_standard or 'mfrs').upper()} "
        f"{(filing_level or 'company').capitalize()}."
    )
    if fact_summary:
        lines.append("")
        lines.append("=== WHAT WAS FILLED (whole run) ===")
        lines.append(fact_summary)
    if is_group:
        lines.append(
            "This is a GROUP filing — facts carry BOTH Group and Company "
            "scope. A failing check tagged [group] is about Group-scope facts; "
            "[company] is about Company-scope. Pass the matching `entity_scope` "
            "to trace_cascade_source_tool / apply_fix (the tools default to "
            "Company)."
        )
    lines.append("")
    lines.append("Failing cross-checks to investigate:")
    if failed_checks:
        for i, c in enumerate(failed_checks):
            name = c.get("name") or c.get("check_name")
            scope = _scope_from_check_name(name)
            lines.append(
                f"- {name}: "
                f"expected={c.get('expected')} actual={c.get('actual')} "
                f"diff={c.get('diff')} — {c.get('message')}"
                + (
                    f"  [target: {c.get('target_sheet')} row {c.get('target_row')}]"
                    if c.get("target_sheet") else ""
                )
                + (f"  [use entity_scope='{scope}']" if scope else "")
            )
            # Phase 2: list the values the check compared, so the reviewer has
            # BOTH sides of a mismatch (not just the one cell `target` names).
            # Each comparand may arrive as a dataclass (inline pass) or a dict
            # (decoded from comparands_json on a manual re-review).
            for cm in (c.get("comparands") or []):
                g = cm if isinstance(cm, dict) else dataclasses.asdict(cm)
                where = f"{g.get('sheet')}"
                if g.get("row"):
                    where += f" row {g.get('row')}"
                lines.append(
                    f"    · [{g.get('role')}] {g.get('label')} "
                    f"({g.get('statement') or where}) = {g.get('value')} "
                    f"@ {where}"
                )
            # Phase 4: inline the pre-computed cascade trace for this check's
            # target, so the reviewer doesn't spend turns rediscovering it.
            trace_text = check_traces[i] if i < len(check_traces) else ""
            if trace_text:
                lines.append(
                    "    cascade trace (pre-computed — the children feeding "
                    "this total; no need to call trace_cascade_source_tool for "
                    "the cell named above):"
                )
                for tl in trace_text.splitlines():
                    lines.append(f"      {tl}")
    else:
        lines.append("  (none failing)")
    lines.append("")
    lines.append("Open reconciliation conflicts (cascade-detected):")
    if conflicts:
        for c in conflicts:
            lines.append(
                f"- concept_uuid: {c.get('concept_uuid')} "
                f"({c.get('canonical_label') or 'unknown'}) "
                f"kind={c.get('kind')} residual={c.get('residual')} — "
                f"{c.get('detail')}"
            )
    else:
        lines.append("  (none)")
    if guidance and guidance.strip():
        lines.append("")
        lines.append("=== HUMAN GUIDANCE (from a re-review) ===")
        lines.append(guidance.strip())
    return "\n".join(lines)


def compute_reviewer_turn_cap(*, filing_level: str, n_items: int) -> int:
    """Dynamic turn cap, staying safely below pydantic-ai's silent 50 (gotcha #18).

    Formula: 12 base + 4 if Group + 2 per item to investigate, clamped
    [12, 36]. Raised from the old [10, 30] for the holistic-audit reviewer
    (Phase 3): it now reads the whole filing (`list_facts`) and traces BOTH
    sides of cross-statement mismatches before writing, so it needs more
    read headroom. The 36 ceiling stays below `MAX_AGENT_ITERATIONS` (40),
    which stays below pydantic-ai's silent 50 — pinned by
    test_turn_cap_below_pydantic_50.
    """
    is_group = (filing_level or "").lower() == "group"
    raw = 12 + (4 if is_group else 0) + 2 * int(n_items)
    return max(12, min(36, raw))


def compute_spot_check_turn_cap(*, filing_level: str, mode: str) -> int:
    """Turn cap for the clean-run spot-check (gotcha #18 — stays below 40/50).

    The spot-check fires when NOTHING failed, so there's no failing-check list
    to scale against. ``light`` is a deliberately tight sanity pass over the
    highest-value figures; ``full`` reuses the holistic reviewer budget so the
    deeper audit has the same read headroom it gets on the failure path.
    """
    is_group = (filing_level or "").lower() == "group"
    if (mode or "light").lower() == "full":
        # Same envelope as the reviewer's holistic audit (no failing items to
        # add, so this is the base + group bump).
        return compute_reviewer_turn_cap(filing_level=filing_level, n_items=0)
    # Light: a handful of grounded checks. 6 company / 8 group — enough to
    # sample the face totals + units + a suspected double-count, no more.
    return 8 if is_group else 6


def run_verification_checks(
    db_path: str | Path,
    run_id: int,
    *,
    filing_level: str = "company",
    filing_standard: str = "mfrs",
    recompute: bool = True,
    scope: "Optional[list]" = None,
) -> list:
    """Re-run the cross-check suite against the run's CURRENT facts.

    This is what lets the reviewer CLOSE THE LOOP on its own edits: after it
    applies a fix it can confirm the targeted failure is gone AND that it
    didn't break a previously-passing check (the silent one-shot regression
    this guards against).

    It reads ``run_concept_facts`` directly — the only store that reflects the
    reviewer's in-progress writes (the xlsx isn't re-exported until after the
    pass). ``recompute`` runs the cascade first so leaf edits flow up into the
    COMPUTED totals the balance/tie-out checks read (``apply_reviewer_fix``
    deliberately does NOT cascade per write).

    Scoping mirrors the pipeline's ``_build_check_template_ids`` exactly
    (gotcha #21): succeeded statements + their variants, each mapped to its
    ``template_id`` via the canonical template path, so every check reads its
    own statements' facts and the result matches the post-correction pass the
    user sees. ``scope`` — an explicit iterable of
    ``(statement_type_value, variant)`` pairs — REPLACES the DB
    ``run_agents.status == 'succeeded'`` read when provided. The INLINE
    auto-reviewer / spot-check run BEFORE the extraction rows are finalized to
    'succeeded' in the DB ([server.py] finish_run_agent runs AFTER the reviewer
    pass), so a DB-status scope would see every row still 'running', resolve
    ZERO statements, and return ``[]`` — which the formatter rendered as a
    false "all 0 PASS" (run 58). Passing the same in-memory succeeded set the
    cross-check pass uses fixes that at the source. ``None`` falls back to the
    DB read for the manual /re-review path, where the rows ARE terminal.

    Returns the list of ``CrossCheckResult`` objects (empty if the run has no
    succeeded statements to scope).
    """
    from cross_checks.framework import (
        FactsContext, build_default_cross_checks, run_all_facts,
        DEFAULT_TOLERANCE_RM,
    )
    from statement_types import (
        StatementType, template_path as _tpl_path,
        FACTS_BEARING_AGENT_STATUSES,
    )
    from concept_model.parser import _derive_template_id
    from db import repository as repo

    if recompute:
        from concept_model.cascade import recompute_after_turn
        try:
            recompute_after_turn(db_path, run_id)
        except Exception as exc:  # noqa: BLE001
            # A leaf edit does NOT cascade per write, so without this recompute
            # the COMPUTED totals are stale and the checks would read pre-edit
            # values — the exact false "VERIFIED" this tool exists to prevent
            # (peer-review MEDIUM). BLOCK: raise so verify_fixes surfaces a
            # "could not run", never a green pass on un-propagated totals.
            logger.warning(
                "verify_fixes: cascade recompute failed for run %s", run_id,
                exc_info=True,
            )
            raise RuntimeError(
                f"cascade recompute failed ({type(exc).__name__}); cross-check "
                f"totals would be stale, so verification cannot be trusted"
            ) from exc

    # One connection for both the scoping read and the checks (the checks read
    # facts through the same FactsContext.conn).
    conn = _open_conn(db_path)
    try:
        if scope is not None:
            # Inline reviewer: the caller hands us the run's succeeded
            # statements directly (the extraction rows aren't 'succeeded' in
            # the DB yet at this point in the pipeline — see the docstring).
            rows = [(st, var) for st, var in scope]
        else:
            # Manual /re-review: the DB rows ARE terminal by now. Scope IN
            # every facts-bearing statement — including the
            # `completed_with_errors` (acknowledge_unresolved) saves, which
            # carry real facts the reviewer must verify. A `succeeded`-only
            # filter here would drop them and the verifier would go
            # INCONCLUSIVE over a still-checkable run (matches the recheck +
            # re-export scope; see FACTS_BEARING_AGENT_STATUSES).
            rows = [
                (a.statement_type, a.variant)
                for a in repo.fetch_run_agents(conn, run_id)
                if a.status in FACTS_BEARING_AGENT_STATUSES
            ]

        template_ids: dict = {}
        statements_to_run: set = set()
        variants: dict = {}
        for statement_type, variant in rows:
            try:
                stmt = StatementType(statement_type)
            except ValueError:
                # Pseudo-agent rows (CORRECTION / notes-validator / scout)
                # don't map to a StatementType — skip, as the recheck path does.
                continue
            try:
                master = _tpl_path(
                    stmt, variant, level=filing_level, standard=filing_standard,
                )
            except (ValueError, KeyError):
                continue
            template_ids[stmt] = _derive_template_id(Path(master))
            statements_to_run.add(stmt)
            variants[stmt] = variant

        if not statements_to_run:
            return []

        check_config = {
            "statements_to_run": statements_to_run,
            "variants": variants,
            "filing_level": filing_level,
            "filing_standard": filing_standard,
        }
        tolerance = float(
            os.environ.get("XBRL_TOLERANCE_RM", str(DEFAULT_TOLERANCE_RM))
        )
        ctx = FactsContext(
            conn=conn, run_id=run_id, template_ids=template_ids,
            filing_level=filing_level, filing_standard=filing_standard,
        )
        return run_all_facts(
            build_default_cross_checks(), ctx, check_config, tolerance=tolerance,
        )
    finally:
        conn.close()


def _format_verification(results: list, original_failed_names: set) -> str:
    """Render a compact verification summary for the reviewer.

    Each failing check is tagged so the reviewer knows whether it's a problem
    it was ASKED to fix (``STILL FAILING``) or one its OWN edits introduced
    (``⚠ NEW``) — the regression signal. Passing/advisory checks are summarised
    by count, not listed, to keep the reply short.

    A clean verdict requires POSITIVE confirmation, never the mere ABSENCE of
    failures. An empty result set — or one where every check came back
    ``pending`` / ``not_applicable`` — is reported ``INCONCLUSIVE``, and a run
    whose ORIGINALLY-FAILING checks weren't re-evaluated to ``passed`` is
    reported ``NOT CONFIRMED``. Both used to fall through the ``if not failed``
    branch and print "✓ VERIFIED: all 0 evaluated cross-check(s) PASS" — the
    false green that let the reviewer declare itself done over a still-broken
    run (run 58). The verifier must fail SAFE, never green, when it evaluated
    nothing.
    """
    failed = [r for r in results if getattr(r, "status", None) == "failed"]
    passed = [r for r in results if getattr(r, "status", None) == "passed"]
    warnings = [r for r in results if getattr(r, "status", None) == "warning"]
    original = original_failed_names or set()

    lines: list[str] = []
    if not failed:
        # --- False-green guards (run-58): never report a pass without having
        # actually evaluated checks to a PASS. ---
        if not passed and not warnings:
            # Empty results, or every check pending / not_applicable: nothing
            # was evaluated at all. This is the absence of evidence, not a pass.
            # (A `warning` IS an evaluation, so warning-only falls through.)
            return (
                "⚠ INCONCLUSIVE: verify_fixes evaluated 0 cross-checks (the "
                "suite returned nothing, or every check was pending or not "
                "applicable). This is NOT a verification — do NOT treat the run "
                "as resolved. Re-examine the facts directly; if a targeted "
                "failure remains, keep fixing it, and if you truly cannot "
                "resolve one, raise a flag."
            )
        confirmed = {getattr(r, "name", None) for r in passed}
        unconfirmed = sorted(n for n in original if n not in confirmed)
        if unconfirmed:
            # No check is FAILING, but a check the reviewer was asked to fix
            # was not re-evaluated to a pass (scoped out → pending /
            # not_applicable / advisory / absent). Can't claim it's resolved.
            return (
                "⚠ NOT CONFIRMED: no check is currently failing, but these "
                "targeted check(s) were NOT re-evaluated to a PASS, so they "
                "are not confirmed resolved: " + ", ".join(unconfirmed) + ". "
                "They may have been scoped out (pending / not applicable). Do "
                "not declare done — confirm each targeted failure actually "
                "passes, or raise a flag explaining why it cannot be checked."
            )
        if passed:
            lines.append(
                f"✓ VERIFIED: all {len(passed)} evaluated cross-check(s) PASS "
                f"after your edits — no failure remains and you introduced none."
            )
            if warnings:
                lines.append(
                    f"({len(warnings)} advisory warning(s) — not blocking.)"
                )
        else:
            # Warning-only: nothing FAILED and nothing the reviewer had to fix
            # is unconfirmed, but no check evaluated to a hard PASS either.
            # Report the advisory-only state honestly — never the misleading
            # "all 0 ... PASS" false-green (run-58).
            lines.append(
                f"✓ No cross-check is failing after your edits "
                f"({len(warnings)} advisory warning(s) only — none blocking, "
                f"none evaluated to a hard PASS). You introduced no failure."
            )
        lines.append(
            "If every targeted failure and open conflict is resolved, you are "
            "done. Otherwise keep going."
        )
        return "\n".join(lines)

    introduced = [r for r in failed if getattr(r, "name", None) not in original]
    lines.append(
        f"{len(failed)} cross-check(s) STILL FAILING after your edits "
        f"({len(introduced)} of them NEW — introduced by your edits):"
    )
    for r in failed:
        tag = (
            "⚠ NEW — your edit caused this; it was PASSING before. Reconsider "
            "(revise or revert) the edit that broke it"
            if getattr(r, "name", None) not in original
            else "still failing — your fix has not resolved it yet"
        )
        detail = getattr(r, "message", None) or (
            f"expected {getattr(r, 'expected', None)} vs "
            f"actual {getattr(r, 'actual', None)} (diff {getattr(r, 'diff', None)})"
        )
        lines.append(f"  - [{getattr(r, 'name', '?')}] {tag}. {detail}")
    if introduced:
        lines.append(
            "A NEW failure means a fix you made was wrong — go back and "
            "reconsider it before you finish. Do not leave the run worse than "
            "you found it."
        )
    return "\n".join(lines)


def create_reviewer_agent(
    *,
    model,
    db_path: str | Path,
    run_id: int,
    filing_level: str = "company",
    filing_standard: str = "mfrs",
    pdf_path: str | Path | None = None,
    failed_checks: Optional[Sequence[dict[str, Any]]] = None,
    conflicts: Optional[Sequence[dict[str, Any]]] = None,
    guidance: Optional[str] = None,
    spot_check_mode: Optional[str] = None,
    verify_scope: Optional[list] = None,
):
    """Build the reviewer agent. Returns ``(agent, deps)``.

    Read tools: ``read_facts``, ``trace_cascade_source``, ``view_pdf_pages``,
    ``calculator``. Write tools: ``apply_fix`` (guarded), ``raise_flag``.
    The system prompt carries the review packet from
    :func:`render_reviewer_prompt`.

    ``spot_check_mode`` (``"light"`` / ``"full"`` / ``None``) selects the
    clean-run spot-check framing — see :func:`render_reviewer_prompt`. The
    registered toolset is identical either way; only the system prompt changes.

    ``verify_scope`` — the run's succeeded statements as
    ``(statement_type_value, variant)`` pairs — is carried onto
    ``ReviewerDeps`` so ``verify_fixes`` scopes its cross-checks off the SAME
    in-memory set the pipeline used. The inline reviewer MUST pass this (the
    extraction ``run_agents`` rows aren't 'succeeded' in the DB yet); manual
    /re-review passes ``None`` and lets ``run_verification_checks`` read the
    (by-then terminal) DB rows. See :func:`run_verification_checks`.
    """
    from model_settings import build_model_settings
    from concept_model.facts_api import FactWrite

    deps = ReviewerDeps(
        db_path=str(db_path), run_id=run_id, filing_level=filing_level,
        filing_standard=filing_standard,
        pdf_path=str(pdf_path) if pdf_path is not None else None,
        # Seed the baseline so verify_fixes can distinguish a failure the
        # reviewer was asked to fix from one it newly introduced.
        original_failed_names={
            c.get("name") for c in (failed_checks or []) if c.get("name")
        },
        # Inline pass hands us the succeeded-statement scope explicitly so
        # verify_fixes doesn't read an un-finalized 'running' DB (run-58 fix).
        verify_scope=list(verify_scope) if verify_scope is not None else None,
    )
    system_prompt = render_reviewer_prompt(
        db_path=db_path, run_id=run_id, failed_checks=failed_checks,
        conflicts=conflicts, guidance=guidance,
        filing_level=filing_level, filing_standard=filing_standard,
        spot_check_mode=spot_check_mode,
    )
    # Fix B (2026-06-20): steer the reviewer off search_pdf_text on a fully
    # scanned PDF (no text layer) — it can only return a 'scanned' signal.
    # No-op on text / hybrid PDFs; the tool stays registered (reviewer.md
    # names it).
    from tools.pdf_search import scanned_pdf_advisory
    system_prompt += scanned_pdf_advisory(deps.pdf_path)
    # Temperature pinned to 1.0 — Gemini 3 through the enterprise proxy
    # requires it (mirrors extraction + notes agents). Phase 2: provider-correct
    # prompt caching of the static reviewer.md body + tool defs.
    agent = Agent(
        model,
        deps_type=ReviewerDeps,
        system_prompt=system_prompt,
        model_settings=build_model_settings(model, cache_key="xbrl-reviewer"),
    )

    @agent.tool
    def calculator(ctx: RunContext[ReviewerDeps], expression: str) -> str:
        """Evaluate arithmetic exactly before writing a corrected fact.

        Supports numbers, parentheses, unary signs, and + - * /.
        """
        # Single-expression by design: only the extraction agent batches
        # (Plan D), which runs many checks per turn. The reviewer verifies one
        # fix at a time, so the simpler signature stays.
        return _calculator_impl(expression)

    @agent.tool
    def lookup_definitions(ctx: RunContext[ReviewerDeps], queries: list[str]) -> str:
        """Look up the OFFICIAL SSM concept definition(s) for one or more terms.

        Use this when auditing whether a value sits on the right concept — e.g.
        to confirm "Accruals" vs "Other current non-trade payables", or
        "Deferred income" vs "Contract liabilities" — so a correction is
        grounded in the taxonomy, not a guess. Pass all the terms to compare in
        ONE call. Scoped automatically to this run's filing standard.
        """
        return _lookup_definitions_impl(queries, ctx.deps.filing_standard)

    @agent.tool
    def read_facts(
        ctx: RunContext[ReviewerDeps],
        concept_uuid: str,
    ) -> str:
        """Read a concept's current facts across periods/scopes (read-only).

        Returns each (period, scope) fact's value, status, source and
        evidence so you can see what the extraction agent wrote and how it
        grounded it before you change anything.
        """
        conn = _open_conn(ctx.deps.db_path)
        try:
            node = conn.execute(
                "SELECT canonical_label, kind, render_sheet, render_row "
                "FROM concept_nodes WHERE concept_uuid = ?",
                (concept_uuid,),
            ).fetchone()
            if node is None:
                return f"Unknown concept_uuid {concept_uuid!r}."
            facts = conn.execute(
                "SELECT period, entity_scope, value, value_status, "
                "children_status, source, evidence FROM run_concept_facts "
                "WHERE run_id = ? AND concept_uuid = ?",
                (ctx.deps.run_id, concept_uuid),
            ).fetchall()
        finally:
            conn.close()
        lines = [
            f"{node['canonical_label']} ({node['kind']}, "
            f"{node['render_sheet']} row {node['render_row']})",
        ]
        if not facts:
            lines.append("  (no fact written yet)")
        for f in facts:
            lines.append(
                f"  - {f['period']}/{f['entity_scope']}: value={f['value']} "
                f"status={f['value_status']} children={f['children_status']} "
                f"source={f['source']!r} evidence={f['evidence']!r}"
            )
        return "\n".join(lines)

    @agent.tool
    def list_facts(ctx: RunContext[ReviewerDeps], sheet: str = "") -> str:
        """List EVERY fact filled across all statements (read-only, holistic).

        Pass an empty ``sheet`` to see the whole run, or a sheet name (e.g.
        'SOFP-Sub-OrdOfLiq') to narrow. Use this to audit the full picture
        before fixing anything: a value disclosed once in the PDF but written
        to several rows/sheets is a double-count, and a figure sitting on the
        wrong statement is a misclassification. A '⚠ Repeated values' footer
        highlights the likely double-counts for you to interrogate.
        """
        facts = list_run_facts(
            ctx.deps.db_path, ctx.deps.run_id,
            sheet=sheet or None,
            template_prefix=_family_prefix(
                ctx.deps.filing_standard, ctx.deps.filing_level),
        )
        return _format_fact_listing(facts)

    # Registered under the model-facing name ``find_candidate_rows`` (the name
    # prompts/reviewer.md advertises) while the Python identifier differs —
    # a same-named wrapper would shadow the module-level helper and recurse
    # into the tool itself (TypeError on every live call).
    @agent.tool(name="find_candidate_rows")
    def find_candidate_rows_tool(
        ctx: RunContext[ReviewerDeps],
        value: float,
        label_hint: str = "",
        entity_scope: str = "",
    ) -> str:
        """Reverse-lookup: given a figure (and optional label), which rows is it?

        Use when you've read a number in the PDF and need to know where it
        belongs — the inverse of trace_cascade_source_tool. Matches the run's
        facts by value (±1) and/or a fuzzy label match. On a GROUP filing pass
        ``entity_scope`` ('Group' | 'Company') to narrow to the right column.
        Returns up to 10 candidates with their sheet, row, label, current value,
        and concept_uuid — scoped to THIS run's template family. Verify the
        right one in the PDF before apply_fix.
        """
        cands = find_candidate_rows(
            ctx.deps.db_path, ctx.deps.run_id,
            value=value, label_hint=label_hint or "",
            template_prefix=_family_prefix(
                ctx.deps.filing_standard, ctx.deps.filing_level),
            entity_scope=entity_scope or None,
        )
        if not cands:
            return (
                f"No candidate rows matched value≈{value}"
                + (f" / label~{label_hint!r}" if label_hint else "")
                + ". Try a wider label hint, or trace down from a known total."
            )
        lines = [f"{len(cands)} candidate(s) for value≈{value}"
                 + (f" / label~{label_hint!r}" if label_hint else "") + ":"]
        for c in cands:
            lines.append(
                f"  {c['sheet']}!row{c['row']} {c['label']!r} [{c['kind']}] "
                f"{c['period']}/{c['entity_scope']}: {c['current_value']} "
                f"uuid={c['concept_uuid']}"
            )
        return "\n".join(lines)

    @agent.tool
    def trace_cascade_source_tool(
        ctx: RunContext[ReviewerDeps],
        concept_uuid: str = "",
        sheet: str = "",
        row: int = 0,
        period: str = "CY",
        entity_scope: str = "Company",
    ) -> str:
        """Walk DOWN from a failing face cell to the total + children feeding it.

        Pass either a concept_uuid OR a (sheet, row) face coordinate. Returns
        the source concept (the sub-sheet total when the face row rolls up
        cross-sheet), each child summand with its signed coefficient and
        current value, and the children's signed sum — so you can find WHICH
        leaf is wrong instead of guessing.
        """
        trace = trace_cascade_source(
            ctx.deps.db_path, ctx.deps.run_id,
            concept_uuid=concept_uuid or None,
            sheet=sheet or None,
            row=row or None,
            period=period, entity_scope=entity_scope,
            template_prefix=_family_prefix(
                ctx.deps.filing_standard, ctx.deps.filing_level),
        )
        return _format_trace(trace)

    @agent.tool
    def view_pdf_pages(ctx: RunContext[ReviewerDeps], pages: list[int]):
        """View source PDF pages as images (read-only) to ground a fix.

        Pass page numbers, e.g. [12, 13]. Always cite the page you used in
        the ``evidence`` arg of apply_fix — ungrounded writes are refused.
        """
        from pydantic_ai import BinaryContent
        from concurrent.futures import ThreadPoolExecutor
        from extraction.agent import _render_single_page
        from tools.pdf_viewer import count_pdf_pages

        if not ctx.deps.pdf_path:
            return ["Source PDF is not available for this run."]
        total = count_pdf_pages(ctx.deps.pdf_path)
        requested = [p for p in pages if isinstance(p, int)]
        invalid = sorted({p for p in requested if p < 1 or p > total})
        render_pages = sorted({p for p in requested if 1 <= p <= total})
        results: list = []
        if invalid:
            results.append(
                f"Skipped invalid page(s) {invalid}. Valid range is 1-{total}."
            )
        if not render_pages:
            results.append("No pages were rendered.")
            return results
        rendered: dict[int, bytes] = {}
        with ThreadPoolExecutor(max_workers=min(len(render_pages), 8)) as pool:
            futures = {
                pool.submit(_render_single_page, ctx.deps.pdf_path, p): p
                for p in render_pages
            }
            for future in futures:
                page_num, png = future.result()
                rendered[page_num] = png
        for p in sorted(rendered):
            results.append(f"=== Page {p} ===")
            results.append(BinaryContent(data=rendered[p], media_type="image/png"))
        return results

    @agent.tool
    def search_pdf_text(ctx: RunContext[ReviewerDeps], queries: list[str]) -> str:
        """Find where phrase(s) appear in the PDF text, then VERIFY by viewing.

        When verifying a disputed figure's source, pass ALL the phrases in ONE
        call (e.g. ``["Total PPE", "amounts owing by directors"]``). Returns,
        per phrase, the PDF page numbers + a snippet of each case-insensitive
        hit. Use it to locate candidate pages fast, then view_pdf_pages to read
        and confirm before apply_fix — a text hit is a pointer, not proof. On a
        scanned PDF it says so explicitly.
        """
        from tools.pdf_search import search_pdf_text_json
        if not ctx.deps.pdf_path:
            return '{"error": "Source PDF is not available for this run.", "results": []}'
        return search_pdf_text_json(ctx.deps.pdf_path, queries)

    @agent.tool
    def apply_fix(
        ctx: RunContext[ReviewerDeps],
        concept_uuid: str,
        value: float,
        reason: str,
        evidence: str,
        period: str = "CY",
        entity_scope: str = "Company",
        children_status: str = "",
    ) -> str:
        """Write a grounded fix to a concept's value (the only write path).

        ``reason`` is a short why; ``evidence`` MUST cite the PDF page + the
        figure you read (or 'arithmetic: <expr>' for a pure reconciliation).
        Ungrounded writes and plugs into catch-all / abstract rows are
        refused. For a total whose breakdown the source doesn't itemise,
        pass children_status='aggregate_only'. Returns 'ok: …' or
        'rejected: …' — read the rejection and re-investigate, never plug.
        """
        out = apply_reviewer_fix(
            ctx.deps.db_path, ctx.deps.run_id,
            FactWrite(
                concept_uuid=concept_uuid, period=period,
                entity_scope=entity_scope, value=value,
                value_status="observed",
                children_status=children_status or None,
                source=reason, evidence=evidence or None, actor="reviewer",
            ),
            template_prefix=_family_prefix(
                ctx.deps.filing_standard, ctx.deps.filing_level),
            rejections=ctx.deps.rejections,
        )
        if out.startswith("ok"):
            ctx.deps.writes_performed += 1
        return out

    @agent.tool
    def mark_not_disclosed(
        ctx: RunContext[ReviewerDeps],
        concept_uuid: str,
        reason: str,
        evidence: str,
        period: str = "CY",
        entity_scope: str = "Company",
    ) -> str:
        """Clear a leaf the source does NOT actually disclose (false positive).

        Use when the extraction invented or mis-attached a figure the PDF
        doesn't contain: this blanks the cell (value=None,
        value_status='not_disclosed') instead of forcing another number in.
        Still grounded — ``evidence`` MUST cite the page you checked to
        confirm the line is absent (e.g. 'page 30: no such line in the
        disclosure'). Goes through the same no-plug guard as apply_fix.
        Returns 'ok: …' or 'rejected: …'.
        """
        out = apply_reviewer_fix(
            ctx.deps.db_path, ctx.deps.run_id,
            FactWrite(
                concept_uuid=concept_uuid, period=period,
                entity_scope=entity_scope, value=None,
                value_status="not_disclosed",
                source=reason, evidence=evidence or None, actor="reviewer",
            ),
            template_prefix=_family_prefix(
                ctx.deps.filing_standard, ctx.deps.filing_level),
            rejections=ctx.deps.rejections,
        )
        if out.startswith("ok"):
            ctx.deps.writes_performed += 1
        return out

    @agent.tool
    def raise_flag(
        ctx: RunContext[ReviewerDeps],
        category: str,
        reasoning: str,
        concept_uuid: str = "",
        target_sheet: str = "",
        target_row: int = 0,
        pdf_page: int = 0,
        applied_fix: str = "",
    ) -> str:
        """Flag a case for the human. Use SPARINGLY — only two categories.

        category='stuck' when you cannot reconcile or ground a figure;
        category='disputes_prior' when you believe an earlier agent erred
        (set applied_fix if you also changed the value). Grounded fixes you
        ARE confident in need no flag — they appear in the diff.
        """
        out = raise_reviewer_flag(
            ctx.deps.db_path, ctx.deps.run_id,
            category=category, reasoning=reasoning,
            concept_uuid=concept_uuid or None,
            target_sheet=target_sheet or None,
            target_row=target_row or None,
            pdf_page=pdf_page or None,
            applied_fix=applied_fix or None,
        )
        if out.startswith("ok"):
            ctx.deps.flags_raised += 1
        return out

    @agent.tool
    def verify_fixes(ctx: RunContext[ReviewerDeps]) -> str:
        """Re-run the cross-checks against your CURRENT edits and report back.

        Call this AFTER you have applied fixes, BEFORE you finish. It
        recomputes the cascade (so your leaf edits flow into the totals) and
        re-runs the SAME cross-check suite that flagged the failures, reading
        your in-progress facts. It tells you, per check:
          - which targeted failures are now RESOLVED,
          - which are STILL FAILING (keep working), and
          - any ``⚠ NEW`` failure your own edit introduced (a check that was
            PASSING before) — meaning that edit was wrong; revise or revert it.

        Do not declare yourself done while a failure you can fix — or any
        failure your edits caused — remains.
        """
        try:
            results = run_verification_checks(
                ctx.deps.db_path, ctx.deps.run_id,
                filing_level=ctx.deps.filing_level,
                filing_standard=ctx.deps.filing_standard,
                scope=ctx.deps.verify_scope,
            )
        except Exception as exc:  # noqa: BLE001 — never crash the agent loop
            logger.warning(
                "verify_fixes failed for run %s", ctx.deps.run_id,
                exc_info=True,
            )
            return (
                f"verify_fixes could not run ({type(exc).__name__}). Continue "
                f"investigating from the facts and the PDF; rely on your "
                f"cascade traces to judge whether the fix holds."
            )
        return _format_verification(results, ctx.deps.original_failed_names)

    return agent, deps
