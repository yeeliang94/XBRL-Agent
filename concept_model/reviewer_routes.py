"""Reviewer-tab API — read surface + flag triage (docs/Archive/PLAN-reviewer-agent.md).

Two endpoints register cleanly as a standalone module (no server import,
mirroring ``concepts_routes.py`` / ``facts_api.py``):

* ``GET  /api/runs/{id}/review`` — everything the Review tab renders:
  whether a reviewer version exists, the original → reviewer diff, the
  reviewer flags, and the run's cross-check rows.
* ``POST /api/runs/{id}/flags/{flag_id}/answer`` — attach free-text human
  guidance to a flag and move it ``open → answered``.

The heavier endpoints that need server orchestration (re-review launches
the reviewer agent; revert re-exports the workbook) live in ``server.py``.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel

from concept_model.versioning import compute_review_diff, has_snapshot


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


_HUMAN_ANSWER_MAX = 8000


class FlagAnswer(BaseModel):
    human_answer: str


def register_reviewer_routes(app, audit_db_getter) -> None:
    def _conn():
        c = sqlite3.connect(str(audit_db_getter()))
        c.execute("PRAGMA foreign_keys = ON")
        c.row_factory = sqlite3.Row
        return c

    @app.get("/api/runs/{run_id}/review")
    def get_review(run_id: int):
        """Serve the diff + flags + cross-checks for the Review tab."""
        db_path = audit_db_getter()
        conn = _conn()
        try:
            run = conn.execute(
                "SELECT id FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")

            # Only ACTIVE flags (open + answered) belong in the main list.
            # Revert dismisses a run's flags; returning dismissed/resolved
            # rows here would let the UI render stale, still-answerable flags
            # under a "No reviewer changes" header (peer-review MEDIUM). This
            # matches the has_reviewer_version definition below (open +
            # answered), so the header and the flag list never disagree.
            flags = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, concept_uuid, target_sheet, target_row, "
                    "category, reasoning, pdf_page, applied_fix, status, "
                    "human_answer, created_at, updated_at "
                    "FROM reviewer_flags WHERE run_id = ? "
                    "AND status IN ('open', 'answered') "
                    "ORDER BY created_at, id",
                    (run_id,),
                ).fetchall()
            ]
            cross_checks = [
                dict(r)
                for r in conn.execute(
                    "SELECT check_name, status, expected, actual, diff, "
                    "tolerance, message, target_sheet, target_row "
                    "FROM cross_checks WHERE run_id = ? ORDER BY id",
                    (run_id,),
                ).fetchall()
            ]
        finally:
            conn.close()

        # The diff helper opens its own connection (it runs multiple queries
        # + metadata joins); call it after closing ours.
        diff = compute_review_diff(db_path, run_id)
        # "has_reviewer_version" means there are ACTIVE reviewer changes to
        # review — a non-empty diff or a still-live flag. It is NOT merely
        # "a snapshot exists": revert keeps the snapshot in place (so a later
        # re-review still goes back to the original extraction), but after a
        # revert the diff is empty and flags are dismissed, so the Review tab
        # correctly shows no reviewer version (Step 14 verify).
        # ``flags`` is already filtered to active (open + answered) above, so
        # its presence is the flag signal here.
        has_reviewer_version = (
            has_snapshot(db_path, run_id)
            and (bool(diff) or bool(flags))
        )
        return {
            "run_id": run_id,
            "has_reviewer_version": has_reviewer_version,
            "diff": diff,
            "flags": flags,
            "cross_checks": cross_checks,
        }

    @app.post("/api/runs/{run_id}/flags/{flag_id}/answer")
    def answer_flag(run_id: int, flag_id: int, body: FlagAnswer):
        """Attach human guidance to a flag; move it open → answered."""
        if not (body.human_answer and body.human_answer.strip()):
            raise HTTPException(
                status_code=400, detail="human_answer must be non-empty.",
            )
        # Bounded: this free text is folded verbatim into the next re-review
        # system prompt, so an unbounded value is a DB-bloat + prompt-stuffing /
        # cost vector. Handler-level check (not a Pydantic Field) so the 422
        # carries a readable message instead of a validation-error list.
        if len(body.human_answer) > _HUMAN_ANSWER_MAX:
            raise HTTPException(
                status_code=422,
                detail=f"human_answer exceeds {_HUMAN_ANSWER_MAX} characters.",
            )
        conn = _conn()
        try:
            existing = conn.execute(
                "SELECT id, status FROM reviewer_flags WHERE id = ? "
                "AND run_id = ?",
                (flag_id, run_id),
            ).fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Flag not found")
            conn.execute(
                "UPDATE reviewer_flags SET human_answer = ?, status = "
                "'answered', updated_at = ? WHERE id = ?",
                (body.human_answer.strip(), _now(), flag_id),
            )
            conn.commit()
            return {"ok": True, "id": flag_id, "status": "answered"}
        finally:
            conn.close()
