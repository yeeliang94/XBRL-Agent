"""Filing-readiness preflight for the mTool fill (Step 8A, finding 4).

Run status alone is **not** a filing-readiness gate. ``completed_with_errors``
is an accepted fillable status (a run can finish with a cross-check imbalance
the operator has already judged), and the exporter deliberately writes
``conflict`` facts rather than blanking a cell the operator can't see — so the
two decisions compound: a run can be "terminal" and still carry values nobody
has adjudicated.

This module is the explicit gate. It answers one question — *is this run's
data settled enough to become a filing artifact?* — and returns plain-language
blockers a product person can act on, never a status code alone.

Blocking is the default. An operator may override, but only by sending an
explicit acknowledgement, which is recorded on the fill receipt (Step 19) so
the override is auditable rather than invisible.

Pure reads; no writes, no schema of its own.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

# Cap the per-blocker example list so a run with hundreds of open conflicts
# produces a readable message instead of a wall of labels. Counts stay exact.
_EXAMPLE_CAP = 8

# The banner value the notes-coverage checklist writes when the scout inventory
# was empty/unavailable (server.COVERAGE_META_NOTE row's status).
_COVERAGE_META_NOTE = -1
_INVENTORY_UNAVAILABLE = "inventory_unavailable"


def _conflict_facts(conn: sqlite3.Connection, run_id: int,
                    family_prefix: str) -> list[sqlite3.Row]:
    """Every unresolved-conflict fact in the run's template family."""
    return conn.execute(
        """
        SELECT n.canonical_label, n.render_sheet, n.kind, f.period,
               f.entity_scope
        FROM run_concept_facts f
        JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid
        WHERE f.run_id = ? AND n.template_id LIKE ?
          AND f.value_status = 'conflict'
        ORDER BY n.render_sheet, n.render_row
        """,
        (run_id, family_prefix + "%"),
    ).fetchall()


def _open_reviewer_flags(conn: sqlite3.Connection,
                         run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT category, target_sheet, target_row, reasoning "
        "FROM reviewer_flags WHERE run_id = ? AND status = 'open' "
        "ORDER BY id",
        (run_id,),
    ).fetchall()


def _coverage_state(conn: sqlite3.Connection,
                    run_id: int) -> tuple[str | None, list[sqlite3.Row]]:
    """Return (banner, unresolved top-level coverage rows).

    Mirrors ``notes.coverage_checklist.row_is_unresolved`` — a ``missing`` /
    ``suspected_gap`` row counts unless the reviewer recorded a resolving
    verdict, and a sub-ref confirmed ``missing`` also counts. Imported lazily so
    this module stays usable from the CLI without the notes package loaded.
    """
    from notes.coverage_checklist import row_is_unresolved

    rows = conn.execute(
        "SELECT note_num, subnote_ref, status, reviewer_verdict, title "
        "FROM notes_coverage_rows WHERE run_id = ? "
        "ORDER BY note_num, (subnote_ref IS NOT NULL), subnote_ref",
        (run_id,),
    ).fetchall()
    if not rows:
        return None, []  # feature never ran for this run — nothing to assert

    banner = None
    subnote_states: dict[int, list[str]] = {}
    tops: list[sqlite3.Row] = []
    for r in rows:
        if r["note_num"] == _COVERAGE_META_NOTE:
            banner = r["status"]
        elif r["subnote_ref"] is None:
            tops.append(r)
        else:
            subnote_states.setdefault(r["note_num"], []).append(r["status"])

    unresolved = [
        r for r in tops
        if row_is_unresolved(r["status"], r["reviewer_verdict"],
                             subnote_states.get(r["note_num"], []))
    ]
    return banner, unresolved


