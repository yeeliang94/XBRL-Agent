"""Durable upload-owned preparation, followed by Scout.

Workers use their own event loop, like reviewer jobs. Only plain snapshots and
thread handles cross loops; cancellation goes through task_registry.
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

import server
import task_registry
from model_settings import DEFAULT_MODEL_ID
from utils.atomic_io import replace_with_retry
from utils.paths import validate_session_id

if TYPE_CHECKING:
    from ingest.document_preparation import PreparedDocument

router = APIRouter()
logger = logging.getLogger(__name__)
_ACTIVE = frozenset({"queued", "working", "retrying"})
_lock = threading.RLock()
_workers: dict[str, threading.Thread] = {}

_PAGE_PREPARATION_STAGES = frozenset({
    "checking_pages", "capturing", "verifying", "joining", "succeeded",
})


def _phase_for_stage(stage: object) -> str | None:
    """Collapse concurrent implementation stages into a monotonic UI phase."""
    if stage in _PAGE_PREPARATION_STAGES:
        return "preparing_pages"
    if stage == "scouting":
        return "building_map"
    if stage == "reconciling":
        return "reconciling_map"
    if stage == "ready":
        return "awaiting_confirmation"
    if stage == "pending":
        return "pending"
    return None


def _with_workflow_state(state: dict) -> dict:
    """Backfill the public workflow interface for older durable snapshots."""
    result = dict(state)
    result.setdefault("phase", _phase_for_stage(result.get("stage")) or "pending")
    if "action_required" not in result:
        if result.get("status") in {"failed", "cancelled"}:
            result["action_required"] = "retry"
        elif result.get("status") == "succeeded":
            result["action_required"] = "confirm_setup"
        else:
            result["action_required"] = "none"
    return result


def active_session_ids() -> set[str]:
    with _lock:
        return {Path(key).name for key, worker in _workers.items() if worker.is_alive()}


def _key(directory: Path) -> str:
    return str(directory.resolve())


def _read(directory: Path) -> dict:
    try:
        return json.loads((directory / "preparation_status.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "status": "not_started", "stage": "pending", "phase": "pending",
            "action_required": "none",
            "message": "Document preparation has not started.",
        }


def _write(directory: Path, snapshot: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".preparation-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(name, directory / "preparation_status.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)


def snapshot(directory: Path) -> dict:
    with _lock:
        result = _read(directory)
        worker = _workers.get(_key(directory))
        if result.get("status") in _ACTIVE and not (worker and worker.is_alive()):
            result.update(status="failed", action_required="retry",
                          message="Document preparation was interrupted. Retry to resume.",
                          error="preparation_interrupted", updated_at=time.time())
            try:
                _write(directory, result)
            except OSError:
                # Keep the UI and audit terminal even if an external process
                # holds the status file beyond the bounded retry window. A
                # later poll will attempt to persist the same terminal state.
                logger.exception("Could not persist interrupted preparation status")
            _finish_interrupted_audit(directory, result)
        return _with_workflow_state(result)


def _finish_interrupted_audit(directory: Path, state: dict) -> None:
    """Draft parents are not covered by extraction's stale-run reaper."""
    if not state.get("run_id"):
        return
    from db import repository as repo
    try:
        conn = _connection(server.AUDIT_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT a.id FROM run_agents a JOIN runs r ON r.id=a.run_id "
                "WHERE r.id=? AND r.session_id=? AND a.status='running' "
                "AND a.statement_type IN ('SOURCE_PREPARATION','SCOUT')",
                (state["run_id"], directory.name),
            ).fetchall()
            for row in rows:
                repo.finish_run_agent(conn, row["id"], "failed", error_type="preparation_interrupted")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("Could not reconcile interrupted preparation audit", exc_info=True)


def _update(directory: Path, attempt: str, **changes) -> dict:
    with _lock:
        current = _read(directory)
        if current.get("attempt_id") != attempt:
            raise RuntimeError("Preparation attempt was superseded")
        if current.get("cancel_requested") and changes.get("status") == "succeeded":
            raise asyncio.CancelledError(task_registry.USER_ABORT_REASON)
        current.update(changes, updated_at=time.time())
        _write(directory, current)
        return current


