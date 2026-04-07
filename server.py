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


def _calc_cost(usage) -> float:
    """Estimate cost from usage data. Rough pricing for Gemini via proxy."""
    prompt = getattr(usage, "input_tokens", 0) or 0
    completion = getattr(usage, "output_tokens", 0) or 0
    # Approximate Gemini Flash pricing (per 1M tokens)
    return (prompt * 0.075 + completion * 0.30) / 1_000_000


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
        progress_queue: asyncio.Queue[str] = asyncio.Queue()

        async def on_progress(msg: str) -> None:
            await progress_queue.put(msg)

        try:
            yield f"event: status\ndata: {json.dumps({'phase': 'scouting', 'message': 'Starting scout...'})}\n\n"

            from scout.runner import run_scout
            scout_task = asyncio.create_task(run_scout(
                pdf_path=pdf_path,
                model=scout_model,
                on_progress=on_progress,
            ))

            # Yield progress events while scout runs
            while not scout_task.done():
                try:
                    msg = await asyncio.wait_for(progress_queue.get(), timeout=0.3)
                    yield f"event: status\ndata: {json.dumps({'phase': 'scouting', 'message': msg})}\n\n"
                except asyncio.TimeoutError:
                    continue

            # Drain any remaining progress messages
            while not progress_queue.empty():
                msg = progress_queue.get_nowait()
                yield f"event: status\ndata: {json.dumps({'phase': 'scouting', 'message': msg})}\n\n"

            infopack = scout_task.result()

            # to_json() returns a JSON string; parse it to embed as a nested dict
            infopack_dict = json.loads(infopack.to_json())
            yield f"event: scout_complete\ndata: {json.dumps({'success': True, 'infopack': infopack_dict})}\n\n"

        except Exception as e:
            logger.exception("Scout failed", extra={"session_id": session_id})
            yield f"event: error\ndata: {json.dumps({'message': str(e), 'traceback': traceback.format_exc()})}\n\n"

    return StreamingResponse(
        scout_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# --- Multi-agent SSE run endpoint (Phase 7.2 + 7.3 + 7.4) ---

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
    from db.recorder import SSEEventRecorder
    from db.schema import init_db
    from db import repository as repo

    # Parse statement types
    statements_to_run: Set[StatementType] = set()
    for s in run_config.statements:
        try:
            statements_to_run.add(StatementType(s))
        except ValueError:
            yield {"event": "error", "data": {"message": f"Unknown statement type: {s}"}}
            yield {"event": "run_complete", "data": {"success": False, "message": f"Unknown statement type: {s}"}}
            return

    # Build variant map — fall back to first registered variant if not specified
    variants: Dict[StatementType, str] = {}
    for stmt in statements_to_run:
        if stmt.value in run_config.variants:
            variants[stmt] = run_config.variants[stmt.value]
        else:
            # Will be resolved by coordinator (infopack suggestion or registry default)
            pass

    # Build model overrides — resolve each through _create_proxy_model so
    # per-agent overrides use the same proxy/direct wiring as the default model.
    models: Dict[StatementType, Any] = {}
    for stmt in statements_to_run:
        if stmt.value in run_config.models:
            override_name = run_config.models[stmt.value]
            models[stmt] = _create_proxy_model(override_name, proxy_url, api_key)

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
            return

    output_dir = str(session_dir)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Create the model object for the coordinator
    model = _create_proxy_model(model_name, proxy_url, api_key)

    config = RunConfig(
        pdf_path=str(session_dir / "uploaded.pdf"),
        output_dir=output_dir,
        model=model,
        statements_to_run=statements_to_run,
        variants=variants,
        models=models,
    )

    # Set up audit DB
    init_db(AUDIT_DB_PATH)

    yield {"event": "status", "data": {
        "phase": "starting", "message": f"Starting extraction for {len(statements_to_run)} statements...",
    }}

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
            yield event
    except (asyncio.CancelledError, GeneratorExit):
        coordinator_task.cancel()
        logger.info("Client disconnected, cancelled coordinator", extra={"session_id": session_id})
        return
    except Exception as e:
        logger.exception("Event queue drain failed", extra={"session_id": session_id})
        yield {"event": "error", "data": {"message": f"Stream error: {e}"}}

    # Await the coordinator task to get CoordinatorResult for post-processing
    try:
        coordinator_result = await coordinator_task
    except Exception as e:
        logger.exception("Coordinator failed", extra={"session_id": session_id})
        yield {"event": "error", "data": {"message": f"Coordinator error: {e}"}}
        return

    # Generate merged result.json from per-statement files so the
    # preview tab can fetch a single file in both single- and multi-agent modes.
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
    merged_path = str(session_dir / "filled.xlsx")
    merge_result = merge_workbooks(all_workbook_paths, merged_path)

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

    # Persist to audit DB
    try:
        import sqlite3
        conn = sqlite3.connect(str(AUDIT_DB_PATH))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row

        run_id = repo.create_run(conn, Path(config.pdf_path).name)

        for agent_result in coordinator_result.agent_results:
            # Persist a serializable model name, not the provider-backed
            # Model object — SQLite cannot store arbitrary Python objects.
            agent_model = config.models.get(agent_result.statement_type, config.model)
            run_agent_id = repo.create_run_agent(
                conn, run_id,
                statement_type=agent_result.statement_type.value,
                variant=agent_result.variant,
                model=str(agent_model),
            )
            status = agent_result.status
            repo.finish_run_agent(conn, run_agent_id, status=status,
                                  workbook_path=agent_result.workbook_path)

            # Persist coarse agent_events for audit trail (mirrors what
            # SSEEventRecorder does in the legacy single-agent path).
            repo.log_event(conn, run_agent_id, "status", {
                "phase": "started",
                "statement_type": agent_result.statement_type.value,
                "variant": agent_result.variant,
            })
            # Persist conversation trace as a complete event
            trace_path = Path(output_dir) / f"{agent_result.statement_type.value}_conversation_trace.json"
            if not trace_path.exists():
                # Fall back to shared trace name (single-agent compat)
                trace_path = Path(output_dir) / "conversation_trace.json"
            trace_blob = None
            if trace_path.exists():
                try:
                    trace_blob = trace_path.read_text(encoding="utf-8")
                except Exception:
                    pass
            repo.log_event(conn, run_agent_id, "complete", {
                "status": status,
                "workbook_path": agent_result.workbook_path,
                "error": agent_result.error,
                "has_trace": trace_blob is not None,
            })

            # Persist extracted fields from per-statement result.json
            result_json_path = Path(output_dir) / f"{agent_result.statement_type.value}_result.json"
            if result_json_path.exists():
                try:
                    result_data = json.loads(result_json_path.read_text(encoding="utf-8"))
                    for field in result_data.get("fields", []):
                        repo.save_extracted_field(
                            conn, run_agent_id,
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
                conn, run_id,
                check_name=check_result.name,
                status=check_result.status,
                expected=check_result.expected,
                actual=check_result.actual,
                diff=check_result.diff,
                tolerance=check_result.tolerance,
                message=check_result.message,
            )

        # Update run status — include merge outcome: merge failure degrades to "completed_with_errors"
        if coordinator_result.all_succeeded and merge_result.success:
            overall_status = "completed"
        elif coordinator_result.all_succeeded and not merge_result.success:
            overall_status = "completed_with_errors"
        else:
            overall_status = "failed"
        repo.update_run_status(conn, run_id, overall_status)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to persist run to audit DB: %s", e)

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

    # Final run_complete event — success requires both agent completion AND merge
    yield {"event": "run_complete", "data": {
        "success": coordinator_result.all_succeeded and merge_result.success,
        "merged_workbook": merged_path if merge_result.success else None,
        "merge_errors": merge_result.errors,
        "cross_checks": checks_data,
        "statements_completed": [r.statement_type.value for r in coordinator_result.agent_results
                                  if r.status == "succeeded"],
        "statements_failed": [r.statement_type.value for r in coordinator_result.agent_results
                               if r.status == "failed"],
    }}


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

dist_dir = BASE_DIR / "dist"
if dist_dir.exists():
    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="frontend")


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
