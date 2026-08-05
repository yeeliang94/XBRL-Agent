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


def _open_notes_flags(conn: sqlite3.Connection,
                      run_id: int) -> list[sqlite3.Row]:
    """Open NOTES-reviewer flags (`notes_review_flags`, schema v24) — the
    prose sibling of `reviewer_flags`. The face check alone was a gap: a notes
    reviewer's unanswered doubt reaches the filing through the footnote fill
    exactly as a face flag reaches it through the numeric fill (peer review,
    2026-08-05)."""
    return conn.execute(
        "SELECT kind, sheet, row, reason "
        "FROM notes_review_flags WHERE run_id = ? AND status = 'open' "
        "ORDER BY id",
        (run_id,),
    ).fetchall()


def _integrity_state(
    conn: sqlite3.Connection, run_id: int,
) -> tuple[str | None, sqlite3.Row | None]:
    """(run's effective source-integrity mode, latest stored verdict row).

    Mode comes from ``runs.notes_integrity_mode`` (v36) — ``None`` means the
    feature predates the run (legacy) and asserts nothing. The verdict is the
    newest ``notes_integrity_runs`` row; gotcha #31: `legacy`, `off` and a
    real verdict are three different facts, and this keeps them apart.
    """
    row = conn.execute(
        "SELECT notes_integrity_mode FROM runs WHERE id = ?",
        (run_id,)).fetchone()
    mode = row["notes_integrity_mode"] if row else None
    verdict = conn.execute(
        "SELECT status, mode, requires_review, blocks_unresolved, "
        "tables_unresolved, pages_expected, pages_processed "
        "FROM notes_integrity_runs WHERE run_id = ? ORDER BY id DESC LIMIT 1",
        (run_id,)).fetchone()
    return mode, verdict


