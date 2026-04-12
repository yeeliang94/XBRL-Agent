"""XBRL Agent — FastAPI web server with SSE streaming.

Provides a web UI for uploading PDFs, running extraction agents,
and streaming progress events in real-time via Server-Sent Events.

Run mode: POST /api/run/{session_id} with RunConfigRequest body.
Orchestrates N sub-agents via coordinator, merges workbooks, runs
cross-checks, and persists results to SQLite audit DB.
"""
# Force UTF-8 on Windows (avoids charmap codec errors with Unicode text from PDFs)
import sys
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import asyncio
import json
import logging
import os
import time
import traceback
import uuid
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, Set, Any

from dotenv import load_dotenv, set_key
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Suppress LiteLLM SSL warnings (enterprise firewall blocks GitHub pricing fetch)
try:
    import litellm
    litellm.suppress_debug_info = True
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
ENV_FILE = BASE_DIR / ".env"
CONFIG_DIR = BASE_DIR / "config"
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
# Shared SQLite audit store (one file per installation, grows over time).
AUDIT_DB_PATH = OUTPUT_DIR / "xbrl_agent.db"

# Phase mapping: tool name → EventPhase
PHASE_MAP = {
    "read_template": "reading_template",
    "view_pdf_pages": "viewing_pdf",
    "fill_workbook": "filling_workbook",
    "verify_totals": "verifying",
    "save_result": "complete",
}

# Lazy imports for multi-agent pipeline — done at call sites to keep startup fast.
# scout.runner.run_scout, coordinator.run_extraction, workbook_merger.merge,
# cross_checks.framework.run_all, etc.


# ---------------------------------------------------------------------------
# Model creation
# ---------------------------------------------------------------------------

def _resolve_api_key() -> str:
    """Return the best available API key: GOOGLE_API_KEY (proxy) or GEMINI_API_KEY (direct)."""
    return os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")


def _detect_provider(model_name: str) -> str:
    """Infer the provider from a model name string.

    Returns 'openai', 'anthropic', or 'google'.
    """
    lower = model_name.lower()
    if lower.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return "openai"
    if lower.startswith("claude-"):
        return "anthropic"
    return "google"


def _model_id(model_obj) -> str:
    """Return the human-readable model id for a PydanticAI Model instance.

    PydanticAI's Model classes (`OpenAIChatModel`, `GoogleModel`,
    `AnthropicModel`) all have a useless `__str__` that returns just the
    class name with empty parentheses (e.g. `'OpenAIChatModel()'`). The
    actual configured id (`'gpt-5.4'`, `'gemini-3-flash-preview'`, etc.)
    lives on the `model_name` attribute. Always prefer that attribute when
    persisting model identity to the audit DB so the History UI and the
    `?model=` filter both see the real id, not a class repr.

    Falls back to `str()` only if `model_name` is missing or empty —
    keeping the helper safe for the test stubs that pass plain strings.
    """
    name = getattr(model_obj, "model_name", None)
    if isinstance(name, str) and name:
        return name
    return str(model_obj)


def _create_proxy_model(model_name: str, proxy_url: str, api_key: str):
    """Create a PydanticAI model with multi-provider support.

    Routing logic:
    1. If ``proxy_url`` is set → enterprise LiteLLM proxy (Windows). All
       models go through the OpenAI-compatible proxy endpoint.
    2. If ``proxy_url`` is empty (Mac / direct API):
       - OpenAI models (gpt-*, o1-*, o3-*, o4-*) → OpenAI API via OPENAI_API_KEY
       - Anthropic models (claude-*) → Anthropic API via ANTHROPIC_API_KEY
       - Everything else → Google Gemini API via GEMINI_API_KEY / GOOGLE_API_KEY
    """
    # Enterprise proxy path — everything goes through one OpenAI-compatible endpoint
    if proxy_url:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        provider = OpenAIProvider(base_url=proxy_url, api_key=api_key)
        return OpenAIChatModel(model_name, provider=provider)

    # Direct API paths — route by provider
    detected = _detect_provider(model_name)

    if detected == "openai":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            raise ValueError(
                f"Model '{model_name}' requires OPENAI_API_KEY in .env but it is not set."
            )
        provider = OpenAIProvider(api_key=openai_key)
        return OpenAIChatModel(model_name, provider=provider)

    if detected == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            raise ValueError(
                f"Model '{model_name}' requires ANTHROPIC_API_KEY in .env but it is not set."
            )
        provider = AnthropicProvider(api_key=anthropic_key)
        return AnthropicModel(model_name, provider=provider)

    # Google Gemini direct path
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    # Strip any "vertex_ai." / "google-gla:" prefix from the model name,
    # since GoogleModel expects bare names like "gemini-3-flash-preview".
    bare = model_name.split(":", 1)[-1]
    if bare.startswith("vertex_ai."):
        bare = bare[len("vertex_ai."):]
    provider = GoogleProvider(api_key=api_key)
    return GoogleModel(bare, provider=provider)



# ---------------------------------------------------------------------------
# Conversation trace saving
# ---------------------------------------------------------------------------

def _save_trace(result, output_dir: str):
    """Save conversation trace (minus binary image data) for debugging."""
    import dataclasses

    trace_path = Path(output_dir) / "conversation_trace.json"
    messages = []
    for msg in result.all_messages():
        if hasattr(msg, "model_dump"):
            msg_dict = msg.model_dump(mode="json")
        elif dataclasses.is_dataclass(msg):
            msg_dict = dataclasses.asdict(msg)
        else:
            msg_dict = {"raw": str(msg)}
        _strip_binary(msg_dict)
        messages.append(msg_dict)

    usage_data = None
    if result.usage:
        usage_data = result.usage.model_dump(mode="json") if hasattr(result.usage, "model_dump") else str(result.usage)

    trace = {
        "messages": messages,
        "usage": usage_data,
        "output": result.output if isinstance(result.output, str) else str(result.output),
    }
    trace_path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")