def _connection(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _configuration() -> tuple[str, str, str]:
    model = os.environ.get("TEST_MODEL", DEFAULT_MODEL_ID)
    scout = server._configured_default_models().get("scout") or os.environ.get("SCOUT_MODEL", "").strip() or model
    from model_settings import configured_role_thinking_level
    payload = {"model": model, "scout": scout,
               "proxy": os.environ.get("LLM_PROXY_URL", ""),
               "thinking": configured_role_thinking_level("scout", default="low")}
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return model, scout, key


def start_preparation(directory: Path, run_id: int | None, *, retry: bool = False) -> dict:
    """Persist intent before dispatch; a repeated start joins the same attempt."""
    with _lock:
        current = snapshot(directory)
        worker = _workers.get(_key(directory))
        if worker is not None and worker.is_alive():
            return current
        if current.get("status") in _ACTIVE:
            return current
        if current.get("status") == "succeeded" and not retry:
            return current
        if run_id is None:
            raise RuntimeError("Document preparation requires a persisted upload")
        attempt = uuid.uuid4().hex
        current = {
            "attempt_id": attempt, "run_id": run_id, "status": "queued",
            "stage": "checking_pages", "message": "Checking the uploaded document",
            "phase": "preparing_pages", "action_required": "none",
            "completed": 0, "total": 0, "captured": 0, "verified": 0, "checked": 0,
            "prepared": False, "scout_status": "queued",
            "started_at": time.time(), "updated_at": time.time(),
        }
        _write(directory, current)
        # Capture paths now: background jobs must not follow mutable server/test globals.
        worker = threading.Thread(
            target=_worker, args=(directory, Path(server.AUDIT_DB_PATH), run_id, attempt),
            name=f"prepare-{directory.name}", daemon=True,
        )
        _workers[_key(directory)] = worker
        try:
            worker.start()
        except Exception:
            _workers.pop(_key(directory), None)
            _update(directory, attempt, status="failed", action_required="retry", error="dispatch_failed",
                    message="Document preparation could not start. Retry.")
            raise
        return current


def _worker(directory: Path, db_path: Path, run_id: int, attempt: str) -> None:
    try:
        asyncio.run(_prepare(directory, db_path, run_id, attempt))
    except Exception:
        logger.exception("Preparation worker failed for run %s", run_id)
        try:
            _update(directory, attempt, status="failed", action_required="retry", error="worker_failed",
                    message="Document preparation could not finish. Retry.")
        except Exception:
            # The original failure is already logged. Do not let a second
            # status-file sharing violation escape the worker unobserved.
            logger.exception("Could not persist worker failure status for run %s", run_id)
    finally:
        with _lock:
            if _workers.get(_key(directory)) is threading.current_thread():
                _workers.pop(_key(directory), None)


def _usage_totals(directory: Path) -> dict[str, dict[str, int]]:
    ledgers: dict[str, list] = {}
    for path in [*directory.glob("preparation-checkpoint-*.json"), directory / "preparation.json"]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            revision = str(data.get("revision") or path.stem.removeprefix("preparation-checkpoint-"))[:16]
            calls = data.get("calls", [])
            if len(calls) >= len(ledgers.get(revision, [])):
                ledgers[revision] = calls
        except (OSError, ValueError, TypeError):
            continue
    totals = {}
    for revision, calls in ledgers.items():
        values: dict[str, int] = {}
        for call in calls:
            for key, value in call.get("usage", {}).items():
                values[key] = values.get(key, 0) + int(value or 0)
        totals[revision] = values
    return totals


def _start_scout_audit(conn, run_id: int, model_name: str) -> tuple[int, dict]:
    """Keep one visible Scout row without losing earlier paid attempt totals."""
    from db import repository as repo
    row = conn.execute(
        "SELECT * FROM run_agents WHERE run_id=? AND statement_type='SCOUT' ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    previous = dict(row) if row is not None else {}
    row_id = repo.reset_or_create_scout_agent_row(conn, run_id, model=model_name)
    # reset_or_create clears totals for the legacy preview contract. Preparation
    # retries accumulate paid attempts, including when the new map hits cache.
    if previous:
        conn.execute("UPDATE run_agents SET total_tokens=?, total_cost=? WHERE id=?",
                     (previous.get("total_tokens", 0), previous.get("total_cost", 0), row_id))
    conn.commit()
    return row_id, previous


def _finish_scout_audit(conn, row_id: int, status: str, usage: dict,
                        model_name: str, previous: dict) -> None:
    from db import repository as repo
    from pricing import estimate_cost
    def total(field: str, metric: str | None = None) -> int:
        return int(previous.get(field) or 0) + int(usage.get(metric or field) or 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    thinking = int(usage.get("thinking_tokens") or 0)
    repo.finish_run_agent(
        conn, row_id, status, prompt_tokens=total("prompt_tokens"),
        completion_tokens=total("completion_tokens"),
        reasoning_tokens=total("reasoning_tokens", "thinking_tokens"),
        total_tokens=total("total_tokens"),
        total_cost=float(previous.get("total_cost") or 0) + estimate_cost(prompt, completion, thinking, model_name),
        turn_count=total("turn_count"), tool_call_count=total("tool_call_count"),
        cache_read_tokens=total("cache_read_tokens"), cache_write_tokens=total("cache_write_tokens"),
    )


def inventory_requires_remap(original: list[dict], current: list[dict]) -> bool:
    """Titles and subsection descriptions do not change top-level ownership."""
    def boundaries(entries):
        return sorted((entry["note_num"], tuple(entry.get("page_range") or []))
                      for entry in entries)
    return boundaries(original) != boundaries(current)


async def remap_prepared_inventory(prepared: PreparedDocument, infopack: dict, *, run_id: int,
                                   session_id: str, on_progress=None) -> PreparedDocument:
    """Apply user inventory edits to an isolated run-owned source ledger."""
    from dataclasses import replace
    from ingest.document_preparation import _atomic_text, reconcile_prepared_inventory, PreparationError
    from scout.prepared_map import build_prepared_document_map

    conn = None
    row_id = None
    previous: dict = {}
    usage: dict = {}
    status = "failed"
    model_name = os.environ.get("TEST_MODEL", DEFAULT_MODEL_ID)
    task_registry.register(session_id, "scout", asyncio.current_task())
    try:
        # Upload publishes readiness before its audit finally block completes.
        # Wait before reusing the same Scout row for a run-owned override.
        while session_id in active_session_ids():
            await asyncio.sleep(0.05)
        conn = _connection(server.AUDIT_DB_PATH)
        server._reload_runtime_settings()
        _, model_name, _ = _configuration()
        row_id, previous = _start_scout_audit(conn, run_id, model_name)
        api_key = server._resolve_api_key()
        if not api_key:
            raise PreparationError("The AI service needs an API key. Ask an administrator to configure Settings, then retry.")
        model = server._create_proxy_model(model_name, os.environ.get("LLM_PROXY_URL", ""), api_key)
        path = prepared.metadata_path.parent / f"preparation-run-{run_id}.json"
        _atomic_text(path, prepared.metadata_path.read_text(encoding="utf-8"))
        isolated = replace(prepared, metadata_path=path)
        _, assignments = await build_prepared_document_map(
            isolated, model, inventory=infopack, on_progress=on_progress, usage_out=usage,
        )
        result = await reconcile_prepared_inventory(
            isolated, assignments=assignments, on_progress=on_progress,
        )
        status = "succeeded"
        return result
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    finally:
        try:
            if conn is not None and row_id is not None:
                _finish_scout_audit(conn, row_id, status, usage, model_name, previous)
                conn.commit()
        finally:
            if conn is not None:
                conn.close()
            task_registry.unregister(session_id, "scout")


async def _prepare(directory: Path, db_path: Path, run_id: int, attempt: str) -> None:
    from db import repository as repo
    from ingest.document_preparation import prepare_document
    from scout.prepared_map import build_prepared_document_map

    session_id = directory.name
    task = asyncio.current_task()
    task_registry.register(session_id, "source-preparation", task)
    agent_id = None
    scout_id = None
    prior_scout_usage: dict = {}
    status = "failed"
    scout_status = "failed"
    usage: dict = {}
    usage_before = _usage_totals(directory)
    conn = None
    model_name = os.environ.get("TEST_MODEL", DEFAULT_MODEL_ID)
    try:
        conn = _connection(db_path)
        # Audit allocation precedes model construction or validation.
        model_name = os.environ.get("TEST_MODEL", DEFAULT_MODEL_ID)
        agent_id = repo.create_run_agent(conn, run_id, statement_type="SOURCE_PREPARATION",
                                         variant=None, model=model_name)
        conn.commit()
        if _read(directory).get("cancel_requested"):
            raise asyncio.CancelledError(task_registry.USER_ABORT_REASON)
        _update(directory, attempt, status="working")
        server._reload_runtime_settings()
        model_name, scout_name, configuration_key = _configuration()
        _update(directory, attempt, model_name=model_name, configuration_key=configuration_key)
        conn.execute("UPDATE run_agents SET model=? WHERE id=?", (model_name, agent_id))
        conn.commit()
        api_key = server._resolve_api_key()
        if not api_key:
            from ingest.document_preparation import PreparationError
            raise PreparationError("The AI service needs an API key. Ask an administrator to configure Settings, then retry.")
        model = server._create_proxy_model(model_name, os.environ.get("LLM_PROXY_URL", ""), api_key)

        def progress(data: dict) -> None:
            allowed = {k: v for k, v in data.items() if k in {
                "stage", "message", "completed", "total", "captured", "verified", "checked",
            }}
            phase = _phase_for_stage(data.get("stage"))
            if phase is not None:
                allowed.update(phase=phase, action_required="none")
            state = _update(directory, attempt, **allowed)
            repo.log_run_event(conn, run_id, "preparation_progress", payload=state,
                               phase=str(state.get("stage", "preparing")))
            conn.commit()

        prepared = await prepare_document(directory / "uploaded.pdf", model,
                                           model_name=model_name, on_progress=progress,
                                           configuration_key=configuration_key)
        status = "succeeded"
        _update(directory, attempt, prepared=True, stage="scouting", phase="building_map",
                action_required="none",
                scout_status="working", message="Building document map and notes inventory")
        scout_id, prior_scout_usage = _start_scout_audit(conn, run_id, scout_name)
        scout_model = server._create_proxy_model(scout_name, os.environ.get("LLM_PROXY_URL", ""), api_key)

        pack, assignments = await build_prepared_document_map(
            prepared, scout_model, on_progress=progress, usage_out=usage,
        )
        if getattr(pack, "degraded", False):
            from ingest.document_preparation import PreparationError
            raise PreparationError("The notes inventory could not be completed. Retry document preparation.")
        from ingest.document_preparation import reconcile_prepared_inventory
        prepared = await reconcile_prepared_inventory(
            prepared, assignments=assignments, on_progress=progress,
        )
        from notes.source_manifest import build_prepared_manifest
        # Reconciliation must succeed before the UI or extraction sees readiness.
        build_prepared_manifest(prepared.metadata_path,
                                scout_note_nums=[e.note_num for e in pack.notes_inventory or []])
        infopack = json.loads(pack.to_json())
        _update(directory, attempt, status="succeeded", stage="ready",
                phase="awaiting_confirmation", action_required="confirm_setup",
                scout_status="succeeded",
                message="Document prepared and notes inventory ready", infopack=infopack,
                source_revision=prepared.revision)
        status = scout_status = "succeeded"
    except asyncio.CancelledError:
        if status != "succeeded":
            status = "cancelled"
        scout_status = "cancelled"
        _update(directory, attempt, status="cancelled", action_required="retry",
                scout_status="cancelled",
                message="Document preparation cancelled")
    except Exception as exc:
        logger.warning("Preparation failed for run %s (%s)", run_id, type(exc).__name__)
        # Only approved user-facing errors are exposed, never raw provider payloads.
        from ingest.document_preparation import PreparationError
        message = str(exc) if isinstance(exc, PreparationError) else "Document preparation could not finish. Retry."
        _update(directory, attempt, status="failed", action_required="retry",
                scout_status="failed",
                error=type(exc).__name__, message=message)
    finally:
        try:
            source_usage: dict[str, int] = {}
            for revision, totals in _usage_totals(directory).items():
                for key, value in totals.items():
                    delta = max(0, value - usage_before.get(revision, {}).get(key, 0))
                    source_usage[key] = source_usage.get(key, 0) + delta
            from pricing import estimate_cost
            for row_id, final, metrics, name in (
                (agent_id, status, source_usage, model_name),
                (scout_id, scout_status, usage, locals().get("scout_name", model_name)),
            ):
                if row_id is not None and conn is not None:
                    if row_id == scout_id:
                        _finish_scout_audit(conn, row_id, final, metrics, name, prior_scout_usage)
                        continue
                    prompt = int(metrics.get("prompt_tokens", 0))
                    completion = int(metrics.get("completion_tokens", 0))
                    thinking = int(metrics.get("thinking_tokens", 0))
                    repo.finish_run_agent(
                        conn, row_id, final, prompt_tokens=prompt,
                        completion_tokens=completion, reasoning_tokens=thinking,
                        total_tokens=int(metrics.get("total_tokens", 0)),
                        total_cost=estimate_cost(prompt, completion, thinking, name),
                        turn_count=int(metrics.get("turn_count", 0)),
                        tool_call_count=int(metrics.get("tool_call_count", 0)),
                    )
            if conn is not None:
                conn.commit()
        finally:
            if conn is not None:
                conn.close()
            task_registry.unregister(session_id, "source-preparation")


async def ensure_prepared(directory: Path, run_id: int | None) -> dict:
    """Join and validate the current completed revision before extraction."""
    from ingest.document_preparation import read_prepared_document
    current = snapshot(directory)
    if current.get("status") == "not_started":
        current = start_preparation(directory, run_id)
    for validation_attempt in range(2):
        while current.get("status") in _ACTIVE:
            await asyncio.sleep(0.2)
            current = snapshot(directory)
        if current.get("status") == "cancelled":
            raise asyncio.CancelledError(task_registry.USER_ABORT_REASON)
        if current.get("status") != "succeeded":
            raise RuntimeError(current.get("message") or "Document preparation is incomplete")
        # Validate AFTER waiting too: settings/input may change while preparing.
        server._reload_runtime_settings()
        model_name, _, key = _configuration()
        prepared = await asyncio.to_thread(
            read_prepared_document, directory / "uploaded.pdf",
            model_name=model_name, configuration_key=key,
        )
        if prepared is not None:
            return current
        if validation_attempt == 0:
            while directory.name in active_session_ids():
                await asyncio.sleep(0.05)
            current = start_preparation(directory, run_id, retry=True)
    raise RuntimeError("Document or model settings changed during preparation. Retry preparation.")


def _directory(session_id: str) -> Path:
    try:
        validate_session_id(session_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid session id.") from exc
    directory = server.OUTPUT_DIR / session_id
    if not (directory / "uploaded.pdf").is_file():
        raise HTTPException(404, "Document not found. Upload first.")
    return directory


@router.get("/api/preparation/{session_id}")
async def preparation_status(session_id: str):
    return snapshot(_directory(session_id))


@router.post("/api/preparation/{session_id}")
async def preparation_retry(session_id: str):
    directory = _directory(session_id)
    if session_id in server.active_runs:
        raise HTTPException(409, "Extraction is using this document. Stop it before retrying preparation.")
    conn = _connection(server.AUDIT_DB_PATH)
    try:
        row = conn.execute("SELECT id FROM runs WHERE session_id=? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(409, "This upload has no saved run. Upload it again.")
    return start_preparation(directory, int(row[0]), retry=True)


@router.post("/api/preparation/{session_id}/cancel")
async def preparation_cancel(session_id: str):
    directory = _directory(session_id)
    current = snapshot(directory)
    if current.get("status") in _ACTIVE:
        _update(directory, current["attempt_id"], cancel_requested=True)
        task_registry.cancel_agent(session_id, "source-preparation")
    return snapshot(directory)
