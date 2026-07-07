"""Notes-reviewer versioning core (docs/PLAN.md — Notes Reviewer, Phase 1).

The notes reviewer writes its grounded fixes directly into the live
``notes_cells`` store — there is no write-gating. Safety comes from
*versioning*, exactly as the face reviewer does over ``run_concept_facts``
(``concept_model/versioning.py``): before the reviewer touches anything we
snapshot the original extraction prose, and "Revert to original" restores it.

This module owns the pure-backend helpers that make that reversibility work:

* :func:`ensure_notes_snapshot` — copy a run's current prose into
  ``run_notes_cell_snapshots`` IF ABSENT (create-once, before the first pass).
* :func:`snapshot_notes_cells` — overwrite-snapshot (test/internal only).
* :func:`revert_notes_to_original` — replace the run's live prose with the
  snapshot. Crucially a **full-set replace** (delete-all-then-restore), so a
  reviewer-AUTHORED row that did not exist at snapshot time is removed on revert.
* :func:`compute_notes_review_diff` — diff live prose vs the snapshot for the
  Notes-review panel.

``notes_cells`` is the canonical store; the xlsx is overlaid from it at
download time. So this prose snapshot is the whole backup — no workbook
juggling.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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


_SNAPSHOT_COLS = (
    "sheet, row, label, html, evidence, source_pages, concept_uuid, style_source"
)


def snapshot_notes_cells(db_path: str | Path, run_id: int) -> int:
    """Copy a run's current prose into ``run_notes_cell_snapshots`` (OVERWRITE).

    Test/internal helper — the live pass calls :func:`ensure_notes_snapshot`
    (create-if-absent) so a manual re-review never overwrites the original
    extraction backup. Returns the number of rows snapshotted.
    """
    conn = _open_conn(db_path)
    try:
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM run_notes_cell_snapshots WHERE run_id = ?", (run_id,)
        )
        conn.execute(
            f"""
            INSERT INTO run_notes_cell_snapshots(
                run_id, {_SNAPSHOT_COLS}, snapshot_at
            )
            SELECT run_id, {_SNAPSHOT_COLS}, ?
            FROM notes_cells WHERE run_id = ?
            """,
            (now, run_id),
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM run_notes_cell_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        # Record the taken-marker so has_notes_snapshot / revert work even when
        # the snapshot captured zero rows (empty original prose).
        conn.execute(
            "INSERT INTO run_notes_review_state(run_id, snapshot_at) VALUES(?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET snapshot_at = excluded.snapshot_at",
            (run_id, now),
        )
        conn.commit()
        return int(count)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_notes_snapshot(db_path: str | Path, run_id: int) -> bool:
    """Create the original-extraction prose snapshot if absent — atomically.

    Mirrors ``concept_model.versioning.ensure_snapshot``: the existence check
    and the create run in one ``BEGIN IMMEDIATE`` transaction so two concurrent
    reviewer passes can't both create a snapshot (the second would overwrite the
    original restore point). Create-if-absent ONLY — when a snapshot exists this
    is a no-op returning ``False`` and never re-copies, so the snapshot always
    stays the ORIGINAL extraction, never a later reviewer state.

    Returns ``True`` when it created a new snapshot, ``False`` when one existed.
    """
    conn = _open_conn(db_path)
    try:
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        # The taken-marker (not row count) decides existence, so an empty
        # original snapshot still counts as "taken".
        existing = conn.execute(
            "SELECT 1 FROM run_notes_review_state WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if existing:
            conn.rollback()
            return False
        conn.execute(
            f"""
            INSERT INTO run_notes_cell_snapshots(
                run_id, {_SNAPSHOT_COLS}, snapshot_at
            )
            SELECT run_id, {_SNAPSHOT_COLS}, ?
            FROM notes_cells WHERE run_id = ?
            """,
            (now, run_id),
        )
        conn.execute(
            "INSERT INTO run_notes_review_state(run_id, snapshot_at) VALUES(?, ?)",
            (run_id, now),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def has_notes_snapshot(db_path: str | Path, run_id: int) -> bool:
    """True when a notes-reviewer prose snapshot exists for the run."""
    conn = _open_conn(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM run_notes_review_state WHERE run_id = ? LIMIT 1",
            (run_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (name,),
    ).fetchone() is not None


def revert_notes_to_original(db_path: str | Path, run_id: int) -> dict[str, Any]:
    """Restore a run's prose from its snapshot and undo reviewer state.

    Full-set replace, in one transaction:

    1. DELETE every live ``notes_cells`` row for the run, then re-INSERT the
       snapshot set. We delete-then-insert (not upsert) precisely so a
       reviewer-AUTHORED row absent from the snapshot disappears on revert —
       the tombstone behaviour peer-review #2 called for.
    2. Reconcile workbook tombstones (v25) so the download overlay matches the
       restored prose: clear every tombstone for the run (a cleared/moved row
       is back in ``notes_cells`` now and must NOT be blanked), then re-tombstone
       any reviewer-AUTHORED coordinate (present live, absent from the snapshot)
       so its prose — already flattened into the xlsx by the reviewer's
       post-pass refresh — is blanked on the next download. Guarded behind a
       table-existence check so revert works before the v25 table ships.
    3. Dismiss the run's notes-review flags (the diff they described is gone).
       Guarded behind a table-existence check so this works before the v24
       flags table ships.

    Returns ``{"reverted": bool, "cells_restored": int}``. ``reverted`` is False
    when no snapshot exists (nothing to revert to) — the caller surfaces that
    instead of silently wiping live prose.
    """
    conn = _open_conn(db_path)
    try:
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        # Key off the taken-marker, NOT row count: a run whose original prose
        # was empty has a valid (zero-row) snapshot, and revert must still wipe
        # any reviewer-authored cells back to that empty original.
        marker = conn.execute(
            "SELECT 1 FROM run_notes_review_state WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if marker is None:
            conn.rollback()
            return {"reverted": False, "cells_restored": 0}

        # Reviewer-AUTHORED coords = present live, absent from the snapshot.
        # Captured BEFORE the delete so we can blank their xlsx prose on revert.
        live_coords = {
            (r["sheet"], int(r["row"])) for r in conn.execute(
                "SELECT sheet, row FROM notes_cells WHERE run_id = ?", (run_id,)
            ).fetchall()
        }
        snap_coords = {
            (r["sheet"], int(r["row"])) for r in conn.execute(
                "SELECT sheet, row FROM run_notes_cell_snapshots WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        authored_coords = live_coords - snap_coords

        conn.execute("DELETE FROM notes_cells WHERE run_id = ?", (run_id,))
        conn.execute(
            f"""
            INSERT INTO notes_cells(
                run_id, {_SNAPSHOT_COLS}, updated_at
            )
            SELECT run_id, {_SNAPSHOT_COLS}, ?
            FROM run_notes_cell_snapshots WHERE run_id = ?
            """,
            (now, run_id),
        )
        restored = conn.execute(
            "SELECT COUNT(*) FROM notes_cells WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        # Workbook tombstones (v25): reset to exactly the authored set so the
        # download overlay restores cleared/moved rows and blanks authored ones.
        if _table_exists(conn, "notes_cell_tombstones"):
            conn.execute(
                "DELETE FROM notes_cell_tombstones WHERE run_id = ?", (run_id,)
            )
            for sheet, row in sorted(authored_coords):
                conn.execute(
                    "INSERT INTO notes_cell_tombstones(run_id, sheet, row, "
                    "created_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(run_id, sheet, row) DO NOTHING",
                    (run_id, sheet, row, now),
                )
        if _table_exists(conn, "notes_review_flags"):
            conn.execute(
                "UPDATE notes_review_flags SET status = 'dismissed', "
                "updated_at = ? WHERE run_id = ? AND status IN ('open', 'answered')",
                (now, run_id),
            )
        conn.commit()
        return {"reverted": True, "cells_restored": int(restored)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def compute_notes_review_diff(
    db_path: str | Path, run_id: int,
) -> list[dict[str, Any]]:
    """Diff the run's live prose against its snapshot for the Notes-review panel.

    One entry per changed cell, keyed by (sheet, row):
    ``{sheet, row, label, change, original_html, current_html, evidence}`` where
    ``change`` is ``"edited"`` | ``"authored"`` (new since snapshot) |
    ``"cleared"`` (present in snapshot, gone now). Returns ``[]`` when no
    snapshot exists.
    """
    conn = _open_conn(db_path)
    try:
        # Existence keys off the taken-marker, NOT row count — a valid snapshot
        # of an empty original captures zero rows, and a reviewer-authored cell
        # after it must still show as "authored" in the diff (peer-review #3).
        marker = conn.execute(
            "SELECT 1 FROM run_notes_review_state WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if marker is None:
            return []
        snap_rows = conn.execute(
            "SELECT sheet, row, label, html FROM run_notes_cell_snapshots "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        snapshot = {(r["sheet"], r["row"]): r for r in snap_rows}

        live_rows = conn.execute(
            "SELECT sheet, row, label, html, evidence FROM notes_cells "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        live = {(r["sheet"], r["row"]): r for r in live_rows}

        diff: list[dict[str, Any]] = []
        for key in sorted(set(snapshot) | set(live)):
            sheet, row = key
            snap = snapshot.get(key)
            cur = live.get(key)
            orig_html = snap["html"] if snap is not None else None
            cur_html = cur["html"] if cur is not None else None
            if snap is not None and cur is not None and orig_html == cur_html:
                continue  # unchanged
            if snap is None:
                change = "authored"
            elif cur is None:
                change = "cleared"
            else:
                change = "edited"
            diff.append({
                "sheet": sheet,
                "row": row,
                "label": (cur["label"] if cur is not None else snap["label"]),
                "change": change,
                "original_html": orig_html,
                "current_html": cur_html,
                "evidence": (cur["evidence"] if cur is not None else None),
            })
        return diff
    finally:
        conn.close()