def _strip_binary(obj):
    """Recursively strip binary/image data from message dicts to keep traces readable."""
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key == "data" and isinstance(obj.get("media_type"), str) and "image" in obj["media_type"]:
                obj[key] = f"[{obj['media_type']} image data stripped]"
            else:
                _strip_binary(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _strip_binary(item)


# ---------------------------------------------------------------------------
# Async streaming agent runner — replaces the old thread + EventQueue pattern.
# Uses PydanticAI's agent.iter() to get granular streaming events.
# ---------------------------------------------------------------------------

## iter_agent_events was removed in Phase 11.3 — the legacy single-agent
## streaming path has been replaced by the multi-agent coordinator.
## Use POST /api/run/{session_id} with RunConfigRequest instead.


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(title="XBRL Agent", version="0.3.0")

# Track active extraction runs by session_id
active_runs: set[str] = set()


# ---------------------------------------------------------------------------
# Request models for multi-agent endpoints
# ---------------------------------------------------------------------------

class RunConfigRequest(BaseModel):
    """Request body for POST /api/run/{session_id}."""
    statements: List[str]  # e.g. ["SOFP", "SOPL"]
    variants: Dict[str, str] = {}  # e.g. {"SOFP": "CuNonCu"}
    models: Dict[str, str] = {}  # per-statement model overrides
    infopack: Optional[Dict] = None  # serialised Infopack JSON (nullable)
    use_scout: bool = False  # informational — actual infopack presence controls behaviour


# --- Settings helpers ---

# Statement type keys used for per-agent model defaults
_AGENT_ROLES = ("scout", "SOFP", "SOPL", "SOCI", "SOCF", "SOCIE")


def _load_available_models() -> list[dict]:
    """Read the pinned model list from config/models.json.

    Re-reads on every call so edits are picked up without a redeploy.
    Returns an empty list if the file is missing or malformed.
    """
    models_file = CONFIG_DIR / "models.json"
    if not models_file.exists():
        return []
    try:
        return json.loads(models_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load config/models.json: %s", exc)
        return []


def _load_extended_settings() -> dict:
    """Read extended settings (default_models, scout toggle, tolerance) from .env.

    Extended keys are stored as dotenv entries with an XBRL_ prefix:
      XBRL_DEFAULT_MODELS = JSON object
      XBRL_SCOUT_ENABLED_DEFAULT = true/false
      XBRL_TOLERANCE_RM = float
    """
    raw_models = os.environ.get("XBRL_DEFAULT_MODELS", "")
    try:
        default_models = json.loads(raw_models) if raw_models else {}
    except json.JSONDecodeError:
        default_models = {}

    # Ensure every agent role has a key (fall back to the global model)
    global_model = os.environ.get("TEST_MODEL", "vertex_ai.gemini-3-flash-preview")
    for role in _AGENT_ROLES:
        default_models.setdefault(role, global_model)

    scout_enabled = os.environ.get("XBRL_SCOUT_ENABLED_DEFAULT", "true").lower() == "true"

    try:
        tolerance = float(os.environ.get("XBRL_TOLERANCE_RM", "1.0"))
    except ValueError:
        tolerance = 1.0

    return {
        "default_models": default_models,
        "scout_enabled_default": scout_enabled,
        "tolerance_rm": tolerance,
    }


# --- Settings endpoints ---

@app.get("/api/settings")
async def get_settings():
    load_dotenv(ENV_FILE, override=True)
    api_key = _resolve_api_key()
    masked = api_key[:4] + "..." + api_key[-2:] if len(api_key) > 8 else ""

    extended = _load_extended_settings()
    return {
        # Backward-compatible fields
        "model": os.environ.get("TEST_MODEL", "vertex_ai.gemini-3-flash-preview"),
        "proxy_url": os.environ.get("LLM_PROXY_URL", ""),
        "api_key_set": bool(api_key),
        "api_key_preview": masked,
        # Extended fields (Phase 8)
        "available_models": _load_available_models(),
        **extended,
    }


@app.post("/api/settings")
async def update_settings(body: dict):
    """Update .env file with new settings."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    # Legacy fields
    if "model" in body:
        set_key(str(ENV_FILE), "TEST_MODEL", body["model"])
    if "api_key" in body and body["api_key"]:
        set_key(str(ENV_FILE), "GOOGLE_API_KEY", body["api_key"])
    if "proxy_url" in body and body["proxy_url"]:
        set_key(str(ENV_FILE), "LLM_PROXY_URL", body["proxy_url"])

    # Extended fields (Phase 8)
    if "default_models" in body:
        # Merge incoming overrides with existing defaults
        load_dotenv(ENV_FILE, override=True)
        existing = _load_extended_settings()["default_models"]
        existing.update(body["default_models"])
        set_key(str(ENV_FILE), "XBRL_DEFAULT_MODELS", json.dumps(existing))
    if "scout_enabled_default" in body:
        set_key(str(ENV_FILE), "XBRL_SCOUT_ENABLED_DEFAULT",
                "true" if body["scout_enabled_default"] else "false")
    if "tolerance_rm" in body:
        set_key(str(ENV_FILE), "XBRL_TOLERANCE_RM", str(body["tolerance_rm"]))

    load_dotenv(ENV_FILE, override=True)
    return {"status": "ok"}


# --- Upload endpoint ---

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    # Validate file type
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    # Read and check size
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large. Max size is {MAX_UPLOAD_SIZE // (1024*1024)}MB.")

    # Create session directory and save the PDF
    session_id = str(uuid.uuid4())
    session_dir = OUTPUT_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "uploaded.pdf").write_bytes(content)

    # Persist the ORIGINAL filename as a sidecar so History can show a
    # meaningful name later. The file on disk is always "uploaded.pdf" to
    # keep downstream paths simple; the sidecar is the single source of
    # truth for the user-facing name.
    # UTF-8 encoding is mandatory — Windows defaults to charmap and will
    # crash on non-ASCII filenames (see CLAUDE.md issue #1).
    (session_dir / "original_filename.txt").write_text(
        file.filename, encoding="utf-8"
    )

    return {"session_id": session_id, "filename": file.filename}


# --- Scout endpoint (Phase 7.1) ---

@app.post("/api/scout/{session_id}")
async def scout_pdf(session_id: str):
    """Run the scout agent on an uploaded PDF and stream progress via SSE.

    Returns an SSE stream with status events during processing, then a
    final 'scout_complete' event containing the full infopack JSON.
    """
    session_dir = OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found. Upload first.")

    load_dotenv(ENV_FILE, override=True)
    api_key = _resolve_api_key()
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    global_model = os.environ.get("TEST_MODEL", "vertex_ai.gemini-3-flash-preview")

    if not api_key:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY (Mac) or GOOGLE_API_KEY (Windows proxy) must be set. Check Settings.")

    # Resolve scout model from per-agent settings (Phase 8), falling back to
    # the global model if no scout-specific override has been configured.
    extended = _load_extended_settings()
    scout_model_name = extended["default_models"].get("scout", global_model)

    # Build a provider-backed model so the scout uses the same proxy/direct
    # wiring as extraction agents (critical for enterprise proxy on Windows).
    scout_model = _create_proxy_model(scout_model_name, proxy_url, api_key)

    async def scout_stream():
        import asyncio
        import task_registry

        # Queue for structured events from the streaming scout agent
        event_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

        async def on_event(event_type: str, data: dict) -> None:
            await event_queue.put((event_type, data))

        scout_task: Optional[asyncio.Task] = None
        try:
            yield f"event: status\ndata: {json.dumps({'phase': 'scouting', 'message': 'Starting scout...'})}\n\n"

            from scout.runner import run_scout_streaming
            scout_task = asyncio.create_task(run_scout_streaming(
                pdf_path=pdf_path,
                model=scout_model,
                on_event=on_event,
            ))

            # Register so abort endpoints can cancel it
            task_registry.register(session_id, "scout", scout_task)

            # Forward structured events as SSE while scout runs
            while not scout_task.done():
                try:
                    event_type, data = await asyncio.wait_for(event_queue.get(), timeout=0.3)
                    yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    continue

            # Drain any remaining events
            while not event_queue.empty():
                event_type, data = event_queue.get_nowait()
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

            infopack = scout_task.result()

            # to_json() returns a JSON string; parse it to embed as a nested dict
            infopack_dict = json.loads(infopack.to_json())
            yield f"event: scout_complete\ndata: {json.dumps({'success': True, 'infopack': infopack_dict})}\n\n"

        except asyncio.CancelledError:
            logger.info("Scout cancelled by user", extra={"session_id": session_id})
            yield f"event: scout_cancelled\ndata: {json.dumps({'message': 'Scout cancelled by user'})}\n\n"

        except Exception as e:
            logger.exception("Scout failed", extra={"session_id": session_id})
            yield f"event: error\ndata: {json.dumps({'message': str(e), 'traceback': traceback.format_exc()})}\n\n"

        finally:
            # Cancel the scout task if still running (e.g. client disconnected)
            if scout_task is not None and not scout_task.done():
                scout_task.cancel()
                try:
                    await scout_task
                except (asyncio.CancelledError, Exception):
                    pass
            # Clean up the task registry entry to avoid stale references
            task_registry.unregister(session_id, "scout")

    return StreamingResponse(
        scout_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# --- Multi-agent SSE run endpoint (Phase 7.2 + 7.3 + 7.4) ---

def _safe_mark_finished(
    db_conn: "Optional[Any]",
    run_id: Optional[int],
    status: str,
) -> bool:
    """Best-effort call to repo.mark_run_finished used from except/finally.

    Returns True on success. Swallows all exceptions so the calling handler
    (which is already dealing with one failure) never gets a second one
    from the audit write. The History page will simply not see this run
    if the DB is unhappy — that's acceptable, since the extraction itself
    has already failed or been cancelled.
    """
    if db_conn is None or run_id is None:
        return False
    try:
        from db import repository as repo
        repo.mark_run_finished(db_conn, run_id, status)
        db_conn.commit()
        return True
    except Exception:
        logger.warning(
            "Failed to mark run %s as %s in audit DB",
            run_id, status, exc_info=True,
        )
        return False


async def run_multi_agent_stream(
    session_id: str,
    session_dir: Path,
    run_config: RunConfigRequest,
    api_key: str,
    proxy_url: str,
    model_name: str,
) -> AsyncIterator[dict]:
    """Orchestrates multi-agent extraction with SSE event multiplexing.

    Runs the coordinator with per-agent event tagging, then merges workbooks,
    runs cross-checks, and persists everything to the audit DB.

    Lifecycle contract (Phase 1.6 refactor):
      1. The `runs` row is created BEFORE the coordinator launches, so
         History captures the run even if the coordinator explodes
         instantly.
      2. The orchestration body is wrapped in try/except/finally. Any path
         out of the function — success, exception, CancelledError, client
         disconnect — leaves the row in a terminal status (never `running`).
      3. `mark_run_merged` is called right after a successful merge, BEFORE
         the final status update, so the download endpoint has a durable
         pointer to filled.xlsx even if later persistence work crashes.
    """
    from coordinator import RunConfig, run_extraction as coordinator_run
    from statement_types import StatementType, get_variant, variants_for
    from workbook_merger import merge as merge_workbooks
    from cross_checks.framework import run_all as run_cross_checks, DEFAULT_TOLERANCE_RM
    from cross_checks.sofp_balance import SOFPBalanceCheck
    from cross_checks.sopl_to_socie_profit import SOPLToSOCIEProfitCheck
    from cross_checks.soci_to_socie_tci import SOCIToSOCIETCICheck
    from cross_checks.socie_to_sofp_equity import SOCIEToSOFPEquityCheck
    from cross_checks.socf_to_sofp_cash import SOCFToSOFPCashCheck
    from db.schema import init_db
    from db import repository as repo
    import sqlite3

    # --- Pre-validation bookkeeping that cannot fail ---
    # These are the only values create_run needs. Compute them up-front so
    # even a totally malformed request still leaves a History row behind.
    output_dir = str(session_dir)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    # Read the original filename from the sidecar written at upload time.
    # The on-disk file is always "uploaded.pdf" so that downstream tools
    # can find it by a stable name; the sidecar records the user-visible
    # name so History search stays meaningful. Fall back to "uploaded.pdf"
    # if the sidecar is missing (legacy sessions from before this change).
    sidecar = session_dir / "original_filename.txt"
    if sidecar.exists():
        try:
            pdf_filename = sidecar.read_text(encoding="utf-8").strip() or "uploaded.pdf"
        except OSError:
            pdf_filename = "uploaded.pdf"
    else:
        pdf_filename = "uploaded.pdf"
    merged_path = str(session_dir / "filled.xlsx")

    # --- Open the audit connection and create the runs row BEFORE any
    # validation. Peer-review fix: if we parsed statements / infopack /
    # model before this point, early failures (invalid enum, bad infopack,
    # proxy unreachable) would never appear in History. ---
    init_db(AUDIT_DB_PATH)

    db_conn: Optional[sqlite3.Connection] = None
    run_id: Optional[int] = None
    # Tracks whether we've already written a terminal status to the runs
    # row. Prevents the finally block from clobbering an earlier-set state.
    terminal_status: Optional[str] = None
    try:
        db_conn = sqlite3.connect(str(AUDIT_DB_PATH))
        db_conn.execute("PRAGMA foreign_keys = ON")
        db_conn.execute("PRAGMA journal_mode = WAL")
        db_conn.execute("PRAGMA busy_timeout = 5000")
        db_conn.row_factory = sqlite3.Row
        run_id = repo.create_run(
            db_conn,
            pdf_filename=pdf_filename,
            session_id=session_id,
            output_dir=output_dir,
            config=run_config.model_dump(),
            scout_enabled=run_config.use_scout,
        )
        db_conn.commit()
    except Exception:
        # If the DB is unhappy, the extraction itself should still run —
        # History just won't see this row. Log loudly so ops notices.
        logger.exception(
            "Failed to create runs row for session %s", session_id,
        )
        # Close a partially-opened connection so we don't leak file handles.
        if db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass
            db_conn = None

    coordinator_result = None  # type: ignore[assignment]
    merge_result = None
    cross_check_results: list = []
    # These are filled in by the validation block below and used by the
    # post-processing / persistence blocks further down.
    statements_to_run: Set[StatementType] = set()
    variants: Dict[StatementType, str] = {}
    models: Dict[StatementType, Any] = {}
    config: Optional[RunConfig] = None

    try:
        # --- Phase 3 fix: validate & construct INSIDE the outer try so
        # any failure path runs through the except block and marks the
        # runs row as failed. Previously these exits happened before the
        # row existed. ---

        # Parse statement types
        for s in run_config.statements:
            try:
                statements_to_run.add(StatementType(s))
            except ValueError:
                yield {"event": "error", "data": {"message": f"Unknown statement type: {s}"}}
                yield {"event": "run_complete", "data": {"success": False, "message": f"Unknown statement type: {s}"}}
                if _safe_mark_finished(db_conn, run_id, "failed"):
                    terminal_status = "failed"
                return

        # Build variant map — fall back to first registered variant if not specified
        for stmt in statements_to_run:
            if stmt.value in run_config.variants:
                variants[stmt] = run_config.variants[stmt.value]
            # else: coordinator will resolve from infopack / registry default.

        # Build model overrides — resolve each through _create_proxy_model so
        # per-agent overrides use the same proxy/direct wiring as the default.
        # Wrap in try/except so a broken override key also produces a clean
        # failed-row rather than bubbling out of the generator.
        try:
            for stmt in statements_to_run:
                if stmt.value in run_config.models:
                    override_name = run_config.models[stmt.value]
                    models[stmt] = _create_proxy_model(override_name, proxy_url, api_key)
        except Exception as e:
            logger.exception(
                "Override model construction failed for session %s", session_id,
            )
            yield {"event": "error", "data": {"message": f"Model override failed: {e}"}}
            yield {"event": "run_complete", "data": {"success": False, "message": f"Model override failed: {e}"}}
            if _safe_mark_finished(db_conn, run_id, "failed"):
                terminal_status = "failed"
            return

        # Resolve infopack
        infopack = None
        if run_config.infopack:
            from scout.infopack import Infopack
            try:
                # from_json expects a JSON string; request body gives us a dict
                infopack = Infopack.from_json(json.dumps(run_config.infopack))
            except Exception as e:
                yield {"event": "error", "data": {"message": f"Invalid infopack: {e}"}}
                yield {"event": "run_complete", "data": {"success": False, "message": f"Invalid infopack: {e}"}}
                if _safe_mark_finished(db_conn, run_id, "failed"):
                    terminal_status = "failed"
                return

        # Create the model object for the coordinator. May raise if the
        # proxy is unreachable or the API key is invalid — treat it as an
        # early validation failure (yield error + mark row failed + return)
        # so the user gets a clean SSE close instead of a 500.
        try:
            model = _create_proxy_model(model_name, proxy_url, api_key)
        except Exception as e:
            logger.exception(
                "Model construction failed for session %s", session_id,
            )
            yield {"event": "error", "data": {"message": f"Model setup failed: {e}"}}
            yield {"event": "run_complete", "data": {"success": False, "message": f"Model setup failed: {e}"}}
            if _safe_mark_finished(db_conn, run_id, "failed"):
                terminal_status = "failed"
            return

        config = RunConfig(
            pdf_path=str(session_dir / "uploaded.pdf"),
            output_dir=output_dir,
            model=model,
            statements_to_run=statements_to_run,
            variants=variants,
            models=models,
        )

        yield {"event": "status", "data": {
            "phase": "starting", "message": f"Starting extraction for {len(statements_to_run)} statements...",
        }}

        # Phase 6.5: create run_agents rows UP FRONT so tool events can be
        # keyed to the right agent as they stream out of the coordinator.
        # The old path created these rows at the end of the run, by which
        # point every tool_call had already been missed.
        #
        # We build a mapping {agent_id → run_agent_id} keyed by the SAME
        # agent_id the coordinator puts on every SSE event (lowercase
        # statement value, e.g. "sofp"). This lets persist_event resolve
        # the right run_agent_id in O(1) without re-querying the DB.
        run_agent_ids_by_agent_id: Dict[str, int] = {}
        # We also keep a parallel map keyed by StatementType for the
        # post-run finish_run_agent / save_extracted_field loop.
        run_agent_ids_by_stmt: Dict[StatementType, int] = {}
        if db_conn is not None and run_id is not None:
            try:
                # Iterate in sorted order so run_agents row IDs are
                # deterministic across test runs (statements_to_run is a
                # Set; its iteration order is hash-based and unstable).
                for stmt in sorted(statements_to_run, key=lambda s: s.value):
                    agent_model = config.models.get(stmt, config.model)
                    rai = repo.create_run_agent(
                        db_conn, run_id,
                        statement_type=stmt.value,
                        variant=variants.get(stmt),
                        model=_model_id(agent_model),
                    )
                    run_agent_ids_by_agent_id[stmt.value.lower()] = rai
                    run_agent_ids_by_stmt[stmt] = rai
                db_conn.commit()
            except Exception:
                logger.warning("Failed to pre-create run_agents rows for %s",
                               session_id, exc_info=True)

        # Phase 6.5: in-place persistence of tool-level SSE events.
        # Mirrors db/recorder.py's _COARSE_EVENT_TYPES — we write status,
        # tool_call, tool_result, error, and complete rows. Thinking/text
        # deltas are intentionally dropped (too high-frequency, low audit
        # value). Failures self-disable for that agent so we never block
        # the live stream on a wedged DB.
        _persist_disabled: Set[int] = set()
        _COARSE_EVENT_TYPES_SET = frozenset({
            "status", "tool_call", "tool_result", "error", "complete",
        })

        def persist_event(evt: dict) -> None:
            if db_conn is None:
                return
            event_type = str(evt.get("event", ""))
            if event_type not in _COARSE_EVENT_TYPES_SET:
                return
            data = evt.get("data") or {}
            if not isinstance(data, dict):
                return
            agent_id_raw = data.get("agent_id") or data.get("agent_role")
            if not isinstance(agent_id_raw, str) or not agent_id_raw:
                return
            rai = run_agent_ids_by_agent_id.get(agent_id_raw.lower())
            if rai is None or rai in _persist_disabled:
                return
            try:
                phase = data.get("phase") if isinstance(data.get("phase"), str) else None
                repo.log_event(
                    db_conn,
                    run_agent_id=rai,
                    event_type=event_type,
                    payload=data,
                    phase=phase,
                )
                db_conn.commit()
            except Exception:
                # Stop trying for this agent — one failure likely means the
                # DB is wedged and we shouldn't spam warnings on every event.
                logger.warning(
                    "persist_event disabled for run_agent %s after error",
                    rai, exc_info=True,
                )
                _persist_disabled.add(rai)

        # Event bridge: concurrent agents push events into this queue,
        # and the SSE generator drains it in real time. None = all done.
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        # Launch coordinator as a background task so we can drain events while agents run
        coordinator_task = asyncio.create_task(
            coordinator_run(config, infopack=infopack, event_queue=event_queue, session_id=session_id)
        )

        # Drain events from the queue as they arrive from concurrent agents.
        # If the client disconnects (GeneratorExit), cancel the coordinator task
        # so we don't leave agents running in the background.
        try:
            while True:
                event = await event_queue.get()
                if event is None:
                    # Sentinel: all agents finished
                    break
                persist_event(event)
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            coordinator_task.cancel()
            logger.info("Client disconnected, cancelled coordinator", extra={"session_id": session_id})
            # Mark the row aborted before we return — the finally block will
            # see terminal_status set and leave it alone.
            if _safe_mark_finished(db_conn, run_id, "aborted"):
                terminal_status = "aborted"
            return
        except Exception as e:
            logger.exception("Event queue drain failed", extra={"session_id": session_id})
            yield {"event": "error", "data": {"message": f"Stream error: {e}"}}

        # Await the coordinator task to get CoordinatorResult for post-processing
        try:
            coordinator_result = await coordinator_task
        except asyncio.CancelledError:
            logger.info("Coordinator cancelled", extra={"session_id": session_id})
            if _safe_mark_finished(db_conn, run_id, "aborted"):
                terminal_status = "aborted"
            yield {"event": "error", "data": {"message": "Run cancelled"}}
            return
        except Exception as e:
            logger.exception("Coordinator failed", extra={"session_id": session_id})
            if _safe_mark_finished(db_conn, run_id, "failed"):
                terminal_status = "failed"
            yield {"event": "error", "data": {"message": f"Coordinator error: {e}"}}
            return

        # Generate merged result.json from per-statement files so the
        # preview tab can fetch a single file in both single- and multi-agent modes.
        # Uses a list (not dict) to preserve duplicate labels (e.g. "Lease liabilities"
        # appearing in both current and non-current sections of SOFP).
        merged_fields: list[dict] = []
        for agent_result in coordinator_result.agent_results:
            stmt_result_path = Path(output_dir) / f"{agent_result.statement_type.value}_result.json"
            if stmt_result_path.exists():
                try:
                    stmt_data = json.loads(stmt_result_path.read_text(encoding="utf-8"))
                    stmt_key = agent_result.statement_type.value
                    for field in stmt_data.get("fields", []):
                        merged_fields.append({
                            "statement": stmt_key,
                            "field_label": field.get("field_label", ""),
                            "value": field.get("value"),
                            "section": field.get("section"),
                        })
                except Exception:
                    logger.warning("Failed to merge result for %s", agent_result.statement_type.value, exc_info=True)
        if merged_fields:
            merged_result_path = Path(output_dir) / "result.json"
            merged_result_path.write_text(
                json.dumps({"fields": merged_fields}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        # Build workbook paths from ALL *_filled.xlsx in the session directory.
        # This ensures reruns merge with previously successful workbooks.
        all_workbook_paths: Dict[StatementType, str] = {}
        for stmt in StatementType:
            wb_path = session_dir / f"{stmt.value}_filled.xlsx"
            if wb_path.exists():
                all_workbook_paths[stmt] = str(wb_path)
        # Override with any just-completed workbooks from this run
        all_workbook_paths.update(coordinator_result.workbook_paths)

        # Merge workbooks (Phase 7.4)
        merge_result = merge_workbooks(all_workbook_paths, merged_path)

        # Record the merged workbook path on the runs row IMMEDIATELY after
        # a successful merge, before we tackle cross-checks and per-agent
        # persistence. This is what History's download endpoint reads as
        # the single source of truth — never derived from session_id.
        if merge_result.success and db_conn is not None and run_id is not None:
            try:
                repo.mark_run_merged(db_conn, run_id, merged_path)
                db_conn.commit()
            except Exception:
                logger.warning(
                    "Failed to mark run_merged on run %s", run_id, exc_info=True,
                )

        # Run cross-checks (Phase 5 wiring)
        all_checks = [
            SOFPBalanceCheck(), SOPLToSOCIEProfitCheck(), SOCIToSOCIETCICheck(),
            SOCIEToSOFPEquityCheck(), SOCFToSOFPCashCheck(),
        ]
        check_config = {
            "statements_to_run": statements_to_run,
            "variants": {stmt: v for stmt, v in variants.items()},
        }
        tolerance = float(os.environ.get("XBRL_TOLERANCE_RM", "1.0"))
        cross_check_results = run_cross_checks(
            all_checks, all_workbook_paths, check_config,
            tolerance=tolerance,
        )

        # Persist per-agent FINAL state + extracted fields + cross-checks.
        # Phase 6.5 moved create_run_agent() UP FRONT so tool events could
        # be persisted live as the stream came in; this block now only
        # finalises each agent row (finish_run_agent) and writes the
        # extracted-field table. The coarse `status:started` and `complete`
        # log_event() calls that lived here have been removed — the live
        # stream already persisted the real complete event with the live
        # `{success: bool, error: str | None}` shape.
        if db_conn is not None and run_id is not None:
            try:
                for agent_result in coordinator_result.agent_results:
                    run_agent_id = run_agent_ids_by_stmt.get(agent_result.statement_type)
                    if run_agent_id is None:
                        # Pre-create didn't happen (DB was unhappy earlier);
                        # fall back to creating the row now so extracted
                        # fields still have somewhere to hang off.
                        agent_model = config.models.get(agent_result.statement_type, config.model)
                        run_agent_id = repo.create_run_agent(
                            db_conn, run_id,
                            statement_type=agent_result.statement_type.value,
                            variant=agent_result.variant,
                            model=_model_id(agent_model),
                        )
                    status = agent_result.status
                    # Pass the coordinator-resolved variant so runs where
                    # the user didn't specify one still record which
                    # template was actually used. (Phase 6.5 pre-creates
                    # run_agents with the user-supplied variant, which may
                    # be None.)
                    repo.finish_run_agent(
                        db_conn, run_agent_id,
                        status=status,
                        workbook_path=agent_result.workbook_path,
                        variant=agent_result.variant,
                    )

                    # Persist extracted fields from per-statement result.json
                    result_json_path = Path(output_dir) / f"{agent_result.statement_type.value}_result.json"
                    if result_json_path.exists():
                        try:
                            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))
                            for field in result_data.get("fields", []):
                                repo.save_extracted_field(
                                    db_conn, run_agent_id,
                                    sheet=field.get("sheet", ""),
                                    field_label=field.get("field_label", ""),
                                    col=field.get("col", 2),
                                    value=field.get("value"),
                                    section=field.get("section"),
                                    row_num=field.get("row"),
                                    evidence=field.get("evidence"),
                                )
                        except Exception as e:
                            logger.warning("Failed to persist fields for %s: %s",
                                           agent_result.statement_type.value, e)

                # Persist cross-check results
                for check_result in cross_check_results:
                    repo.save_cross_check(
                        db_conn, run_id,
                        check_name=check_result.name,
                        status=check_result.status,
                        expected=check_result.expected,
                        actual=check_result.actual,
                        diff=check_result.diff,
                        tolerance=check_result.tolerance,
                        message=check_result.message,
                    )
                db_conn.commit()
            except Exception as e:
                logger.warning("Failed to persist run data to audit DB: %s", e)

        # Compute the final run-level status — include merge outcome AND
        # cross-check results — and stamp it on the runs row.
        any_check_failed = any(cr.status == "failed" for cr in cross_check_results)
        if coordinator_result.all_succeeded and merge_result.success and not any_check_failed:
            overall_status = "completed"
        elif coordinator_result.all_succeeded and not merge_result.success:
            overall_status = "completed_with_errors"
        elif coordinator_result.all_succeeded and any_check_failed:
            overall_status = "completed_with_errors"
        else:
            overall_status = "failed"
        if _safe_mark_finished(db_conn, run_id, overall_status):
            terminal_status = overall_status

        # Emit cross-check results as SSE events
        checks_data = []
        for cr in cross_check_results:
            checks_data.append({
                "name": cr.name,
                "status": cr.status,
                "expected": cr.expected,
                "actual": cr.actual,
                "diff": cr.diff,
                "tolerance": cr.tolerance,
                "message": cr.message,
            })

        # Final run_complete event — success requires agents + merge + cross-checks all passing
        yield {"event": "run_complete", "data": {
            "success": coordinator_result.all_succeeded and merge_result.success and not any_check_failed,
            "merged_workbook": merged_path if merge_result.success else None,
            "merge_errors": merge_result.errors,
            "cross_checks": checks_data,
            "statements_completed": [r.statement_type.value for r in coordinator_result.agent_results
                                      if r.status == "succeeded"],
            "statements_failed": [r.statement_type.value for r in coordinator_result.agent_results
                                   if r.status == "failed"],
        }}
    except BaseException:
        # Belt-and-braces: if we reach the outer except without having
        # already recorded a terminal state, mark the run failed so History
        # never shows a dangling 'running' row. BaseException catches
        # CancelledError + KeyboardInterrupt too.
        if terminal_status is None:
            _safe_mark_finished(db_conn, run_id, "failed")
            terminal_status = "failed"
        raise
    finally:
        # Last-ditch cleanup: if no other code path left the row in a
        # terminal state (e.g. the event loop was torn down between yields),
        # call it aborted. Idempotent for rows that were already finalized.
        if terminal_status is None:
            _safe_mark_finished(db_conn, run_id, "aborted")
        if db_conn is not None:
            try:
                db_conn.close()
            except Exception:
                pass


@app.post("/api/run/{session_id}")
async def run_multi_extraction(session_id: str, body: RunConfigRequest):
    """Multi-agent SSE endpoint — runs extraction for multiple statements.

    Accepts a RunConfig body specifying which statements to extract,
    their variants, optional model overrides, and optional infopack
    from a prior scout run.
    """
    session_dir = OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found. Upload first.")

    if session_id in active_runs:
        raise HTTPException(status_code=409, detail="Extraction already running for this session.")

    load_dotenv(ENV_FILE, override=True)
    api_key = _resolve_api_key()
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    model_name = os.environ.get("TEST_MODEL", "vertex_ai.gemini-3-flash-preview")

    if not api_key:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY (Mac) or GOOGLE_API_KEY (Windows proxy) must be set. Check Settings.")

    active_runs.add(session_id)

    async def event_stream():
        try:
            async for evt in run_multi_agent_stream(
                session_id=session_id,
                session_dir=session_dir,
                run_config=body,
                api_key=api_key,
                proxy_url=proxy_url,
                model_name=model_name,
            ):
                yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
        finally:
            active_runs.discard(session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


## Legacy GET /api/run/{session_id} endpoint was removed in Phase 11.3.
## Use POST /api/run/{session_id} with RunConfigRequest body instead.


# ---------------------------------------------------------------------------
# Abort endpoints — cancel running agents without restarting
# ---------------------------------------------------------------------------

@app.post("/api/abort/{session_id}")
async def abort_session(session_id: str):
    """Cancel ALL running agents for a session."""
    import task_registry
    count = task_registry.cancel_all(session_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="No active tasks for this session.")
    return {"cancelled": count}


@app.post("/api/abort/{session_id}/{agent_id}")
async def abort_agent(session_id: str, agent_id: str):
    """Cancel a single agent within a session (e.g. abort just SOPL)."""
    import task_registry
    if not task_registry.cancel_agent(session_id, agent_id):
        raise HTTPException(status_code=404, detail="Agent not found or already finished.")
    return {"cancelled": agent_id}


# ---------------------------------------------------------------------------
# Rerun endpoint — re-extract a single statement in an existing session
# ---------------------------------------------------------------------------

@app.post("/api/rerun/{session_id}")
async def rerun_agent(session_id: str, body: RunConfigRequest):
    """Re-run extraction for a single statement within an existing session.

    Reuses the same output directory so the new workbook overwrites the old one.
    After the agent finishes, merge + cross-checks run against all workbooks
    in the session (both old successful ones and the new one).
    """
    if len(body.statements) != 1:
        raise HTTPException(status_code=400, detail="Rerun expects exactly one statement.")

    # Block rerun while an extraction is already running for this session
    if session_id in active_runs:
        raise HTTPException(status_code=409, detail="Extraction still running. Wait for it to finish before rerunning.")

    session_dir = OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found for this session.")

    load_dotenv(ENV_FILE, override=True)
    api_key = _resolve_api_key()
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    model_name = os.environ.get("TEST_MODEL", "vertex_ai.gemini-3-flash-preview")

    if not api_key:
        raise HTTPException(status_code=400, detail="API key not set. Check Settings.")

    active_runs.add(session_id)

    async def event_stream():
        try:
            async for evt in run_multi_agent_stream(
                session_id=session_id,
                session_dir=session_dir,
                run_config=body,
                api_key=api_key,
                proxy_url=proxy_url,
                model_name=model_name,
            ):
                yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
        finally:
            active_runs.discard(session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# --- Test connection endpoint ---

@app.post("/api/test-connection")
async def test_connection(body: dict):
    """Test LLM connectivity with provided or .env settings."""
    load_dotenv(ENV_FILE, override=True)

    model_name = body.get("model") or os.environ.get("TEST_MODEL", "vertex_ai.gemini-3-flash-preview")
    api_key = body.get("api_key") or os.environ.get("GOOGLE_API_KEY", "")
    proxy_url = body.get("proxy_url") or os.environ.get("LLM_PROXY_URL", "")

    if not api_key:
        return JSONResponse(status_code=400, content={"status": "error", "message": "API key is required."})

    start = time.time()
    try:
        from pydantic_ai import Agent

        model = _create_proxy_model(model_name, proxy_url, api_key)
        test_agent = Agent(model)
        result = await test_agent.run("Say OK")
        latency_ms = int((time.time() - start) * 1000)
        return {"status": "ok", "model": model_name, "latency_ms": latency_ms}
    except Exception as e:
        logger.exception("Connection test failed", extra={"model": model_name})
        return JSONResponse(status_code=502, content={"status": "error", "message": str(e)})


# ---------------------------------------------------------------------------
# History API — Phase 3 of frontend-upgrade-history
#
# Four endpoints under /api/runs that the new History tab consumes:
#   GET    /api/runs                     — list with filters + pagination
#   GET    /api/runs/{id}                — hydrated detail (agents + checks)
#   DELETE /api/runs/{id}                — DB-only delete (leaves disk alone)
#   GET    /api/runs/{id}/download/filled — stream the merged workbook
#
# All reads go through `db.repository`; this module never speaks raw SQL.
# ---------------------------------------------------------------------------

def _open_audit_conn():
    """Open an audit-DB connection with the same pragmas as the lifecycle
    path. Callers must close it themselves (or use the contextmanager via
    `db_session`)."""
    from db.schema import init_db
    import sqlite3
    init_db(AUDIT_DB_PATH)
    conn = sqlite3.connect(str(AUDIT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    return conn


def _run_summary_to_dict(summary) -> dict:
    """Serialise a repository.RunSummary for the History list JSON payload.

    Kept separate so both the list and the detail endpoint can reuse a
    consistent wire shape if we ever want to embed a summary in the detail.
    """
    return {
        "id": summary.id,
        "created_at": summary.created_at,
        "pdf_filename": summary.pdf_filename,
        "status": summary.status,
        "session_id": summary.session_id,
        "statements_run": summary.statements_run,
        "models_used": summary.models_used,
        "duration_seconds": summary.duration_seconds,
        "scout_enabled": summary.scout_enabled,
        "has_merged_workbook": bool(summary.merged_workbook_path),
    }


@app.get("/api/runs")
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
    # keyword), so we alias via a secondary dependency below.
):
    """List past runs with optional filters. Newest first by default."""
    from db import repository as repo
    # Clamp once, use for both the DB query AND the response payload.
    # Previously the response echoed the raw request values, so a caller
    # asking for limit=500 would get back 200 rows with limit=500 in the
    # payload, desyncing client-side pagination math (Load More offsets).
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    conn = _open_audit_conn()
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
        "runs": [_run_summary_to_dict(s) for s in summaries],
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
    }


# Because `from` is a Python keyword we cannot name a function parameter
# `from`. FastAPI exposes a `Query(..., alias="from")` pattern but our
# simpler approach is a second entry point that reads raw query params off
# the request and forwards them — keeps the typed handler above clean.
@app.middleware("http")
async def _history_date_range_alias(request: Request, call_next):
    """Rewrite `?from=...&to=...` to `?date_from=...&date_to=...` on /api/runs.

    The frontend uses the human-friendly names; the handler signature uses
    the Python-safe names. Doing the rewrite here keeps both ends ergonomic
    without polluting unrelated endpoints.
    """
    # Only touch /api/runs reads — never anywhere else.
    if request.url.path == "/api/runs" and request.method == "GET":
        params = dict(request.query_params)
        mutated = False
        if "from" in params and "date_from" not in params:
            params["date_from"] = params.pop("from")
            mutated = True
        if "to" in params and "date_to" not in params:
            params["date_to"] = params.pop("to")
            mutated = True
        if mutated:
            # Rebuild the querystring and reassign. Starlette's request
            # query params are immutable; we swap the underlying scope.
            from urllib.parse import urlencode
            new_qs = urlencode(params)
            request.scope["query_string"] = new_qs.encode("utf-8")
    return await call_next(request)


@app.get("/api/runs/{run_id}")
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
    conn = _open_audit_conn()
    try:
        detail = repo.get_run_detail(conn, run_id)
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
                "events": [_serialize_event(e) for e in a.events],
            }
            for a in detail.agents
        ],
        "cross_checks": [
            {
                "name": c.check_name,
                "status": c.status,
                "expected": c.expected,
                "actual": c.actual,
                "diff": c.diff,
                "tolerance": c.tolerance,
                "message": c.message,
            }
            for c in detail.cross_checks
        ],
    }


@app.delete("/api/runs/{run_id}")
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
    conn = _open_audit_conn()
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
        if run.session_id and run.session_id in active_runs:
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


@app.get("/api/runs/{run_id}/download/filled")
async def download_filled_endpoint(run_id: int):
    """Stream the merged workbook for a past run.

    Single source of truth for the file path is `runs.merged_workbook_path`.
    We explicitly do NOT derive the path from session_id or probe the
    filesystem — if the stored path no longer exists on disk we return a
    clear 404 instead of a 500.
    """
    from db import repository as repo
    conn = _open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not run.merged_workbook_path:
        raise HTTPException(
            status_code=404,
            detail="This run has no merged workbook (likely failed before merge).",
        )
    wb_path = Path(run.merged_workbook_path)
    if not wb_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Merged workbook file no longer exists on disk: {wb_path}",
        )
    return FileResponse(
        str(wb_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"run_{run_id}_filled.xlsx",
    )


# --- Download endpoints ---

ALLOWED_DOWNLOADS = {"filled.xlsx", "result.json", "conversation_trace.json"}
# Per-statement output files are also downloadable
_STMT_PREFIXES = ("SOFP_", "SOPL_", "SOCI_", "SOCF_", "SOCIE_")


@app.get("/api/result/{session_id}/{filename}")
async def download_result(session_id: str, filename: str):
    # Allow per-statement files (e.g. SOFP_filled.xlsx, SOPL_result.json)
    is_stmt_file = any(filename.startswith(p) for p in _STMT_PREFIXES) and filename.endswith((".xlsx", ".json", ".txt"))
    if filename not in ALLOWED_DOWNLOADS and not is_stmt_file:
        raise HTTPException(status_code=400, detail=f"File not available. Allowed: {ALLOWED_DOWNLOADS}")

    file_path = OUTPUT_DIR / session_id / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    return FileResponse(str(file_path), filename=filename)


# --- Serve built frontend (Vite output in dist/) ---
#
# Two-layer wiring:
#
#  1. A SPA-fallback catch-all is registered BEFORE the StaticFiles mount.
#     For any non-API GET that StaticFiles can't satisfy with a real file,
#     we return `index.html` so the React router can pick the URL up on the
#     client side. Without this, refreshing /history (or any future client
#     route) returns 404 in production.
#
#  2. The StaticFiles mount still serves real assets (JS bundles, CSS,
#     images, the hashed Vite outputs under /assets/...) verbatim. The
#     fallback only fires when StaticFiles itself would 404.
#
# Why register the fallback BEFORE the mount? FastAPI matches routes in
# definition order. The mount at "/" is greedy and would otherwise
# intercept every GET first.
#
# Extracted as a helper so tests can wire it up against a temp dist
# directory without monkeypatching module globals.

def mount_spa(app, dist_directory: Path) -> None:
    """Register the SPA fallback + StaticFiles mount onto a FastAPI app.

    Idempotent only at module-load time — calling this twice on the same
    app will register two catch-all handlers, which is fine for testing
    but should not happen in production code paths.
    """
    index_html = dist_directory / "index.html"
    resolved_dist = dist_directory.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        # API routes never fall through to the SPA — a typo'd /api/... must
        # surface as a real 404 so client code doesn't parse HTML as JSON.
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="Not found")

        # If the request resolves to a real file inside dist/, serve it.
        # Path-traversal guard: resolve and confirm the result is still
        # under dist_dir before opening it.
        if full_path:
            candidate = (dist_directory / full_path).resolve()
            try:
                candidate.relative_to(resolved_dist)
            except ValueError:
                raise HTTPException(status_code=404, detail="Not found")
            if candidate.is_file():
                return FileResponse(str(candidate))

        # Otherwise hand back the SPA shell. The client router takes over
        # from here and renders the right view based on window.location.
        return FileResponse(str(index_html), media_type="text/html")

    # Mount StaticFiles AFTER the catch-all so the catch-all wins for
    # arbitrary paths but the mount can still handle the bare "/" request
    # (and gives us the asset MIME-type defaults StaticFiles bakes in).
    app.mount("/", StaticFiles(directory=str(dist_directory), html=True), name="frontend")


dist_dir = BASE_DIR / "dist"
if dist_dir.exists():
    mount_spa(app, dist_dir)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    load_dotenv(ENV_FILE)
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8002"))
    logger.info(f"Starting SOFP Agent Web UI on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
