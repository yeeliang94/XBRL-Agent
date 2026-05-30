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

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

# Module-scope so pydantic-ai can resolve the RunContext annotation on the
# tool wrappers (lazy eval looks in module globals).
from pydantic_ai import Agent, RunContext

from tools.calculator import calculator_result_json as _calculator_impl


_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "reviewer.md"
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
    consistent failure contract and re-investigates.
    """
    kind = concept["kind"]
    label = concept["canonical_label"]
    sheet = concept["render_sheet"]
    row = concept["render_row"]

    if not (evidence and str(evidence).strip()):
        return (
            "rejected: ungrounded write refused — cite the PDF page + the "
            "figure you read in `evidence` (or 'arithmetic: <expr>' when the "
            "value is a pure reconciliation of already-grounded cells). The "
            "reviewer never writes a number it can't ground."
        )

    if kind == "ABSTRACT":
        return (
            f"rejected: {sheet} row {row} ({label!r}) is an ABSTRACT section "
            f"header — never writable (invariant #17). Write a leaf row "
            f"inside the section instead."
        )

    if _is_catchall_label(label) and _is_arithmetic_only_evidence(evidence):
        return (
            f"rejected: {sheet} row {row} ({label!r}) is a catch-all / "
            f"residual row, and an arithmetic-only value is a balancing plug. "
            f"Never plug a residual into a catch-all to force a balance "
            f"(invariant #17). Fix the real leaf, or leave the imbalance "
            f"flagged. (A PDF-cited disclosure on this row is fine — cite the "
            f"page instead of an arithmetic expression.)"
        )

    return None


def apply_reviewer_fix(
    db_path: str | Path,
    run_id: int,
    fact,
    *,
    template_prefix: Optional[str] = None,
) -> str:
    """Run the no-plug guard, then write one reviewer fix through apply_fact.

    ``fact`` is a ``FactWrite`` (actor should be ``"reviewer"``). Returns a
    short status string for the agent: ``ok: …`` on success, ``rejected: …``
    when the guard or the facts API refuses. Never raises — a rejection is
    reported so the agent can pick a different target, mirroring
    ``correction/canonical_agent.py::_apply_correction_fact``.

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

        # Deterministic guard NEXT — invariant #17 + grounding.
        rejection = evaluate_apply_fix_guard(concept, evidence=fact.evidence)
        if rejection is not None:
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
) -> str:
    """Compose the reviewer system prompt.

    Body comes from ``prompts/reviewer.md``; we append a structured review
    packet listing the failing cross-checks + open conflicts the reviewer
    should investigate, plus any free-text human guidance from a re-review.
    The packet leads with the filing context (standard + level) so the
    reviewer reads/writes the right ``entity_scope`` on Group filings.
    """
    body = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    packet = _format_review_packet(
        failed_checks or [], conflicts or [], guidance,
        filing_level=filing_level, filing_standard=filing_standard,
    )
    return f"{body}\n\n{packet}"


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


def _format_review_packet(
    failed_checks: Sequence[dict[str, Any]],
    conflicts: Sequence[dict[str, Any]],
    guidance: Optional[str],
    *,
    filing_level: str = "company",
    filing_standard: str = "mfrs",
) -> str:
    is_group = (filing_level or "").lower() == "group"
    lines = ["=== REVIEW PACKET ===", ""]
    lines.append(
        f"Filing: {(filing_standard or 'mfrs').upper()} "
        f"{(filing_level or 'company').capitalize()}."
    )
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
        for c in failed_checks:
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

    Formula: 10 base + 4 if Group + 2 per item to investigate, clamped
    [10, 30]. The 30 ceiling leaves the same comfortable buffer below 50
    that the correction agent's 25-cap does, while giving the reviewer more
    room than correction because it investigates (reads) before it writes.
    """
    is_group = (filing_level or "").lower() == "group"
    raw = 10 + (4 if is_group else 0) + 2 * int(n_items)
    return max(10, min(30, raw))


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
):
    """Build the reviewer agent. Returns ``(agent, deps)``.

    Read tools: ``read_facts``, ``trace_cascade_source``, ``view_pdf_pages``,
    ``calculator``. Write tools: ``apply_fix`` (guarded), ``raise_flag``.
    The system prompt carries the review packet from
    :func:`render_reviewer_prompt`.
    """
    from pydantic_ai.settings import ModelSettings
    from concept_model.facts_api import FactWrite

    deps = ReviewerDeps(
        db_path=str(db_path), run_id=run_id, filing_level=filing_level,
        filing_standard=filing_standard,
        pdf_path=str(pdf_path) if pdf_path is not None else None,
    )
    system_prompt = render_reviewer_prompt(
        db_path=db_path, run_id=run_id, failed_checks=failed_checks,
        conflicts=conflicts, guidance=guidance,
        filing_level=filing_level, filing_standard=filing_standard,
    )
    # Temperature pinned to 1.0 — Gemini 3 through the enterprise proxy
    # requires it (mirrors extraction + correction agents).
    agent = Agent(
        model,
        deps_type=ReviewerDeps,
        system_prompt=system_prompt,
        model_settings=ModelSettings(temperature=1.0),
    )

    @agent.tool
    def calculator(ctx: RunContext[ReviewerDeps], expression: str) -> str:
        """Evaluate arithmetic exactly before writing a corrected fact.

        Supports numbers, parentheses, unary signs, and + - * /.
        """
        return _calculator_impl(expression)

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

    return agent, deps
