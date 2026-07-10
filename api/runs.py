"""Run history CRUD / read routes.

Endpoints:
  ``GET    /api/runs``                          — list with filters + pagination
  ``GET    /api/runs/{run_id}``                 — hydrated detail (agents + checks)
  ``GET    /api/runs/{run_id}/agents/{stmt}/trace`` — verbatim conversation trace
  ``PATCH  /api/runs/{run_id}``                 — persist pre-run config edits (draft)
  ``DELETE /api/runs/{run_id}``                 — DB-only delete
  ``GET    /api/runs/{run_id}/recheck``         — re-run cross-checks on current facts

The ``?from=/?to=`` → ``date_from/date_to`` rewrite middleware stays on the
app in server.py (APIRouter has no middleware hook). Shared helpers are read
through ``server.X`` at call time.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

import server
from server import RunConfigPatchRequest

logger = logging.getLogger("server")

router = APIRouter()


@router.get("/api/runs")
async def list_runs_endpoint(
    q: Optional[str] = None,
    status: Optional[str] = None,
    model: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    # Accept `from`/`to` aliases too so the frontend can use human-friendly
    # query params. FastAPI cannot parse a param named `from` (reserved
    # keyword), so we alias via the date-range middleware on the app.
):
    """List past runs with optional filters. Newest first by default."""
    from db import repository as repo
    # Clamp once, use for both the DB query AND the response payload.
    # Previously the response echoed the raw request values, so a caller
    # asking for limit=500 would get back 200 rows with limit=500 in the
    # payload, desyncing client-side pagination math (Load More offsets).
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    conn = server._open_audit_conn()
    try:
        summaries = repo.list_runs(
            conn,
            filename_substring=q,
            status=status,
            model=model,
            date_from=date_from,
            date_to=date_to,
            limit=safe_limit,
            offset=safe_offset,
        )
        total = repo.count_runs(
            conn,
            filename_substring=q,
            status=status,
            model=model,
            date_from=date_from,
            date_to=date_to,
        )
    finally:
        conn.close()
    return {
        "runs": [server._run_summary_to_dict(s) for s in summaries],
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
    }


@router.get("/api/runs/{run_id}")
async def get_run_detail_endpoint(run_id: int):
    """Return a hydrated detail view of a single run.

    Phase 7 / Phase 6.5: each agent now carries its persisted SSE-equivalent
    events so History can replay the tool timeline via buildToolTimeline()
    on the frontend. We also normalize a LEGACY `complete` payload shape
    (`{status: "succeeded", ...}`) written by the pre-Phase-6.5 post-run
    block into the live shape (`{success: bool, error: str | None}`) so
    the frontend only ever sees one terminal-row contract.

    Contract: frontend consumers (live SSE and history replay) MUST see
    the same `complete` shape: `{success: bool, error?: string}`.
    """
    from db import repository as repo
    from datetime import datetime
    conn = server._open_audit_conn()
    try:
        detail = repo.get_run_detail(conn, run_id)
        # v16 gold-standard eval: fetch the scorecard (if any) on the same
        # connection so the Eval tab is gated + populated from one round-trip.
        eval_score = (
            repo.fetch_eval_score_for_run(conn, run_id)
            if detail is not None else None
        )
    finally:
        conn.close()
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")

    def _event_ts_to_epoch_seconds(ts: str) -> float:
        """Convert the DB's ISO-string timestamp to float epoch seconds.

        The SSE client records `Date.now() / 1000` as `timestamp`, and
        buildToolTimeline multiplies that back up by 1000 to get ms. We
        match the same unit here so live and replay paths are pairwise
        compatible.
        """
        try:
            s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return 0.0

    def _normalize_event_payload(event_type: str, data: dict) -> dict:
        """Phase 7.4: migrate legacy `complete` payloads to the live shape.

        Pre-Phase-6.5 runs wrote `{status: "succeeded"|"failed", error,
        workbook_path, has_trace}`. The frontend terminal-row logic reads
        `data.success` and `data.error`, so we synthesise them here when
        they're missing. Original fields are preserved for debuggability.
        """
        if event_type == "complete" and "status" in data and "success" not in data:
            return {
                **data,
                "success": data.get("status") == "succeeded",
            }
        return data

    def _serialize_event(evt) -> dict:
        data = evt.payload if isinstance(evt.payload, dict) else {}
        return {
            "event": evt.event_type,
            "data": _normalize_event_payload(evt.event_type, data),
            "timestamp": _event_ts_to_epoch_seconds(evt.ts),
        }

    run = detail.run
    return {
        "id": run.id,
        "created_at": run.created_at,
        "pdf_filename": run.pdf_filename,
        "status": run.status,
        "session_id": run.session_id,
        "output_dir": run.output_dir,
        "merged_workbook_path": run.merged_workbook_path,
        "scout_enabled": run.scout_enabled,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "config": run.config,
        "filing_level": (run.config or {}).get("filing_level", "company"),
        "filing_standard": (run.config or {}).get("filing_standard", "mfrs"),
        # v10: sourced from the canonical `runs.orchestration` column,
        # not the config blob, so a corrupt/missing config still surfaces
        # the right path. Falls back to 'split' on legacy rows.
        "orchestration": getattr(run, "orchestration", "split"),
        # v16 gold-standard eval: the benchmark this run graded against (None
        # on normal runs — the frontend gates the Eval tab on it) plus the
        # scorecard dict (None when not graded).
        "benchmark_id": getattr(run, "benchmark_id", None),
        "eval_score": eval_score,
        # v30 evals workspace: the build that produced this run (None on legacy
        # rows), plus the repeat-group linkage when this run is one of N repeats.
        "app_version": getattr(run, "app_version", None),
        "repeat_group_id": getattr(run, "repeat_group_id", None),
        "repeat_index": getattr(run, "repeat_index", None),
        # v22 per-run notes-table style override (docs/PLAN-notes-table-theme.md).
        # None on a run with no override — the Notes tab then uses the firm
        # default from /api/config.
        "notes_table_style": getattr(run, "notes_table_style", None),
        "agents": [
            {
                "id": a.id,
                "statement_type": a.statement_type,
                "variant": a.variant,
                "model": a.model,
                "status": a.status,
                "started_at": a.started_at,
                "ended_at": a.ended_at,
                "workbook_path": a.workbook_path,
                "total_tokens": a.total_tokens,
                "total_cost": a.total_cost,
                # v17 (item 9): machine-readable failure class; None on
                # success / legacy rows. Frontend renders it as a badge.
                "error_type": getattr(a, "error_type", None),
                # v8 telemetry: per-agent token split + iteration counts, and
                # the per-turn metrics rows the Telemetry tab renders.
                "token_breakdown": {
                    "prompt_tokens": a.prompt_tokens,
                    "completion_tokens": a.completion_tokens,
                    "turn_count": a.turn_count,
                    "tool_call_count": a.tool_call_count,
                    # v15 cache telemetry: cache_read > 0 proves caching hit.
                    "cache_read_tokens": getattr(a, "cache_read_tokens", 0),
                    "cache_write_tokens": getattr(a, "cache_write_tokens", 0),
                },
                "turns": a.turns,
                "events": [_serialize_event(e) for e in a.events],
            }
            for a in detail.agents
        ],
        # v8 run-level rollup so the Overview metric strip + Telemetry tab can
        # show totals without re-summing per-agent on the client.
        "telemetry_rollup": {
            "total_tokens": sum(a.total_tokens or 0 for a in detail.agents),
            "total_cost": sum(a.total_cost or 0.0 for a in detail.agents),
            "prompt_tokens": sum(a.prompt_tokens or 0 for a in detail.agents),
            "completion_tokens": sum(a.completion_tokens or 0 for a in detail.agents),
            "turn_count": sum(a.turn_count or 0 for a in detail.agents),
            "tool_call_count": sum(a.tool_call_count or 0 for a in detail.agents),
            # v15 cache telemetry rollup — total cache reads/writes across the
            # run so the Telemetry tab can show a hit rate at a glance.
            "cache_read_tokens": sum(
                getattr(a, "cache_read_tokens", 0) or 0 for a in detail.agents
            ),
            "cache_write_tokens": sum(
                getattr(a, "cache_write_tokens", 0) or 0 for a in detail.agents
            ),
        },
        "cross_checks": [
            {
                "name": c.check_name,
                "status": c.status,
                "expected": c.expected,
                "actual": c.actual,
                "diff": c.diff,
                "tolerance": c.tolerance,
                "message": c.message,
                "target_sheet": c.target_sheet,
                "target_row": c.target_row,
            }
            for c in detail.cross_checks
        ],
    }


@router.get("/api/runs/{run_id}/agents/{statement}/trace")
async def get_agent_trace_endpoint(run_id: int, statement: str):
    """Serve the verbatim conversation trace for one agent of a run (v8).

    The trace holds exactly what was sent and returned each turn (text
    verbatim, binary elided, oversized cells capped) plus the per-turn token
    deltas. It lives on disk at `{output_dir}/{statement}_conversation_trace.json`
    — the hybrid-storage half that keeps heavy content out of SQLite.

    Security: `statement` is validated against the run's actual agent
    statement_types before touching the filesystem, so a caller can't
    path-traverse via the URL (e.g. `../../etc/passwd`).
    """
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        detail = repo.get_run_detail(conn, run_id)
    finally:
        conn.close()
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Only statement_types that actually belong to this run are addressable.
    known = {a.statement_type for a in detail.agents}
    if statement not in known:
        raise HTTPException(
            status_code=404,
            detail=f"No agent '{statement}' in run {run_id}",
        )

    output_dir = detail.run.output_dir
    if not output_dir:
        raise HTTPException(status_code=404, detail="Run has no output directory")

    # Defence-in-depth (peer-review [3]): `statement` is already whitelisted
    # against this run's agents above, and statement_type is system-generated,
    # so a traversal isn't reachable in normal operation. But a corrupt DB row
    # shouldn't be able to read outside the run's output dir — confirm the
    # resolved path stays under output_dir before touching the filesystem.
    out_root = Path(output_dir).resolve()
    trace_path = (out_root / f"{statement}_conversation_trace.json").resolve()
    if not trace_path.is_relative_to(out_root):
        raise HTTPException(status_code=400, detail="Invalid trace path")
    if not trace_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No trace captured for {statement} (older run or failed early)",
        )

    try:
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not read trace: {exc}"
        )
    return payload


@router.patch("/api/runs/{run_id}")
async def patch_run_config_endpoint(run_id: int, body: RunConfigPatchRequest):
    """Persist pre-run config edits onto a draft run.

    Called from the frontend PreRunPanel as the user picks statements /
    level / standard / model / notes — debounced ~500ms client-side.
    Only drafts (status='draft') accept PATCH; once a run starts, its
    stored config is the audit-trail record of what was actually
    extracted, so we refuse to mutate it.

    Body validation runs before this handler; an invalid filing_level
    enum surfaces as a 422 from FastAPI/Pydantic. Unset fields are
    excluded from the merge so partial updates preserve prior choices.

    Atomicity (peer-review MEDIUM #4): the not-draft guard is enforced
    by a SQL `WHERE status='draft'` clause inside `update_run_config`,
    not just by the upfront fetch_run check. The two-step approach
    (check, then update) opens a TOCTOU window where a /start request
    in flight could flip the row 'draft' → 'running' between our check
    and our update — silently mutating a started run's audit-trail
    config. We still do the upfront fetch so we can distinguish 404
    (no row at all) from 409 (row exists but it's not a draft).
    """
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status != "draft":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Run config is locked once the run has started. "
                    f"Current status: {run.status}"
                ),
            )

        patch = body.model_dump(exclude_unset=True)
        if not patch:
            # Empty PATCH is a no-op — return the current config so the
            # client's optimistic state stays in sync.
            return {"id": run_id, "config": run.config or {}}

        merged = repo.update_run_config(conn, run_id, patch)
        if merged is None:
            # Race: the row flipped between fetch_run and the UPDATE's
            # WHERE clause. Same user-facing semantics as the upfront
            # check above.
            raise HTTPException(
                status_code=409,
                detail=(
                    "Run config is locked once the run has started "
                    "(state changed during the request)."
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return {"id": run_id, "config": merged}


@router.patch("/api/runs/{run_id}/notes_table_style")
async def patch_run_notes_table_style_endpoint(run_id: int, body: dict):
    """Set (or clear) a run's notes-table style override.

    Unlike PATCH /api/runs/{id} (draft-only config), this is editable on ANY
    run status — notes review happens AFTER extraction, so the reviewer can
    re-theme a completed run's tables (docs/PLAN-notes-table-theme.md). Body:
    ``{"notes_table_style": <object> | null}``; a null or empty object clears
    the override so the run falls back to the firm default. The object is
    validated/cleaned with the same rules as the firm default (400 on a bad
    colour / enum / range).
    """
    from db import repository as repo
    from api.config_routes import _validate_notes_table_style

    raw = body.get("notes_table_style")
    # ONLY null or an empty object clears the override. A falsy-but-malformed
    # payload (false / "" / []) must still hit validation and 400, not silently
    # clear (peer-review LOW #6).
    if raw is None or raw == {}:
        style = None
    else:
        style = _validate_notes_table_style(raw)

    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        repo.set_run_notes_table_style(conn, run_id, style)
        conn.commit()
    finally:
        conn.close()

    return {"id": run_id, "notes_table_style": style}


@router.delete("/api/runs/drafts")
async def delete_draft_runs_endpoint():
    """Bulk-delete abandoned draft runs (uploads that were never started).

    Registered BEFORE ``/api/runs/{run_id}`` so the literal ``drafts`` segment
    isn't matched as an int run id. Draft-only by construction (the repo query
    filters ``status = 'draft'``), and any draft whose session is mid-start is
    skipped, so this can never delete real or in-flight work. Returns the count
    removed.
    """
    from db import repository as repo
    conn = server._open_audit_conn()
    try:
        removed = repo.delete_draft_runs(
            conn, protected_session_ids=set(server.active_runs)
        )
        conn.commit()
    finally:
        conn.close()
    return {"deleted": removed}


@router.delete("/api/runs/{run_id}")
async def delete_run_endpoint(run_id: int):
    """Hard-delete a run row from the DB.

    By design, this does NOT touch the on-disk `output/{session_id}/`
    folder. Safer default: disk cleanup can come later if needed.

    Safety guards (peer-review fix for [CRITICAL] deletion of in-flight
    runs): reject deletion if the run is still executing. The DELETE
    cascades through run_agents, agent_events, extracted_fields, and
    cross_checks — so wiping the parent row mid-extraction either
    orphans child inserts or triggers FK violations on the coordinator's
    next write. Two independent checks cover both the happy path and
    the stale-row case:

      1. `runs.status == 'running'` — the authoritative DB state.
      2. `session_id in active_runs` — the in-memory lock that the
         run_multi_agent_stream endpoint holds for the lifetime of an
         extraction. Catches edge cases where the DB row was left in a
         terminal state by a crash but a fresh extraction is happening
         right now on the same session.
    """
    from db import repository as repo
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        if run.status == "running":
            raise HTTPException(
                status_code=409,
                detail="Cannot delete a run that is still running. "
                       "Wait for it to finish (or abort it) before deleting.",
            )
        # Second-layer guard: session still actively streaming.
        if run.session_id and run.session_id in server.active_runs:
            raise HTTPException(
                status_code=409,
                detail="An active extraction is running against this "
                       "session. Cannot delete while it is still in flight.",
            )

        removed = repo.delete_run(conn, run_id)
        conn.commit()
    finally:
        conn.close()
    if not removed:
        # Race: row vanished between fetch_run and delete_run. Treat as
        # "already deleted" and report 404.
        raise HTTPException(status_code=404, detail="Run not found")
    return {"deleted": run_id}


@router.get("/api/runs/{run_id}/recheck")
async def recheck_endpoint(run_id: int):
    """Phase 4.3 — re-run cross-checks against the current (edited) DB facts.

    Lets the review UI refresh validation after manual edits without a full
    pipeline re-run. Returns the serialised cross-check results, or 404 if the
    run is unknown / has nothing to check. Heavy (openpyxl) work runs off the
    event loop.
    """
    from db import repository as repo
    conn = server._open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    results = await asyncio.to_thread(server._recheck_from_facts, run_id)
    if results is None:
        raise HTTPException(
            status_code=404,
            detail="No facts to re-check for this run (canonical mode off, or "
                   "no succeeded statements).",
        )
    return {"run_id": run_id, "results": results}