def evaluate_preflight(
    db_path: str | Path,
    run_id: int,
    *,
    filing_standard: str,
    filing_level: str,
    written_keys: set[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """Assess whether ``run_id`` is settled enough to produce a filing artifact.

    ``written_keys`` is the set of ``(canonical_label, period, entity_scope)``
    the fill doc will actually write. Passing it splits conflicts into the ones
    that would reach the filing (blocking) and the ones that would not
    (advisory) — so a conflict on a SOCIE row nobody can file today doesn't
    stop a legitimate SOFP fill.

    Returns::

        {"ok": bool,
         "blockers": [{code, count, message, examples: [...]}, ...],
         "warnings": [ ...same shape... ]}

    Messages are written for the operator, not the log.
    """
    family_prefix = f"{filing_standard.lower()}-{filing_level.lower()}-"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conflicts = _conflict_facts(conn, run_id, family_prefix)
        flags = _open_reviewer_flags(conn, run_id)
        banner, coverage_unresolved = _coverage_state(conn, run_id)
    finally:
        conn.close()

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def _describe(r: sqlite3.Row) -> str:
        return f"{r['canonical_label']} ({r['render_sheet']}, {r['period']})"

    if written_keys is None:
        filed_conflicts, other_conflicts = list(conflicts), []
    else:
        filed_conflicts, other_conflicts = [], []
        for r in conflicts:
            key = (r["canonical_label"], r["period"], r["entity_scope"])
            (filed_conflicts if key in written_keys
             else other_conflicts).append(r)

    if filed_conflicts:
        blockers.append({
            "code": "open_conflicts",
            "count": len(filed_conflicts),
            "message": (
                f"{len(filed_conflicts)} figure(s) are still marked as "
                "conflicting — two sources disagree and nobody has picked a "
                "winner. Resolve them on the Review values tab first; filing "
                "an unadjudicated figure is exactly the mistake this gate "
                "exists to prevent."),
            "examples": [_describe(r) for r in filed_conflicts[:_EXAMPLE_CAP]],
        })
    if other_conflicts:
        warnings.append({
            "code": "conflicts_outside_fill",
            "count": len(other_conflicts),
            "message": (
                f"{len(other_conflicts)} conflicting figure(s) exist on rows "
                "this fill does not write (for example the statement of "
                "changes in equity, which isn't filled yet). They won't reach "
                "the workbook, but they're worth resolving."),
            "examples": [_describe(r) for r in other_conflicts[:_EXAMPLE_CAP]],
        })

    if flags:
        blockers.append({
            "code": "open_reviewer_flags",
            "count": len(flags),
            "message": (
                f"The reviewer raised {len(flags)} question(s) that are still "
                "open. Answer or dismiss them on the Review tab so the filing "
                "reflects a decision, not an unanswered doubt."),
            "examples": [
                f"{r['category']}: "
                f"{(r['reasoning'] or '').strip()[:120] or '(no detail)'}"
                for r in flags[:_EXAMPLE_CAP]],
        })

    if banner == _INVENTORY_UNAVAILABLE:
        blockers.append({
            "code": "notes_inventory_unavailable",
            "count": 1,
            "message": (
                "We could not build the list of notes for this run, so there "
                "is no way to tell whether every note was captured. Re-run the "
                "notes review before filing."),
            "examples": [],
        })
    elif coverage_unresolved:
        blockers.append({
            "code": "notes_coverage_unresolved",
            "count": len(coverage_unresolved),
            "message": (
                f"{len(coverage_unresolved)} note(s) from the source document "
                "are still unaccounted for. Resolve them on the Notes tab "
                "(place them, or mark them not applicable) before filing."),
            "examples": [
                f"Note {r['note_num']}"
                + (f" — {r['title']}" if r["title"] else "")
                + f" ({r['status']})"
                for r in coverage_unresolved[:_EXAMPLE_CAP]],
        })

    return {"ok": not blockers, "blockers": blockers, "warnings": warnings}


def written_keys_from_doc(doc: dict[str, Any]) -> set[tuple[str, str, str]]:
    """The ``(label, period, entity_scope)`` set a fill doc actually writes.

    The doc carries a semantic ``column_role`` rather than a period/scope pair,
    so map it back — that role is exactly what the exporter derived them from.
    """
    out: set[tuple[str, str, str]] = set()
    for w in doc.get("writes", []):
        role = w.get("column_role") or ""
        period = "PY" if role.endswith("prior_year") else "CY"
        scope = "Group" if role.startswith("group_") else "Company"
        out.add((w.get("label"), period, scope))
    return out


__all__ = ["evaluate_preflight", "written_keys_from_doc"]
