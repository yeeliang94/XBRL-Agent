"""Phase 3 — canonical-mode correction agent.

The legacy ``correction/agent.py`` operates on the merged xlsx — it
inspects cells, calls ``fill_workbook``, then re-runs cross-checks.
The canonical-mode variant operates on the concept tree instead:
conflicts come from ``run_concept_conflicts``; resolutions write
through the facts API; ``aggregate_only`` and ``not_disclosed`` are
legitimate moves the legacy agent didn't have.

This module starts with the prompt-renderer (step 3.1).  Tools and
the agent factory follow in steps 3.2-3.10.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

# Imported at module scope so pydantic-ai can resolve the RunContext
# annotation on the tool wrappers' signatures (lazy eval looks in module
# globals). pydantic_ai is a third-party dep — no import cycle.
from pydantic_ai import Agent, RunContext

from tools.calculator import calculator_result_json as _calculator_impl


_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "prompts"
    / "correction_canonical.md"
)


def render_canonical_correction_prompt(
    *,
    db_path: str | Path,
    run_id: int,
    conflicts: Sequence[dict[str, Any]] | None = None,
) -> str:
    """Compose the canonical-mode correction prompt.

    The prompt body comes from ``prompts/correction_canonical.md`` and
    is suffixed with a structured ``=== OPEN CONFLICTS ===`` block
    that lists each conflict the cascade detected for this run.  The
    block carries concept_uuid + kind + residual + detail — exactly
    the shape the legacy prompt's ``failed_checks`` block carried,
    but rephrased for the concept-tree mental model.
    """
    body = _PROMPT_PATH.read_text(encoding="utf-8").strip()

    # If the caller supplied conflicts directly, use them.  Otherwise
    # pull from the DB so a fresh agent instance always sees the
    # current queue.
    if conflicts is None:
        conflicts = _load_open_conflicts(db_path, run_id)

    conflict_block = _format_conflicts(conflicts)
    return f"{body}\n\n{conflict_block}"


def _load_open_conflicts(
    db_path: str | Path, run_id: int
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
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


def _format_conflicts(conflicts: Sequence[dict[str, Any]]) -> str:
    """One block per conflict — pinned shape so the agent's tool calls
    can quote ``concept_uuid`` verbatim back into ``post_fact`` /
    ``mark_aggregate_only`` / ``mark_not_disclosed``.
    """
    lines = ["=== OPEN CONFLICTS ===", ""]
    if not conflicts:
        lines.append("(none — the cascade is currently clean.)")
        return "\n".join(lines)
    for c in conflicts:
        label = c.get("canonical_label") or "(unknown concept)"
        sheet = c.get("render_sheet") or "?"
        row = c.get("render_row") or "?"
        lines.append(
            f"- concept_uuid: {c['concept_uuid']}\n"
            f"  canonical_label: {label}\n"
            f"  render: {sheet} row {row}\n"
            f"  kind: {c['kind']}\n"
            f"  residual: {c.get('residual')}\n"
            f"  detail: {c.get('detail')}\n"
            f"  resolutions allowed: revise_leaf (correct the underlying leaf), "
            f"mark_aggregate_only (parent), mark_not_disclosed (leaf)"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool payload builders (step 3.2 - 3.3)
#
# These are pure functions that emit the body the canonical-mode
# correction agent passes to ``POST /api/runs/{id}/facts``.  Keeping
# them separate from the FastAPI route means we can unit-test the
# payload shape without standing up the full agent.  The actual
# pydantic-ai @agent.tool wrappers live in the canonical agent
# factory (step 3.10 follow-up); they just call these helpers.
# ---------------------------------------------------------------------------


def mark_aggregate_only(
    *,
    concept_uuid: str,
    value: float,
    source: str,
    evidence: str | None = None,
    period: str = "CY",
    entity_scope: str = "Company",
) -> dict[str, Any]:
    """Stage an ``aggregate_only`` fact for a COMPUTED parent."""
    return {
        "concept_uuid": concept_uuid,
        "period": period,
        "entity_scope": entity_scope,
        "value": value,
        "value_status": "observed",
        "children_status": "aggregate_only",
        "source": source,
        "evidence": evidence,
    }


def mark_not_disclosed(
    *,
    concept_uuid: str,
    source: str,
    evidence: str | None = None,
    period: str = "CY",
    entity_scope: str = "Company",
) -> dict[str, Any]:
    """Stage a ``not_disclosed`` fact for a LEAF.

    The exporter skips not_disclosed leaves and records them in the
    side-channel JSON (see ``concept_model/exporter.py``).
    """
    return {
        "concept_uuid": concept_uuid,
        "period": period,
        "entity_scope": entity_scope,
        "value": None,
        "value_status": "not_disclosed",
        "children_status": None,
        "source": source,
        "evidence": evidence,
    }


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


@dataclass
class CanonicalCorrectionDeps:
    """Run context carried through the canonical correction agent's tools.

    The tools write facts through the in-process facts core (apply_fact)
    against this run's DB, so the correction edits land in the canonical
    store — not a scratch xlsx — keeping the Concepts UI and the
    DB-exported download in sync (peer-review finding 2 / Phase D).
    """
    db_path: str
    run_id: int
    filing_level: str = "company"
    # Source PDF for the run — enables the read-only view_pdf_pages tool so
    # the agent can ground corrections in the actual disclosure (peer-review
    # F2). None when the path is unknown; the tool then reports it's
    # unavailable rather than guessing.
    pdf_path: str | None = None
    writes_performed: int = 0


def _apply_correction_fact(db_path: str | Path, run_id: int, fact) -> str:
    """Apply one correction fact and resolve the concept's open conflicts.

    Returns a short status string for the agent (``ok``/``rejected``). A
    rejection (e.g. the agent aimed an observed write at a formula concept)
    is reported back, not raised, so the agent can pick a different tool.
    """
    from fastapi import HTTPException
    from concept_model.facts_api import apply_fact, _open_conn

    conn = _open_conn(str(db_path))
    try:
        apply_fact(conn, run_id, fact)  # commits on success
        # The agent acted on this concept to resolve its conflict — close any
        # open conflict rows for it so the reconciliation queue reflects the
        # resolution. Sentinel rows (concept_uuid='') are never touched.
        if fact.concept_uuid:
            conn.execute(
                "UPDATE run_concept_conflicts SET status = 'resolved', "
                "resolved_at = ? WHERE run_id = ? AND concept_uuid = ? "
                "AND status = 'open'",
                (_now(), run_id, fact.concept_uuid),
            )
            conn.commit()
        return (
            f"ok: {fact.value_status} {fact.value if fact.value is not None else ''} "
            f"on {fact.concept_uuid} ({fact.period}/{fact.entity_scope})"
        ).strip()
    except HTTPException as exc:
        conn.rollback()
        return f"rejected: {exc.detail}"
    except Exception as exc:  # noqa: BLE001 — report, don't crash the agent loop
        conn.rollback()
        return f"error: {type(exc).__name__}: {exc}"
    finally:
        conn.close()


def create_canonical_correction_agent(
    *,
    model,
    db_path: str | Path,
    run_id: int,
    filing_level: str = "company",
    pdf_path: str | Path | None = None,
    conflicts: Sequence[dict[str, Any]] | None = None,
):
    """Build the canonical-mode correction agent (Phase D).

    Returns ``(agent, deps)``. The agent surfaces three WRITE tools —
    ``revise_leaf`` (re-state an observed LEAF value), ``mark_aggregate_only``
    (declare a COMPUTED parent's breakdown undisclosed), and
    ``mark_not_disclosed`` (a LEAF the source confirms is absent) — plus three
    READ-ONLY tools (peer-review F2) so corrections are grounded in evidence,
    not guessed: ``get_conflict_context`` (why a concept is in conflict),
    ``get_child_facts`` (the parent's current breakdown so the agent can find
    the wrong leaf), and ``view_pdf_pages`` (read the source disclosure). All
    writes land through the facts API in run_concept_facts. The system prompt
    carries the open-conflict block from
    :func:`render_canonical_correction_prompt`.
    """
    from pydantic_ai.settings import ModelSettings
    from concept_model.facts_api import FactWrite

    deps = CanonicalCorrectionDeps(
        db_path=str(db_path), run_id=run_id, filing_level=filing_level,
        pdf_path=str(pdf_path) if pdf_path is not None else None,
    )
    system_prompt = render_canonical_correction_prompt(
        db_path=db_path, run_id=run_id, conflicts=conflicts,
    )
    # Temperature pinned to 1.0 — Gemini 3 through the enterprise proxy
    # requires it (mirrors extraction + legacy correction agents).
    agent = Agent(
        model,
        deps_type=CanonicalCorrectionDeps,
        system_prompt=system_prompt,
        model_settings=ModelSettings(temperature=1.0),
    )

    @agent.tool
    def calculator(ctx: RunContext[CanonicalCorrectionDeps], expression: str) -> str:
        """Evaluate arithmetic exactly before writing corrected facts.

        Supports numbers, parentheses, unary signs, and + - * /. Use explicit
        negatives such as -123; accounting parentheses are treated as ordinary
        grouping.
        """
        return _calculator_impl(expression)

    @agent.tool
    def get_conflict_context(
        ctx: RunContext[CanonicalCorrectionDeps],
        concept_uuid: str,
    ) -> str:
        """Read why a concept is in conflict before you fix it (read-only).

        Returns the concept's open conflict rows (kind, residual, detail) plus
        its own current fact (value, value_status, children_status) and source
        cell, for the given run. Use this to understand a residual instead of
        guessing a correction.
        """
        conn = sqlite3.connect(ctx.deps.db_path)
        conn.row_factory = sqlite3.Row
        try:
            node = conn.execute(
                "SELECT canonical_label, kind, render_sheet, render_row "
                "FROM concept_nodes WHERE concept_uuid = ?",
                (concept_uuid,),
            ).fetchone()
            if node is None:
                return f"Unknown concept_uuid {concept_uuid!r}."
            conflicts_here = conn.execute(
                "SELECT period, entity_scope, kind, residual, detail "
                "FROM run_concept_conflicts WHERE run_id = ? "
                "AND concept_uuid = ? AND status = 'open' ORDER BY created_at",
                (ctx.deps.run_id, concept_uuid),
            ).fetchall()
            facts = conn.execute(
                "SELECT period, entity_scope, value, value_status, "
                "children_status, source FROM run_concept_facts "
                "WHERE run_id = ? AND concept_uuid = ?",
                (ctx.deps.run_id, concept_uuid),
            ).fetchall()
        finally:
            conn.close()
        lines = [
            f"{node['canonical_label']} ({node['kind']}, "
            f"{node['render_sheet']} row {node['render_row']})",
            "open conflicts:",
        ]
        lines += (
            [f"  - {c['kind']} {c['period']}/{c['entity_scope']}: "
             f"residual={c['residual']} — {c['detail']}" for c in conflicts_here]
            or ["  (none)"]
        )
        lines.append("current facts:")
        lines += (
            [f"  - {f['period']}/{f['entity_scope']}: value={f['value']} "
             f"status={f['value_status']} children={f['children_status']} "
             f"source={f['source']}" for f in facts]
            or ["  (no fact written yet)"]
        )
        return "\n".join(lines)

    @agent.tool
    def get_child_facts(
        ctx: RunContext[CanonicalCorrectionDeps],
        concept_uuid: str,
        period: str = "CY",
        entity_scope: str = "Company",
    ) -> str:
        """List a parent concept's children and their current values (read-only).

        For a COMPUTED/total concept, returns each summand (label, kind, signed
        coefficient, current value/status) for the given period+scope, plus the
        parent's value and the children's signed sum. Use this to find WHICH
        leaf is wrong or missing when a parent/children mismatch is flagged.
        """
        conn = sqlite3.connect(ctx.deps.db_path)
        conn.row_factory = sqlite3.Row
        try:
            edges = conn.execute(
                "SELECT e.child_uuid, e.coefficient, n.canonical_label, n.kind "
                "FROM concept_edges e "
                "JOIN concept_nodes n ON n.concept_uuid = e.child_uuid "
                "WHERE e.parent_uuid = ?",
                (concept_uuid,),
            ).fetchall()
            if not edges:
                return (
                    f"{concept_uuid} has no children (it's a leaf / data-entry "
                    "cell). Use get_conflict_context instead."
                )
            total = 0.0
            missing = False
            lines = [f"children of {concept_uuid} ({period}/{entity_scope}):"]
            for e in edges:
                fact = conn.execute(
                    "SELECT value, value_status FROM run_concept_facts "
                    "WHERE run_id = ? AND concept_uuid = ? AND period = ? "
                    "AND entity_scope = ?",
                    (ctx.deps.run_id, e["child_uuid"], period, entity_scope),
                ).fetchone()
                val = fact["value"] if fact else None
                status = fact["value_status"] if fact else "(no fact)"
                if val is None:
                    missing = True
                else:
                    total += float(e["coefficient"]) * float(val)
                lines.append(
                    f"  - {e['canonical_label']} ({e['kind']}, coef "
                    f"{e['coefficient']:+g}) = {val} [{status}] uuid={e['child_uuid']}"
                )
            parent = conn.execute(
                "SELECT value FROM run_concept_facts WHERE run_id = ? "
                "AND concept_uuid = ? AND period = ? AND entity_scope = ?",
                (ctx.deps.run_id, concept_uuid, period, entity_scope),
            ).fetchone()
        finally:
            conn.close()
        parent_val = parent["value"] if parent else None
        lines.append(
            f"children signed sum = {total}"
            + (" (some children missing)" if missing else "")
            + f"; parent value = {parent_val}"
        )
        return "\n".join(lines)

    @agent.tool
    def view_pdf_pages(
        ctx: RunContext[CanonicalCorrectionDeps], pages: list[int]
    ):
        """View source PDF pages as images (read-only) to verify a figure.

        Pass page numbers, e.g. [12, 13]. Returns the rendered pages so you can
        read the disclosure and ground a correction in the actual source rather
        than guessing. Always cite the page you used in the ``evidence`` arg of
        the write tool.
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
    def revise_leaf(
        ctx: RunContext[CanonicalCorrectionDeps],
        concept_uuid: str,
        value: float,
        source: str,
        evidence: str = "",
        period: str = "CY",
        entity_scope: str = "Company",
    ) -> str:
        """Re-state an observed value on a LEAF concept to fix a residual.

        Use when the cascade flagged a parent/children mismatch caused by a
        wrong or missing leaf value. Never aim this at a formula (COMPUTED /
        total) concept — use mark_aggregate_only for those.
        """
        out = _apply_correction_fact(
            ctx.deps.db_path, ctx.deps.run_id,
            FactWrite(
                concept_uuid=concept_uuid, period=period,
                entity_scope=entity_scope, value=value,
                value_status="observed", source=source,
                evidence=evidence or None, actor="correction",
            ),
        )
        if out.startswith("ok"):
            ctx.deps.writes_performed += 1
        return out

    @agent.tool
    def mark_aggregate_only(
        ctx: RunContext[CanonicalCorrectionDeps],
        concept_uuid: str,
        value: float,
        source: str,
        evidence: str = "",
        period: str = "CY",
        entity_scope: str = "Company",
    ) -> str:
        """Declare a COMPUTED parent's value authoritative because its
        itemised breakdown is not disclosed in the source. Replaces the live
        formula with the literal value on export and annotates the source.
        """
        out = _apply_correction_fact(
            ctx.deps.db_path, ctx.deps.run_id,
            FactWrite(
                concept_uuid=concept_uuid, period=period,
                entity_scope=entity_scope, value=value,
                value_status="observed", children_status="aggregate_only",
                source=source, evidence=evidence or None, actor="correction",
            ),
        )
        if out.startswith("ok"):
            ctx.deps.writes_performed += 1
        return out

    @agent.tool
    def mark_not_disclosed(
        ctx: RunContext[CanonicalCorrectionDeps],
        concept_uuid: str,
        source: str,
        evidence: str = "",
        period: str = "CY",
        entity_scope: str = "Company",
    ) -> str:
        """Mark a LEAF the source confirms is genuinely absent. The exporter
        leaves the cell blank and records it in the side-channel JSON. Use
        ONLY when the disclosure truly isn't in the PDF — never to silence a
        residual you couldn't reconcile.
        """
        out = _apply_correction_fact(
            ctx.deps.db_path, ctx.deps.run_id,
            FactWrite(
                concept_uuid=concept_uuid, period=period,
                entity_scope=entity_scope, value=None,
                value_status="not_disclosed", source=source,
                evidence=evidence or None, actor="correction",
            ),
        )
        if out.startswith("ok"):
            ctx.deps.writes_performed += 1
        return out

    return agent, deps


def canonical_correction_payload_builders() -> list[Callable[..., Any]]:
    """The standalone *payload-builder* helpers, exposed for unit tests that
    assert the request shape sent to the facts API without standing up the
    full agent.

    This is NOT the live agent's tool roster — do not wire it as such. The
    runnable agent built by :func:`create_canonical_correction_agent`
    registers three tools — ``revise_leaf``, ``mark_aggregate_only`` and
    ``mark_not_disclosed`` — as closures bound to the run's DB. ``revise_leaf``
    has no standalone payload builder (it's a thin ``observed`` LEAF write),
    so it doesn't appear here.
    """
    return [mark_aggregate_only, mark_not_disclosed]


# ---------------------------------------------------------------------------
# Caps (step 3.4-3.6) — ported verbatim from the legacy correction agent.
#
# We extract these to a single home so the legacy and canonical paths
# can NEVER drift.  Raising one cap without the other would silently
# break the gotcha-#18 invariant (canonical_correction races past
# pydantic-ai's 50-iter silent default and crashes mid-turn).
# ---------------------------------------------------------------------------


def compute_canonical_turn_cap(
    *, filing_level: str, n_conflicts: int
) -> int:
    """RUN-REVIEW P0-1 dynamic turn cap, canonical-mode flavour.

    Formula: 8 base + 4 if Group + 2 per open conflict, clamped [8, 25].

    Identical to ``server.py::_run_correction_pass`` so the two
    correction paths share an authoritative ceiling.  When Phase 3.10
    re-enables auto-correction in canonical mode, the coordinator
    calls this helper instead of inlining the math (see step 3.10).
    """
    is_group = (filing_level or "").lower() == "group"
    raw = 8 + (4 if is_group else 0) + 2 * int(n_conflicts)
    return max(8, min(25, raw))


def canonical_correction_wallclock_timeout() -> float:
    """Re-export ``server.CORRECTION_WALLCLOCK_TIMEOUT`` so canonical
    correction never drifts from the legacy 300s ceiling.  Sharing the
    constant means an operator override via ``XBRL_CORRECTION_WALLCLOCK_S``
    applies to both paths automatically.
    """
    # Local import dodges the server↔correction import cycle (server
    # imports this module at FastAPI startup; importing server at
    # module load time would deadlock).
    from server import CORRECTION_WALLCLOCK_TIMEOUT
    return CORRECTION_WALLCLOCK_TIMEOUT


def record_correction_exhaustion(
    *,
    db_path: str | Path,
    run_id: int,
    unresolved_conflict_ids: Sequence[int],
    turns_used: int,
    max_turns: int,
    detail: str | None = None,
) -> None:
    """Drop a sentinel ``correction_exhausted`` row into
    ``run_concept_conflicts`` so the reconciliation queue UI surfaces
    "the agent gave up" distinctly from a routine open conflict.

    The unresolved conflicts themselves stay open in their own rows —
    we don't touch them.  The sentinel row carries ``concept_uuid=''``
    (no specific concept; the exhaustion is run-level) and the open
    conflict count in ``detail`` so the UI can render a banner.
    """
    from datetime import datetime, timezone
    now = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    body = (
        detail
        if detail is not None
        else f"correction exhausted after {turns_used}/{max_turns} turns; "
             f"{len(unresolved_conflict_ids)} conflict(s) left open"
    )
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO run_concept_conflicts(
                run_id, concept_uuid, period, entity_scope,
                kind, residual, detail, status, created_at
            ) VALUES (?, '', '', '', 'correction_exhausted',
                      NULL, ?, 'open', ?)
            """,
            (run_id, body, now),
        )
        conn.commit()
    finally:
        conn.close()