def _incomplete_face_agents(
    conn: sqlite3.Connection, run_id: int,
) -> list[sqlite3.Row]:
    """Face statements that did not finish, yet may have left facts behind.

    An agent that fails partway — the iteration cap, a turn timeout, a cancel —
    has usually already projected some of its figures into ``run_concept_facts``
    (extraction writes them live), and the merge picks its scratch workbook up
    off disk regardless of status. So a half-extracted statement reaches both the
    download and this fill without any of the other blockers noticing: its
    figures aren't in conflict, they're merely incomplete.

    Run status is deliberately NOT the gate here (see the module docstring) —
    ``completed_with_errors`` is fillable. This asks the narrower question: did
    a specific statement stop early? ``SETTLED_AGENT_STATUSES`` owns that
    definition so this gate and the download's incomplete marker cannot drift.
    """
    from statement_types import SETTLED_AGENT_STATUSES, StatementType

    faces = sorted(s.value for s in StatementType)
    settled = sorted(SETTLED_AGENT_STATUSES)
    return conn.execute(
        f"SELECT statement_type, variant, status, error_type "
        f"FROM run_agents WHERE run_id = ? "
        f"  AND statement_type IN ({','.join('?' * len(faces))}) "
        f"  AND status NOT IN ({','.join('?' * len(settled))}) "
        f"ORDER BY statement_type",
        (run_id, *faces, *settled),
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
    conflicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assess whether ``run_id`` is settled enough to produce a filing artifact.

    ``written_keys`` is the set of ``(canonical_label, period, entity_scope)``
    the fill doc will actually write. Passing it splits conflicts into the ones
    that would reach the filing (blocking) and the ones that would not
    (advisory) — so a conflict on a SOCIE row nobody can file today doesn't
    stop a legitimate SOFP fill.

    ``conflicts`` is the conflict list captured in the fill doc's OWN fact
    snapshot (``doc["meta"]["conflicts"]``). Pass it whenever a doc exists:
    it makes the verdict describe the same revision as the writes, so a
    reviewer resolving (or creating) a conflict between the doc build and this
    call cannot produce a receipt whose facts and verdict disagree (peer
    review, 2026-08-05). ``None`` falls back to a fresh DB read — correct only
    when there is no doc to stay consistent with.

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
        if conflicts is None:
            conflicts = _conflict_facts(conn, run_id, family_prefix)
        flags = _open_reviewer_flags(conn, run_id)
        notes_flags = _open_notes_flags(conn, run_id)
        incomplete_faces = _incomplete_face_agents(conn, run_id)
        banner, coverage_unresolved = _coverage_state(conn, run_id)
        integrity_mode, integrity_verdict = _integrity_state(conn, run_id)
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

    if incomplete_faces:
        _reasons = {
            "iteration_capped": "ran out of its step budget",
            "turn_timeout": "stalled and timed out",
            "wallclock": "ran out of time",
            "token_budget": "ran out of its token budget",
            "cancelled": "was stopped",
            "projection_failed": "could not save its figures",
        }
        blockers.append({
            "code": "incomplete_face_statements",
            "count": len(incomplete_faces),
            "message": (
                f"{len(incomplete_faces)} statement(s) did not finish "
                "extracting, but the figures they got as far as writing are "
                "still in this run. Filing now would submit a partly-read "
                "statement as if it were complete. Re-run the statement(s) "
                "below, or remove them from the filing."),
            "examples": [
                f"{r['statement_type']}"
                + (f" ({r['variant']})" if r["variant"] else "")
                + f" — {_reasons.get(r['error_type'] or '', r['status'])}"
                for r in incomplete_faces[:_EXAMPLE_CAP]],
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

    if notes_flags:
        blockers.append({
            "code": "open_notes_review_flags",
            "count": len(notes_flags),
            "message": (
                f"The notes reviewer raised {len(notes_flags)} question(s) "
                "that are still open. The prose notes fill into the filing "
                "too — answer or dismiss them on the Notes tab first."),
            "examples": [
                f"{r['kind']}"
                + (f" ({r['sheet']}"
                   + (f" row {r['row']}" if r["row"] is not None else "")
                   + ")" if r["sheet"] else "")
                + f": {(r['reason'] or '').strip()[:120] or '(no detail)'}"
                for r in notes_flags[:_EXAMPLE_CAP]],
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

    # Source-integrity verdict (gotcha #31). Only `enforce` may BLOCK a
    # filing — `shadow` computes the identical verdict but is contractually
    # not allowed to change outcomes, so it warns; `off`/legacy assert
    # nothing. An ENFORCE run with no stored verdict is a missing assessment,
    # and failure to assess is never proof of no loss.
    _verdict_incomplete = integrity_verdict is not None and (
        bool(integrity_verdict["requires_review"])
        or integrity_verdict["status"] != "complete")
    if integrity_mode == "enforce":
        if integrity_verdict is None:
            blockers.append({
                "code": "notes_integrity_missing",
                "count": 1,
                "message": (
                    "This run requires the source-completeness check "
                    "(enforce mode), but no verdict was stored — there is no "
                    "proof the source document was fully handled. Re-run the "
                    "notes integrity check before filing."),
                "examples": [],
            })
        elif _verdict_incomplete:
            blockers.append({
                "code": "notes_integrity_needs_review",
                "count": 1,
                "message": (
                    "The source-completeness check found material that is "
                    "not accounted for (or could not finish assessing the "
                    "document). Resolve it on the Notes tab before filing — "
                    "filing over an unfinished completeness check is exactly "
                    "the false green this mode exists to prevent."),
                "examples": [
                    f"verdict: {integrity_verdict['status']}, "
                    f"{integrity_verdict['blocks_unresolved']} block(s) and "
                    f"{integrity_verdict['tables_unresolved']} table(s) "
                    "unresolved, pages assessed "
                    f"{integrity_verdict['pages_processed']}"
                    f"/{integrity_verdict['pages_expected']}"],
            })
    elif integrity_mode == "shadow" and _verdict_incomplete:
        warnings.append({
            "code": "notes_integrity_shadow_needs_review",
            "count": 1,
            "message": (
                "The source-completeness check (running in shadow mode) "
                "would have flagged this run. Shadow mode never blocks, but "
                "the verdict is worth reading before filing."),
            "examples": [],
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
