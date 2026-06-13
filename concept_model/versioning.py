"""Reviewer-agent versioning core (docs/Archive/PLAN-reviewer-agent.md, Phase 2).

The reviewer pass writes its grounded fixes directly into the live
``run_concept_facts`` store — there is no write-gating. Safety comes
instead from *versioning*: before the reviewer touches anything we
snapshot the original extraction facts, and "Revert to original"
restores them. This module owns the three pure-backend helpers that make
that reversibility work:

* :func:`snapshot_facts` — copy a run's current facts into
  ``run_fact_snapshots`` (taken once, before the first reviewer pass).
* :func:`revert_to_original` — replace the run's live facts with the
  snapshot, recompute totals, and dismiss any reviewer flags/diff state.
* :func:`compute_review_diff` — diff the live facts against the snapshot
  for the Review tab, annotated with each change's reason + grounding.

None of these touch the workbook on disk — the workbook always rebuilds
from facts, so the facts snapshot is the whole backup.
"""
from __future__ import annotations

import logging
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


def _open_conn(db_path: str | Path) -> sqlite3.Connection:
    """Open the audit DB with the canonical pragmas. Caller closes."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    return conn


def snapshot_facts(db_path: str | Path, run_id: int) -> int:
    """Copy a run's current facts into ``run_fact_snapshots``.

    Called once per run, immediately before the reviewer pass writes
    anything (Step 9 ordering — this is the load-bearing invariant for
    reversibility). Re-running it OVERWRITES any prior snapshot for the
    run: the snapshot must always represent the original extraction, not
    a later reviewer state, so a manual re-review still reverts to the
    first extraction.

    Returns the number of fact rows snapshotted.
    """
    conn = _open_conn(db_path)
    try:
        # Atomic replace: clear the old snapshot, then copy the live facts
        # in one transaction so a crash mid-copy can't leave a half-written
        # backup that revert would later trust.
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM run_fact_snapshots WHERE run_id = ?", (run_id,)
        )
        conn.execute(
            """
            INSERT INTO run_fact_snapshots(
                run_id, concept_uuid, period, entity_scope, value,
                value_status, children_status, source, evidence, snapshot_at
            )
            SELECT run_id, concept_uuid, period, entity_scope, value,
                   value_status, children_status, source, evidence, ?
            FROM run_concept_facts WHERE run_id = ?
            """,
            (now, run_id),
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM run_fact_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        conn.commit()
        return int(count)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_snapshot(db_path: str | Path, run_id: int) -> bool:
    """Create the original-extraction snapshot if absent — atomically.

    Item 13 (snapshot race fix). The two-step ``if not has_snapshot():
    snapshot_facts()`` call site used two separate connections, so two
    concurrent reviewer passes could both observe "no snapshot yet" and the
    second would overwrite the first — destroying the original-extraction
    restore point. This folds the existence check and the create into ONE
    ``BEGIN IMMEDIATE`` transaction (write-lock-up-front, the pattern
    ``notes/persistence.py`` uses), so only one racer can ever create it.

    Create-if-absent ONLY: when a snapshot already exists this is a no-op that
    returns ``False`` and never re-copies — the snapshot must always stay the
    ORIGINAL extraction, never a later reviewer state (gotcha #21). Any
    overwrite capability is reserved for :func:`snapshot_facts` (test/internal),
    which is intentionally NOT what the reviewer path calls.

    Returns ``True`` when it created a new snapshot, ``False`` when one already
    existed.
    """
    conn = _open_conn(db_path)
    try:
        now = _now()
        # Take the write lock up front so the COUNT check and the INSERT are
        # one atomic unit — a concurrent ensure_snapshot blocks here until we
        # commit, then sees the row and no-ops.
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT COUNT(*) FROM run_fact_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        if existing:
            conn.rollback()  # release the lock; nothing to create
            return False
        conn.execute(
            """
            INSERT INTO run_fact_snapshots(
                run_id, concept_uuid, period, entity_scope, value,
                value_status, children_status, source, evidence, snapshot_at
            )
            SELECT run_id, concept_uuid, period, entity_scope, value,
                   value_status, children_status, source, evidence, ?
            FROM run_concept_facts WHERE run_id = ?
            """,
            (now, run_id),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def has_snapshot(db_path: str | Path, run_id: int) -> bool:
    """True when a reviewer snapshot exists for the run.

    The Review tab uses this as ``has_reviewer_version`` — a snapshot is
    only ever taken when the reviewer pass runs, so its presence is the
    signal that a reviewer version of the run exists.
    """
    conn = _open_conn(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM run_fact_snapshots WHERE run_id = ? LIMIT 1",
            (run_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def revert_to_original(db_path: str | Path, run_id: int) -> dict[str, Any]:
    """Restore a run's facts from its snapshot and undo reviewer state.

    Steps, all in one transaction so a partial revert can't leave the run
    in a mixed original/reviewer state:

    1. Replace ``run_concept_facts`` for the run with the snapshot rows.
    2. Mark every reviewer flag for the run ``dismissed`` (the diff they
       described no longer exists).

    Then run the cascade so dependent subtotals are recomputed from the
    restored leaves. The snapshot itself is left in place so a later
    re-review still reverts to the same original extraction.

    Returns ``{"reverted": bool, "facts_restored": int, "recomputed": bool,
    "cascade_ok": bool, "cascade_error": str | None}``.
    ``reverted`` is False when no snapshot exists (nothing to revert to) — the
    caller can surface that to the user instead of silently no-opping.
    ``cascade_ok`` (mirrored by the legacy ``recomputed``) is False when the
    restore succeeded but the post-restore cascade raised; ``cascade_error``
    then carries the exception text. Item 11: the failure is reported instead
    of swallowed so the Review tab can warn "values restored, but totals could
    not be recomputed" rather than show a silent stale-totals window.
    """
    from concept_model.cascade import recompute_after_turn

    conn = _open_conn(db_path)
    try:
        now = _now()
        # Acquire the write lock BEFORE checking for the snapshot, so the
        # existence check and the wipe-and-restore are one atomic unit. A
        # concurrent first-snapshot (auto-reviewer) or a second revert can no
        # longer slip between "does a snapshot exist?" and the DELETE.
        conn.execute("BEGIN IMMEDIATE")
        snap = conn.execute(
            "SELECT COUNT(*) FROM run_fact_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        if snap == 0:
            # No original backup — refuse rather than wipe the live facts.
            conn.rollback()  # release the write lock; nothing to do
            return {"reverted": False, "facts_restored": 0, "recomputed": False}

        # Wipe the live facts and re-insert the snapshot. We DELETE+INSERT
        # rather than UPSERT because the reviewer may have added facts that
        # don't exist in the snapshot — those must disappear on revert.
        conn.execute(
            "DELETE FROM run_concept_facts WHERE run_id = ?", (run_id,)
        )
        conn.execute(
            """
            INSERT INTO run_concept_facts(
                run_id, concept_uuid, period, entity_scope, value,
                value_status, children_status, source, evidence, updated_at
            )
            SELECT run_id, concept_uuid, period, entity_scope, value,
                   value_status, children_status, source, evidence, ?
            FROM run_fact_snapshots WHERE run_id = ?
            """,
            (now, run_id),
        )
        restored = conn.execute(
            "SELECT COUNT(*) FROM run_concept_facts WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        # The reviewer's flags described changes that no longer exist —
        # dismiss them so the Review tab clears.
        conn.execute(
            "UPDATE reviewer_flags SET status = 'dismissed', updated_at = ? "
            "WHERE run_id = ? AND status IN ('open', 'answered')",
            (now, run_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Recompute outside the transaction (cascade opens its own connection
    # and commits) so a recompute failure can't roll back the restore. Catch
    # + signal rather than propagate: the restore already committed, so a
    # cascade error means "restored but totals may be stale", not "revert
    # failed" — the caller surfaces a soft warning off ``recomputed``.
    recomputed = True
    cascade_error: str | None = None
    try:
        recompute_after_turn(db_path, run_id)
    except Exception as exc:  # noqa: BLE001 — restore already committed
        logging.getLogger(__name__).exception(
            "post-revert cascade failed for run %s", run_id
        )
        recomputed = False
        # Surface a stable, token-free message (the cascade reads only the DB,
        # so its exceptions never embed secrets) so the caller can show it.
        cascade_error = f"{type(exc).__name__}: {exc}"
    return {
        "reverted": True, "facts_restored": int(restored),
        "recomputed": recomputed,
        # Item 11: explicit cascade-health fields the API/UI surface.
        "cascade_ok": recomputed,
        "cascade_error": cascade_error,
    }


def compute_review_diff(db_path: str | Path, run_id: int) -> list[dict[str, Any]]:
    """Diff the run's live facts against its snapshot for the Review tab.

    Returns one entry per changed cell:
    ``{concept_uuid, sheet, row, col, label, period, entity_scope,
       original, current, reason, grounding, actor}``.

    A cell counts as changed when its value differs from the snapshot, or
    when it exists in one side but not the other (reviewer added / the
    snapshot had a value now cleared). ``reason`` / ``grounding`` come from
    the live fact's ``source`` / ``evidence`` (apply_fix writes the
    reviewer's reason into ``source`` and its PDF grounding into
    ``evidence``); ``actor`` is taken from the latest audit event for the
    concept so the UI can tell a reviewer leaf-fix from a cascade-derived
    total. Returns an empty list when no snapshot exists.
    """
    conn = _open_conn(db_path)
    try:
        snap_rows = conn.execute(
            "SELECT concept_uuid, period, entity_scope, value "
            "FROM run_fact_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        if not snap_rows:
            return []
        snapshot = {
            (r["concept_uuid"], r["period"], r["entity_scope"]): r["value"]
            for r in snap_rows
        }

        live_rows = conn.execute(
            "SELECT concept_uuid, period, entity_scope, value, source, evidence "
            "FROM run_concept_facts WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        live = {
            (r["concept_uuid"], r["period"], r["entity_scope"]): r
            for r in live_rows
        }

        # Concept metadata (sheet/row/col/label) for every concept that
        # appears on either side, fetched once.
        all_uuids = {k[0] for k in snapshot} | {k[0] for k in live}
        meta: dict[str, sqlite3.Row] = {}
        if all_uuids:
            placeholders = ",".join("?" for _ in all_uuids)
            for r in conn.execute(
                f"SELECT concept_uuid, canonical_label, display_label, "
                f"render_sheet, render_row, render_col FROM concept_nodes "
                f"WHERE concept_uuid IN ({placeholders})",
                tuple(all_uuids),
            ).fetchall():
                meta[r["concept_uuid"]] = r

        # Per-(concept, scope, period) physical target, for Group + matrix
        # (SOCIE) facts. concept_nodes.render_* is the PRIMARY coord (CY/Company
        # column B); Group Company-scope facts land in col D/E and SOCIE cells
        # route by concept_targets. Mirroring cell_resolver, prefer the target
        # coord so the Review tab's displayed cell + click-to-cell match the
        # workbook. Linear MFRS Company runs have no targets → fall back to
        # render_*.
        targets: dict[tuple[str, str, str], sqlite3.Row] = {}
        if all_uuids:
            placeholders = ",".join("?" for _ in all_uuids)
            for r in conn.execute(
                f"SELECT concept_uuid, entity_scope, period, target_sheet, "
                f"target_row, target_col FROM concept_targets "
                f"WHERE concept_uuid IN ({placeholders})",
                tuple(all_uuids),
            ).fetchall():
                targets[
                    (r["concept_uuid"], r["entity_scope"], r["period"])
                ] = r

        # Latest audit actor per (concept, period, scope), so a reviewer
        # edit to only the CY cell doesn't mislabel the untouched PY cell as
        # reviewer-touched. Keyed by the same tuple the diff iterates.
        actor_by_key: dict[tuple[str, str, str], str] = {}
        for r in conn.execute(
            "SELECT concept_uuid, period, entity_scope, actor "
            "FROM concept_fact_events WHERE run_id = ? AND id IN ("
            "  SELECT MAX(id) FROM concept_fact_events WHERE run_id = ? "
            "  GROUP BY concept_uuid, period, entity_scope)",
            (run_id, run_id),
        ).fetchall():
            actor_by_key[
                (r["concept_uuid"], r["period"], r["entity_scope"])
            ] = r["actor"]

        diff: list[dict[str, Any]] = []
        for key in sorted(set(snapshot) | set(live)):
            uuid, period, scope = key
            original = snapshot.get(key)
            live_row = live.get(key)
            current = live_row["value"] if live_row is not None else None
            if _values_equal(original, current) and (key in snapshot) == (key in live):
                continue
            m = meta.get(uuid)
            tgt = targets.get((uuid, scope, period))
            diff.append({
                "concept_uuid": uuid,
                "period": period,
                "entity_scope": scope,
                # Target coord (Group/SOCIE) wins; render_* is the fallback.
                "sheet": (
                    tgt["target_sheet"] if tgt
                    else (m["render_sheet"] if m else None)
                ),
                "row": (
                    tgt["target_row"] if tgt
                    else (m["render_row"] if m else None)
                ),
                "col": (
                    tgt["target_col"] if tgt
                    else (m["render_col"] if m else None)
                ),
                "label": (
                    (m["display_label"] or m["canonical_label"]) if m else None
                ),
                "original": original,
                "current": current,
                "reason": live_row["source"] if live_row is not None else None,
                "grounding": live_row["evidence"] if live_row is not None else None,
                "actor": actor_by_key.get(key),
            })
        return diff
    finally:
        conn.close()


def _values_equal(a: float | None, b: float | None) -> bool:
    """Compare two monetary values at cent scale (mirrors cascade tolerance)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(round(float(a), 2) - round(float(b), 2)) <= 0.01
