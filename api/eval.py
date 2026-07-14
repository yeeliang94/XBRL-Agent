"""Gold-standard eval / benchmark routes (docs/PLAN-eval-benchmark.md, Step 7).

Thin HTTP shell over ``eval/store.py`` (benchmark CRUD + gold grid) and the
per-run scorecard in ``db/repository.py``. Shared helpers are reached through
``server.X`` at call time, matching the other ``api/`` routers.

Endpoints:
  GET    /api/benchmarks                       — list the library
  POST   /api/benchmarks                       — create from an uploaded xlsx
  POST   /api/benchmarks/from-run              — seed from a finished run's facts
  GET    /api/benchmarks/{id}                  — one benchmark (+ template set)
  DELETE /api/benchmarks/{id}                  — archive (default) / ?hard=true
                                                 admin-only cascade delete
  POST   /api/benchmarks/{id}/unarchive        — restore an archived benchmark
  GET    /api/benchmarks/{id}/concepts         — gold grid (ConceptsPage reuse)
  PATCH  /api/benchmarks/{id}/facts            — spot-edit one gold value
  GET    /api/runs/{id}/eval                   — the run's scorecard
  POST   /api/runs/{id}/re-grade               — re-grade against current gold
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

import server

# Benchmark/eval routes are authenticated (the `/api/*` middleware enforces
# that globally, gotcha #24) but NOT admin-gated — the Evals workspace is open
# to all signed-in users (docs/PLAN-evals-workspace.md, decision #6).

logger = logging.getLogger("server")

router = APIRouter()


class GoldFactPatch(BaseModel):
    concept_uuid: str
    period: str = "CY"
    entity_scope: str = "Company"
    value: Optional[float] = None


class BenchmarkFromRun(BaseModel):
    run_id: int
    name: str
    document: Optional[str] = None


@router.get("/api/benchmarks")
async def list_benchmarks_endpoint(include_archived: bool = False):
    from eval import store

    conn = server._open_audit_conn()
    try:
        return {"benchmarks": store.list_benchmarks(
            conn, include_archived=include_archived
        )}
    finally:
        conn.close()


@router.post("/api/benchmarks")
async def create_benchmark_endpoint(
    file: UploadFile = File(...),
    name: str = Form(...),
    filing_standard: str = Form("mfrs"),
    filing_level: str = Form("company"),
    document: Optional[str] = Form(None),
):
    """Create a benchmark from a human-filled MBRS template workbook.

    The template set is auto-detected from the workbook's sheets; gold facts
    are reverse-ingested in the same transaction. Rejects a non-xlsx upload, a
    bad standard/level, and a workbook matching no template (all 4xx).
    """
    from eval import store

    if filing_standard not in ("mfrs", "mpers"):
        raise HTTPException(status_code=400, detail="filing_standard must be mfrs or mpers")
    if filing_level not in ("company", "group"):
        raise HTTPException(status_code=400, detail="filing_level must be company or group")
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Only .xlsx / .xlsm workbooks are accepted.")

    # Stream the upload to a temp file (capped like PDF uploads) so the whole
    # workbook never lives in memory; ingest reads it, then we delete it.
    _CHUNK = 1 * 1024 * 1024
    total = 0
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    try:
        try:
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > server.MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Max size is "
                               f"{server.MAX_UPLOAD_SIZE // (1024 * 1024)}MB.",
                    )
                tmp.write(chunk)
        finally:
            tmp.close()

        conn = server._open_audit_conn()
        try:
            result = store.create_benchmark_from_workbook(
                conn,
                name=name,
                document=document or file.filename,
                filing_standard=filing_standard,
                filing_level=filing_level,
                xlsx_path=tmp.name,
            )
            conn.commit()
        except ValueError as exc:
            # Loud rejection (wrong file / no matching template) → 422 so the
            # half-made benchmark rolls back with the transaction.
            conn.rollback()
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception:
            conn.rollback()
            logger.exception("benchmark creation failed")
            raise HTTPException(status_code=500, detail="Benchmark creation failed.")
        finally:
            conn.close()
        return {"ok": True, **result}
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass


# Declared figure unit → the multiplier applied to every ingested value. The
# user MUST declare this (decision #4); a wrong declaration is caught by the
# ingest report's magnitude backstop, not silently swallowed.
_UNIT_SCALE = {"full": 1.0, "thousands": 1000.0}


def _validate_column_map(cm) -> None:
    """Reject a syntactically-valid but wrong-shaped column map with a 400 (a
    user-fixable error), instead of letting it become a 500 inside ingest.

    Expected: ``{sheet: {"label_column": str, "columns": {role: col}}}``."""
    if not isinstance(cm, dict) or not cm:
        raise HTTPException(
            status_code=400,
            detail="column_map must be a non-empty object keyed by sheet name.",
        )
    for sheet, cfg in cm.items():
        if not isinstance(cfg, dict):
            raise HTTPException(
                status_code=400,
                detail=f"column_map[{sheet!r}] must be an object with "
                       "'label_column' and 'columns'.",
            )
        if not isinstance(cfg.get("label_column"), str) or not cfg["label_column"]:
            raise HTTPException(
                status_code=400,
                detail=f"column_map[{sheet!r}].label_column must be a column letter.",
            )
        cols = cfg.get("columns")
        if not isinstance(cols, dict) or not cols:
            raise HTTPException(
                status_code=400,
                detail=f"column_map[{sheet!r}].columns must be a non-empty "
                       "object mapping roles to column letters.",
            )
        for role, col in cols.items():
            if not isinstance(col, str) or not col:
                raise HTTPException(
                    status_code=400,
                    detail=f"column_map[{sheet!r}].columns[{role!r}] must be a "
                           "column letter.",
                )


def _parse_template_ids(raw: str) -> list[str]:
    """Accept the template set as a JSON array or a comma-separated string."""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        import json

        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return [str(t) for t in value] if isinstance(value, list) else []
    return [t.strip() for t in raw.split(",") if t.strip()]


@router.post("/api/benchmarks/from-mtool")
async def create_benchmark_from_mtool_endpoint(
    file: UploadFile = File(...),
    name: str = Form(...),
    filing_standard: str = Form("mfrs"),
    filing_level: str = Form("company"),
    unit: str = Form(...),            # 'full' | 'thousands' — MANDATORY
    template_ids: str = Form(...),    # JSON array or comma-separated
    document: Optional[str] = Form(None),
    column_map: Optional[str] = Form(None),  # optional JSON override
):
    """Create a benchmark by reverse-ingesting a human-filled mTool workbook.

    The operator declares the figure unit (no auto-guess) and the statement
    variants (variant-precise template set — gotcha #21). Numeric gold + prose
    footnotes are captured. Low-confidence column detection with no explicit
    ``column_map`` is refused with an actionable 422.
    """
    from eval import store
    from eval.mtool_ingest import ColumnDetectionError

    if filing_standard not in ("mfrs", "mpers"):
        raise HTTPException(status_code=400, detail="filing_standard must be mfrs or mpers")
    if filing_level not in ("company", "group"):
        raise HTTPException(status_code=400, detail="filing_level must be company or group")
    if unit not in _UNIT_SCALE:
        raise HTTPException(
            status_code=400,
            detail="unit must be declared as 'full' or 'thousands'.",
        )
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Only .xlsx / .xlsm mTool workbooks are accepted.")
    ids = _parse_template_ids(template_ids)
    if not ids:
        raise HTTPException(
            status_code=400,
            detail="Select at least one statement variant (template_ids).",
        )
    override = None
    if column_map:
        import json

        try:
            override = json.loads(column_map)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="column_map is not valid JSON.")
        _validate_column_map(override)

    _CHUNK = 1 * 1024 * 1024
    total = 0
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    try:
        try:
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > server.MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Max size is "
                               f"{server.MAX_UPLOAD_SIZE // (1024 * 1024)}MB.",
                    )
                tmp.write(chunk)
        finally:
            tmp.close()

        conn = server._open_audit_conn()
        try:
            result = store.create_benchmark_from_mtool(
                conn,
                name=name,
                document=document or file.filename,
                filing_standard=filing_standard,
                filing_level=filing_level,
                template_ids=ids,
                xlsx_path=tmp.name,
                unit_scale=_UNIT_SCALE[unit],
                column_map_override=override,
            )
            conn.commit()
        except ColumnDetectionError as exc:
            conn.rollback()
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc) + ". Provide an explicit column map.",
                    "low_confidence_sheets": exc.low_sheets,
                },
            )
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception:
            conn.rollback()
            logger.exception("benchmark from-mtool creation failed")
            raise HTTPException(status_code=500, detail="Benchmark creation failed.")
        finally:
            conn.close()
        return {"ok": True, **result}
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass


@router.get("/api/eval/templates")
async def list_eval_templates_endpoint(
    standard: str = "mfrs", level: str = "company"
):
    """List the face template variants imported for a ``{standard}-{level}``
    family, so the mTool-gold form can offer a variant-precise picker (the
    from-mtool endpoint requires an explicit template_ids set — gotcha #21).

    Each entry: ``{template_id, statement, variant, label}``. Notes templates
    are excluded (they're reviewed in the Notes tab, not graded here).
    """
    if standard not in ("mfrs", "mpers"):
        raise HTTPException(status_code=400, detail="standard must be mfrs or mpers")
    if level not in ("company", "group"):
        raise HTTPException(status_code=400, detail="level must be company or group")
    from notes_types import notes_template_ids

    notes_ids = sorted(notes_template_ids())
    family = f"{standard}-{level}-"
    conn = server._open_audit_conn()
    try:
        not_in = (
            f"AND template_id NOT IN ({','.join('?' * len(notes_ids))})"
            if notes_ids else ""
        )
        rows = conn.execute(
            f"SELECT DISTINCT template_id FROM concept_nodes "
            f"WHERE template_id LIKE ? {not_in} ORDER BY template_id",
            (family + "%", *notes_ids),
        ).fetchall()
    finally:
        conn.close()

    out = []
    for (tid,) in rows:
        parts = tid.split("-")
        statement = parts[2].upper() if len(parts) >= 3 else "OTHER"
        variant = parts[3] if len(parts) >= 4 else ""
        label = f"{statement}{f' · {variant}' if variant else ''}"
        out.append({
            "template_id": tid, "statement": statement,
            "variant": variant, "label": label,
        })
    return {"templates": out}


@router.post("/api/benchmarks/from-run")
async def create_benchmark_from_run_endpoint(body: BenchmarkFromRun):
    """Seed a benchmark directly from a finished run's extracted facts.

    The lossless alternative to uploading a workbook: copies the run's
    LEAF/MATRIX_CELL facts (all sub-sheet + matrix leaves included) straight
    into gold, sidestepping the openpyxl formula-cache loss that an
    un-recalculated ``.xlsx`` upload suffers. Rejects an unknown or
    not-yet-finished run (422).
    """
    from eval import store

    conn = server._open_audit_conn()
    try:
        result = store.create_benchmark_from_run(
            conn,
            name=body.name,
            run_id=body.run_id,
            document=body.document,
        )
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        conn.rollback()
        logger.exception("benchmark from-run creation failed")
        raise HTTPException(status_code=500, detail="Benchmark creation failed.")
    finally:
        conn.close()
    return {"ok": True, **result}


@router.get("/api/benchmarks/{benchmark_id}")
async def get_benchmark_endpoint(benchmark_id: int):
    from eval import store

    conn = server._open_audit_conn()
    try:
        bench = store.get_benchmark(conn, benchmark_id)
    finally:
        conn.close()
    if bench is None:
        raise HTTPException(status_code=404, detail="Benchmark not found")
    return bench


@router.delete("/api/benchmarks/{benchmark_id}")
async def delete_benchmark_endpoint(
    benchmark_id: int, request: Request, hard: bool = False,
):
    """Archive the benchmark (default — scores + trends survive, Step 9).
    ``?hard=true`` is the admin-only true-mistake path: it CASCADE-destroys
    the gold AND every historical score; the archive response carries
    ``scores_kept`` so the UI can state what a hard delete would cost."""
    from auth import routes as auth_routes
    from eval import store

    conn = server._open_audit_conn()
    try:
        if store.get_benchmark(conn, benchmark_id) is None:
            raise HTTPException(status_code=404, detail="Benchmark not found")
        scores = store.score_count_for_benchmark(conn, benchmark_id)
        if hard:
            denied = auth_routes._require_admin(conn, request)
            if denied is not None:
                return denied
            store.delete_benchmark(conn, benchmark_id)
            conn.commit()
            return {"ok": True, "id": benchmark_id, "hard_deleted": True,
                    "scores_destroyed": scores}
        store.archive_benchmark(conn, benchmark_id)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": benchmark_id, "archived": True,
            "scores_kept": scores}


@router.post("/api/benchmarks/{benchmark_id}/scale-verified")
async def mark_scale_verified_endpoint(benchmark_id: int):
    """Clear the 'scale unverified' badge on an mTool-derived benchmark
    (Step 14) — pressed after the operator has checked a real human-filled
    mTool file against its PDF and confirmed the figures land at the same
    scale as extraction stores them."""
    conn = server._open_audit_conn()
    try:
        cur = conn.execute(
            "UPDATE eval_benchmarks SET scale_verified = 1 WHERE id = ?",
            (benchmark_id,),
        )
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Benchmark not found")
    return {"ok": True, "id": benchmark_id, "scale_verified": True}


@router.post("/api/benchmarks/{benchmark_id}/unarchive")
async def unarchive_benchmark_endpoint(benchmark_id: int):
    from eval import store

    conn = server._open_audit_conn()
    try:
        restored = store.unarchive_benchmark(conn, benchmark_id)
        conn.commit()
    finally:
        conn.close()
    if not restored:
        raise HTTPException(status_code=404, detail="Benchmark not found")
    return {"ok": True, "id": benchmark_id, "archived": False}


@router.post("/api/runs/{run_id}/re-grade")
async def re_grade_run_endpoint(run_id: int):
    """Re-grade a run against its benchmark's CURRENT gold (Step 8): updates
    the stored score + gold fingerprint in place and reports old vs new, so a
    gold correction can be applied to history with one click instead of
    leaving a stale trend point."""
    from db import repository as repo
    from eval.grader import grade_run

    conn = server._open_audit_conn()
    try:
        row = conn.execute(
            "SELECT benchmark_id FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Run not found")
        old = repo.fetch_eval_score_for_run(conn, run_id)
        benchmark_id = (old or {}).get("benchmark_id") or row[0]
        if benchmark_id is None:
            raise HTTPException(
                status_code=422,
                detail="This run has no benchmark attached — nothing to grade against.",
            )
        try:
            card = grade_run(conn, run_id, int(benchmark_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        repo.save_eval_score(conn, run_id, int(benchmark_id), card)
        conn.commit()
        new = repo.fetch_eval_score(conn, run_id, int(benchmark_id))
    finally:
        conn.close()
    return {
        "ok": True,
        "run_id": run_id,
        "benchmark_id": int(benchmark_id),
        "old_score": old.get("score") if old else None,
        "new_score": new.get("score") if new else None,
        "score": new,
    }


@router.get("/api/benchmarks/{benchmark_id}/concepts")
async def benchmark_concepts_endpoint(benchmark_id: int):
    """Gold grid in the same shape as ``/api/runs/{id}/concepts`` so the
    frontend ConceptsPage renders it unchanged (source='benchmark')."""
    from eval import store

    conn = server._open_audit_conn()
    try:
        bench = store.get_benchmark(conn, benchmark_id)
        if bench is None:
            raise HTTPException(status_code=404, detail="Benchmark not found")
        concepts = store.benchmark_concepts(conn, benchmark_id)
    finally:
        conn.close()
    # Keyed under both `run_id`-style fields the grid may read; `benchmark_id`
    # is the canonical one here.
    return {"benchmark_id": benchmark_id, "concepts": concepts}


@router.patch("/api/benchmarks/{benchmark_id}/facts")
async def patch_gold_fact_endpoint(benchmark_id: int, body: GoldFactPatch):
    from eval import store

    conn = server._open_audit_conn()
    try:
        if store.get_benchmark(conn, benchmark_id) is None:
            raise HTTPException(status_code=404, detail="Benchmark not found")
        try:
            fact = store.patch_gold_fact(
                conn, benchmark_id, body.concept_uuid,
                period=body.period, entity_scope=body.entity_scope,
                value=body.value,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, **fact}


@router.get("/api/runs/{run_id}/eval")
async def get_run_eval_endpoint(run_id: int):
    """The run's scorecard for the Eval tab. 404 when the run wasn't graded.
    Carries ``gold_stale`` (Step 7): True when the benchmark's gold changed
    after this score was stamped — the tab offers a one-click re-grade."""
    from db import repository as repo
    from eval.store import gold_fingerprint

    conn = server._open_audit_conn()
    try:
        score = repo.fetch_eval_score_for_run(conn, run_id)
        if score is not None:
            stale = None
            if score.get("gold_fingerprint"):
                try:
                    stale = (
                        gold_fingerprint(conn, int(score["benchmark_id"]))
                        != score["gold_fingerprint"]
                    )
                except Exception:
                    stale = None
            score["gold_stale"] = stale
    finally:
        conn.close()
    if score is None:
        raise HTTPException(status_code=404, detail="Run has no eval score")
    return score


def resolve_slot_labels(conn, rows: list[dict]) -> None:
    """Attach human line-item names to slot rows IN PLACE (Step 11/12).

    A slot key is (concept_uuid, period, entity_scope) — meaningless to a
    reviewer. Resolve each uuid to its sheet + label via concept_nodes; a
    uuid that no longer resolves keeps only the raw key (the UI falls back
    to rendering it). Shared by the consistency panel and the compare
    drill-down."""
    uuids = {r["key"][0] for r in rows if r.get("key")}
    if not uuids:
        return
    placeholders = ",".join("?" for _ in uuids)
    labels = {
        u: (sheet, label)
        for u, sheet, label in conn.execute(
            f"SELECT concept_uuid, render_sheet, canonical_label "
            f"FROM concept_nodes WHERE concept_uuid IN ({placeholders})",
            tuple(uuids),
        ).fetchall()
    }
    for r in rows:
        hit = labels.get((r.get("key") or [None])[0])
        if hit is not None:
            r["sheet"], r["label"] = hit


@router.get("/api/runs/{run_id}/reviewer-lift")
async def reviewer_lift_endpoint(run_id: int):
    """What the reviewer pass contributed to this run's score (Step 12 —
    computed since E5 but never reachable). available:false when the run has
    no benchmark or no pre-reviewer snapshot exists (no reviewer pass ran)."""
    from eval.grader import reviewer_lift

    conn = server._open_audit_conn()
    try:
        row = conn.execute(
            "SELECT benchmark_id FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if row[0] is None:
            return {"available": False, "run_id": run_id}
        lift = reviewer_lift(conn, run_id, int(row[0]))
    finally:
        conn.close()
    if lift is None:
        return {"available": False, "run_id": run_id}
    return {"available": True, "run_id": run_id, **lift}


@router.get("/api/repeat-groups/{group_id}")
async def get_repeat_group_endpoint(group_id: int):
    """A repeat group + its computed consistency result (v30). Feeds the
    consistency panel on a grouped run's page (docs/PLAN-evals-workspace.md).
    Disagreement slots carry resolved sheet/label names; each child run
    carries its own accuracy (Step 11)."""
    from db import repository as repo

    conn = server._open_audit_conn()
    try:
        group = repo.fetch_repeat_group(conn, group_id)
        if group is not None and group.get("consistency"):
            c = group["consistency"]
            resolve_slot_labels(
                conn,
                list(c.get("presence_disagreements") or [])
                + list(c.get("value_disagreements") or []),
            )
    finally:
        conn.close()
    if group is None:
        raise HTTPException(status_code=404, detail="Repeat group not found")
    return group


@router.post("/api/repeat-groups/{group_id}/recompute")
async def recompute_repeat_group_endpoint(group_id: int):
    """Recompute + persist a group's consistency from its finished repeats. Used
    after a repeat finishes, or manually from the panel."""
    from db import repository as repo
    from eval.consistency import finalize_repeat_group

    conn = server._open_audit_conn()
    try:
        if repo.fetch_repeat_group(conn, group_id) is None:
            raise HTTPException(status_code=404, detail="Repeat group not found")
        result = finalize_repeat_group(conn, group_id)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "group_id": group_id, "consistency": result.to_dict()}
