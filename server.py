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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Dict, List, Literal, Optional, Set, Any

from dotenv import load_dotenv, set_key
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

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

# Notes templates that are safe to expose over the public API / CLI.
# Kept separate from NotesTemplateType so the enum stays a superset (new
# template types can be drafted and tested before being exposed). A
# tests/test_server_notes_api.py assertion catches accidental drift
# between this allowlist and the enum.
def _public_notes_templates() -> frozenset:
    from notes_types import NotesTemplateType as _NT
    return frozenset({
        _NT.CORP_INFO,
        _NT.ACC_POLICIES,
        _NT.LIST_OF_NOTES,
        _NT.ISSUED_CAPITAL,
        _NT.RELATED_PARTY,
    })


_PUBLIC_NOTES_TEMPLATES = _public_notes_templates()


# `_build_default_cross_checks` moved to `cross_checks.framework` in the
# peer-review round so `correction.agent` no longer needs a lazy
# `from server import …`. Keep a local alias for back-compat with tests
# that already import from `server` directly (MPERS wiring pins).
from cross_checks.framework import (
    build_default_cross_checks as _build_default_cross_checks,
)


# Lazy imports for multi-agent pipeline — done at call sites to keep startup fast.
# scout.runner.run_scout, coordinator.run_extraction, workbook_merger.merge,
# cross_checks.framework.run_all, etc.


# ---------------------------------------------------------------------------
# Model creation
# ---------------------------------------------------------------------------

def _resolve_api_key() -> str:
    """Return the best available API key: GOOGLE_API_KEY (proxy) or GEMINI_API_KEY (direct)."""
    return os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")


# Provider-prefix forms that appear in config/models.json and PydanticAI
# namespacing. Order matters — the longest match must come first so that
# "bedrock.anthropic." is stripped before any prefix that shares its head.
_PROVIDER_PREFIXES: tuple[str, ...] = (
    "bedrock.anthropic.",
    "vertex_ai.",
    "openai.",
    "google-gla:",
    "google-vertex:",
)


def _strip_provider_prefix(model_name: str) -> str:
    """Return the bare model id with any known registry prefix removed.

    The registry IDs in config/models.json are fully qualified (e.g.
    `openai.gpt-5.4`, `bedrock.anthropic.claude-sonnet-4-6`). Both provider
    detection and direct-mode model construction need the bare name (e.g.
    `gpt-5.4`), so this helper is the single source of truth.
    """
    for prefix in _PROVIDER_PREFIXES:
        if model_name.startswith(prefix):
            return model_name[len(prefix):]
    return model_name


def _detect_provider(model_name: str) -> str:
    """Infer the provider from a model name string.

    Returns 'openai', 'anthropic', or 'google'. Handles both bare names
    (`gpt-5.4`) and prefixed registry IDs (`openai.gpt-5.4`) by stripping
    the prefix before matching.
    """
    bare = _strip_provider_prefix(model_name).lower()
    if bare.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return "openai"
    if bare.startswith("claude-"):
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

    # Direct API paths — route by provider.
    # The registry IDs carry a provider prefix (e.g. "openai.gpt-5.4"); the
    # upstream SDKs expect bare names, so strip once up front and use the
    # bare form for both detection and construction.
    bare_name = _strip_provider_prefix(model_name)
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
        return OpenAIChatModel(bare_name, provider=provider)

    if detected == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            raise ValueError(
                f"Model '{model_name}' requires ANTHROPIC_API_KEY in .env but it is not set."
            )
        provider = AnthropicProvider(api_key=anthropic_key)
        return AnthropicModel(bare_name, provider=provider)

    # Google Gemini direct path — GoogleModel expects bare names like
    # "gemini-3-flash-preview".
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    provider = GoogleProvider(api_key=api_key)
    return GoogleModel(bare_name, provider=provider)



# ---------------------------------------------------------------------------
# Phase 3: correction-agent helper (module-scope for testability)
# ---------------------------------------------------------------------------

# Agent-id the correction pass emits under. Matches the frontend's CORRECTION
# tab routing; kept here as a single source of truth so backend + frontend +
# tests can't drift.
CORRECTION_AGENT_ID = "CORRECTION"
NOTES_VALIDATOR_AGENT_ID = "NOTES_VALIDATOR"

# Per-turn timeout for the correction agent. Mirrors the notes coordinator's
# NOTES_TURN_TIMEOUT — 180s is comfortably above healthy p99 for a single
# model turn and catches the minute-long stalls we've seen on PydanticAI.
CORRECTION_TURN_TIMEOUT: float = 180.0

# Same bound for the notes post-validator. Without it, a stalled model
# turn after merge leaves the whole run hung in `running` (violating
# gotcha #10 — every exit path must reach a terminal status).
NOTES_VALIDATOR_TURN_TIMEOUT: float = 180.0


async def _run_correction_pass(
    failed_checks: list,
    merged_workbook_path: str,
    pdf_path: str,
    infopack,
    filing_level: str,
    filing_standard: str,
    model,
    output_dir: str,
    event_queue,
    statements_to_run: Optional[set] = None,
    agent_id: str = CORRECTION_AGENT_ID,
    variants: Optional[dict] = None,
) -> dict:
    """Run the correction agent once against a failed cross-check set.

    Bounded to 1 iteration per PLAN D4. The helper streams the agent's
    tool events into ``event_queue`` under ``agent_id`` so the frontend
    + DB persistence can route them exactly like face / notes agents.

    Returns a dict describing what happened:
        {
          "invoked": bool,              # did we actually launch the agent
          "writes_performed": int,      # fill_workbook invocations (by field)
          "error": Optional[str],       # set on failure
        }

    The caller is responsible for re-running cross-checks afterwards —
    this helper does not know what the "fresh" check registry looks like.
    """
    import asyncio as _asyncio
    from correction.agent import create_correction_agent
    from notes.coordinator import _iter_with_turn_timeout
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
    )
    from pydantic_ai import Agent

    outcome: dict = {"invoked": False, "writes_performed": 0, "error": None}

    async def _emit(event_type: str, data: dict) -> None:
        if event_queue is None:
            return
        await event_queue.put({
            "event": event_type,
            "data": {**data, "agent_id": agent_id, "agent_role": agent_id},
        })

    if not failed_checks:
        return outcome

    outcome["invoked"] = True

    # Default to all statements only when the caller didn't narrow — the
    # prod path in run_multi_agent_stream always threads the real set so
    # the agent's run_cross_checks tool sees the same scope as the outer
    # run. Tests that construct the helper directly without the kwarg
    # stay functional (they don't invoke the tool).
    if statements_to_run is None:
        from statement_types import StatementType as _ST
        statements_to_run = set(_ST)

    try:
        agent, deps = create_correction_agent(
            merged_workbook_path=merged_workbook_path,
            pdf_path=pdf_path,
            failed_checks=failed_checks,
            infopack=infopack,
            filing_level=filing_level,
            filing_standard=filing_standard,
            model=model,
            output_dir=output_dir,
            statements_to_run=statements_to_run,
            variants=variants,
        )
    except Exception as e:  # noqa: BLE001 — defensive at the coordinator boundary
        logger.exception("Correction agent construction failed")
        # Peer-review S3: include the exception class in the user-facing
        # SSE message so "agent construction failed: <str>" shows the
        # error type alongside its message.
        outcome["error"] = f"agent construction failed: {type(e).__name__}: {e}"
        await _emit("error", {"message": outcome["error"]})
        await _emit("complete", {"success": False, "error": outcome["error"]})
        return outcome

    prompt = (
        "Investigate the failed cross-checks in your system prompt, correct "
        "the wrong cell(s) via fill_workbook, re-verify each touched sheet, "
        "and call run_cross_checks once when finished. You have one "
        "iteration — do not re-extract entire sheets."
    )

    await _emit("status", {
        "phase": "started",
        "message": f"Correction agent started for {len(failed_checks)} failed check(s).",
    })

    try:
        async with agent.iter(prompt, deps=deps) as agent_run:
            async for node in _iter_with_turn_timeout(
                agent_run, CORRECTION_TURN_TIMEOUT,
            ):
                if Agent.is_call_tools_node(node):
                    async with node.stream(agent_run.ctx) as tool_stream:
                        async for event in tool_stream:
                            if isinstance(event, FunctionToolCallEvent):
                                args = event.part.args
                                if isinstance(args, str):
                                    try:
                                        parsed = json.loads(args)
                                    except (json.JSONDecodeError, TypeError):
                                        parsed = {}
                                elif isinstance(args, dict):
                                    parsed = args
                                else:
                                    parsed = {}
                                await _emit("tool_call", {
                                    "tool_name": event.part.tool_name,
                                    "tool_call_id": event.part.tool_call_id,
                                    "args": parsed,
                                })
                            elif isinstance(event, FunctionToolResultEvent):
                                content = event.result.content
                                summary = str(content)[:800] if content else ""
                                await _emit("tool_result", {
                                    "tool_name": event.result.tool_name,
                                    "tool_call_id": event.result.tool_call_id,
                                    "result_summary": summary,
                                    "duration_ms": 0,
                                })
        outcome["writes_performed"] = deps.writes_performed
        await _emit("complete", {
            "success": True,
            "writes_performed": deps.writes_performed,
        })
    except _asyncio.CancelledError:
        await _emit("complete", {"success": False, "error": "Cancelled by user"})
        outcome["error"] = "cancelled"
        raise
    except _asyncio.TimeoutError:
        # Per-turn wait_for fired — the model stalled mid-conversation.
        # Report writes that landed before the stall so the coordinator
        # can still re-run cross-checks against partial progress.
        msg = (
            f"Correction agent stalled past {CORRECTION_TURN_TIMEOUT}s "
            f"per-turn timeout after {deps.writes_performed} write(s)."
        )
        logger.warning(msg)
        outcome["error"] = msg
        outcome["writes_performed"] = deps.writes_performed
        await _emit("error", {"message": msg})
        await _emit("complete", {
            "success": False, "error": msg,
            "writes_performed": deps.writes_performed,
        })
    except Exception as e:  # noqa: BLE001 — never let correction blow up the run
        logger.exception("Correction agent run failed")
        outcome["error"] = str(e)
        await _emit("error", {"message": str(e)})
        await _emit("complete", {"success": False, "error": str(e)})
    return outcome


async def _run_notes_validator_pass(
    merged_workbook_path: str,
    pdf_path: str,
    notes_template_outputs: dict,
    filing_level: str,
    filing_standard: str,
    model,
    output_dir: str,
    event_queue,
    agent_id: str = NOTES_VALIDATOR_AGENT_ID,
) -> dict:
    """Run the notes post-validator once after the merge.

    Triggers only when BOTH Sheet 11 (ACC_POLICIES) and Sheet 12
    (LIST_OF_NOTES) ran in the run — otherwise there's nothing to
    cross-validate. Bounded to 1 iteration per PLAN D4.

    ``notes_template_outputs`` maps NotesTemplateType.value (or any string
    key used by the caller) → filled xlsx path so we can locate the
    per-template sidecar JSONs the writer left behind.

    Returns:
        {
          "invoked": bool,
          "writes_performed": int,
          "error": Optional[str],
          "context": dict (detector findings: duplicates, overlap),
        }
    """
    import asyncio as _asyncio
    from notes.coordinator import _iter_with_turn_timeout
    from notes.validator_agent import create_notes_validator_agent
    from notes.writer import payload_sidecar_path
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
    )
    from pydantic_ai import Agent

    outcome: dict = {
        "invoked": False, "writes_performed": 0, "error": None,
        "context": {},
    }

    # Trigger condition: both Sheet 11 and Sheet 12 must have run. Keys
    # may be NotesTemplateType enums (from the coordinator) or bare
    # strings (from tests) — normalise to the string form.
    keys_as_strings = {
        getattr(k, "value", k): v for k, v in notes_template_outputs.items()
    }
    if "ACC_POLICIES" not in keys_as_strings or "LIST_OF_NOTES" not in keys_as_strings:
        return outcome

    sidecar_paths = [
        str(payload_sidecar_path(p))
        for p in keys_as_strings.values()
        if p
    ]

    async def _emit(event_type: str, data: dict) -> None:
        if event_queue is None:
            return
        await event_queue.put({
            "event": event_type,
            "data": {**data, "agent_id": agent_id, "agent_role": agent_id},
        })

    try:
        agent, deps, context = create_notes_validator_agent(
            merged_workbook_path=merged_workbook_path,
            pdf_path=pdf_path,
            sidecar_paths=sidecar_paths,
            filing_level=filing_level,
            filing_standard=filing_standard,
            model=model,
            output_dir=output_dir,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Notes validator construction failed")
        outcome["error"] = f"agent construction failed: {e}"
        return outcome

    outcome["context"] = context

    # Short-circuit when there's genuinely nothing to do — skip invoking
    # the model entirely. Saves latency + tokens on the common case.
    #
    # Bug 4a — the short-circuit used to return silently, which left the
    # Notes Validator frontend tab stranded on "Waiting for the agent to
    # start…" with no status chip. Emit a status + success-complete pair
    # so the tab shows a human-readable skip reason and flips to green.
    # Both events carry agent_id via _emit so the frontend router can seed
    # the tab and route them into it.
    if not context["duplicates"] and not context["overlap_candidates"]:
        logger.info(
            "Notes validator skipped — no cross-sheet duplicate candidates."
        )
        await _emit("status", {
            "phase": "complete",
            "message": "No cross-sheet duplicates to review — skipped.",
        })
        await _emit("complete", {
            "success": True,
            "writes_performed": 0,
            "skipped": True,
        })
        return outcome

    outcome["invoked"] = True
    await _emit("status", {
        "phase": "started",
        "message": (
            f"Notes validator scanning {len(context['duplicates'])} "
            f"ref-based + {len(context['overlap_candidates'])} overlap "
            f"candidate(s)."
        ),
    })

    prompt = (
        "Resolve every candidate in your system prompt in one pass. "
        "Apply the 'Material Accounting Policies → Sheet 11; else Sheet 12' "
        "rule using the PDF as source of truth. Log each decision via "
        "flag_duplication."
    )

    try:
        async with agent.iter(prompt, deps=deps) as agent_run:
            async for node in _iter_with_turn_timeout(
                agent_run, NOTES_VALIDATOR_TURN_TIMEOUT,
            ):
                if Agent.is_call_tools_node(node):
                    async with node.stream(agent_run.ctx) as tool_stream:
                        async for event in tool_stream:
                            if isinstance(event, FunctionToolCallEvent):
                                args = event.part.args
                                if isinstance(args, str):
                                    try:
                                        parsed = json.loads(args)
                                    except (json.JSONDecodeError, TypeError):
                                        parsed = {}
                                elif isinstance(args, dict):
                                    parsed = args
                                else:
                                    parsed = {}
                                await _emit("tool_call", {
                                    "tool_name": event.part.tool_name,
                                    "tool_call_id": event.part.tool_call_id,
                                    "args": parsed,
                                })
                            elif isinstance(event, FunctionToolResultEvent):
                                content = event.result.content
                                summary = str(content)[:800] if content else ""
                                await _emit("tool_result", {
                                    "tool_name": event.result.tool_name,
                                    "tool_call_id": event.result.tool_call_id,
                                    "result_summary": summary,
                                    "duration_ms": 0,
                                })
        outcome["writes_performed"] = deps.writes_performed
        # Persist the agent's correction log next to the merged workbook
        # so operators have a durable audit trail of validator actions.
        try:
            log_path = Path(output_dir) / "notes_validator_log.json"
            log_path.write_text(
                json.dumps(deps.correction_log, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("Failed to write notes_validator_log.json", exc_info=True)

        await _emit("complete", {
            "success": True,
            "writes_performed": deps.writes_performed,
            "decisions_logged": len(deps.correction_log),
        })
    except _asyncio.CancelledError:
        await _emit("complete", {"success": False, "error": "Cancelled by user"})
        outcome["error"] = "cancelled"
        raise
    except _asyncio.TimeoutError:
        msg = (
            f"Notes validator stalled past {NOTES_VALIDATOR_TURN_TIMEOUT}s "
            f"per-turn timeout after {deps.writes_performed} write(s)."
        )
        logger.warning(msg)
        outcome["error"] = msg
        outcome["writes_performed"] = deps.writes_performed
        await _emit("error", {"message": msg})
        await _emit("complete", {
            "success": False, "error": msg,
            "writes_performed": deps.writes_performed,
        })
    except Exception as e:  # noqa: BLE001
        logger.exception("Notes validator run failed")
        outcome["error"] = str(e)
        await _emit("error", {"message": str(e)})
        await _emit("complete", {"success": False, "error": str(e)})
    return outcome


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

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Create / migrate the audit DB once at startup (peer-review #9).

    Previously every `_open_audit_conn` call re-ran `init_db`, which is
    idempotent but paid ~1ms of CREATE-IF-NOT-EXISTS churn on every
    request to the history and notes_cells endpoints. Running it once
    at startup keeps the schema-migration guarantee (v2/v3 migrations
    still land on first boot after deploy) without the per-request cost.
    """
    from db.schema import init_db
    init_db(AUDIT_DB_PATH)
    yield


app = FastAPI(title="XBRL Agent", version="0.3.0", lifespan=_lifespan)

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
    filing_level: Literal["company", "group"] = "company"
    # Filing standard axis, orthogonal to filing_level. Defaults to "mfrs"
    # so existing frontends (and persisted `run_config_json` blobs on
    # legacy rows) continue to resolve to the MFRS template tree without
    # changes. `"mpers"` routes through XBRL-template-MPERS/ and enables
    # the SoRE variant on SOCIE.
    filing_standard: Literal["mfrs", "mpers"] = "mfrs"
    # Notes templates to fill, as NotesTemplateType.value strings (e.g.
    # ["CORP_INFO", "ISSUED_CAPITAL"]). Empty = face-only run.
    notes_to_run: List[str] = []
    # Per-notes-template model overrides, keyed by NotesTemplateType.value.
    # Unspecified templates fall back to the run's default model. Mirrors
    # ``models`` for face statements.
    notes_models: Dict[str, str] = {}


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
    global_model = os.environ.get("TEST_MODEL", "openai.gpt-5.4")
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
        "model": os.environ.get("TEST_MODEL", "openai.gpt-5.4"),
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
        # Validate the submitted dict BEFORE merging anything. The peer
        # review flagged that an unvalidated payload could land arbitrary
        # data in .env (e.g. {"x": {"nested": [...]}} would be json-dumped
        # verbatim). Constrain keys to the known agent roles + notes
        # templates, and values to short strings matching an id in
        # config/models.json. Reject everything else with 400 so a
        # misconfigured client fails loudly instead of polluting the env
        # file the whole run pipeline reads from.
        raw_models = body["default_models"]
        if not isinstance(raw_models, dict):
            raise HTTPException(
                status_code=400,
                detail="default_models must be an object keyed by agent role.",
            )
        from notes_types import NotesTemplateType as _NT
        allowed_keys = set(_AGENT_ROLES) | {nt.value for nt in _NT}
        known_model_ids = {m["id"] for m in _load_available_models() if "id" in m}
        for key, value in raw_models.items():
            if key not in allowed_keys:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown default_models key: {key!r}. Allowed: {sorted(allowed_keys)}.",
                )
            if not isinstance(value, str) or not value:
                raise HTTPException(
                    status_code=400,
                    detail=f"default_models[{key!r}] must be a non-empty string model id.",
                )
            if len(value) > 128:
                raise HTTPException(
                    status_code=400,
                    detail=f"default_models[{key!r}] value too long (max 128 chars).",
                )
            # An unknown model id is a soft warning, not an error — the
            # config file may have been edited without a server restart,
            # or a new model may be in the registry file but not yet
            # loaded. The guard above already capped length + type.
            if known_model_ids and value not in known_model_ids:
                logger.warning(
                    "default_models[%s]=%s not in config/models.json", key, value,
                )

        # Merge incoming (now-validated) overrides with existing defaults
        load_dotenv(ENV_FILE, override=True)
        existing = _load_extended_settings()["default_models"]
        existing.update(raw_models)
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

    # Create session directory up front — we stream the upload straight to
    # disk so the full file never lives in memory (peer-review I13). A
    # running byte counter trips 413 as soon as the cap is exceeded.
    session_id = str(uuid.uuid4())
    session_dir = OUTPUT_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    target = session_dir / "uploaded.pdf"

    _CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB reads
    total_bytes = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_SIZE:
                    # Drain was already interrupted — discard the partial
                    # file so a half-written PDF doesn't linger.
                    out.close()
                    try:
                        target.unlink()
                        session_dir.rmdir()
                    except OSError:
                        pass
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Max size is {MAX_UPLOAD_SIZE // (1024*1024)}MB.",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:
        # Clean up partial files on any non-HTTP error.
        try:
            if target.exists():
                target.unlink()
            session_dir.rmdir()
        except OSError:
            pass
        raise

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
async def scout_pdf(session_id: str, request: Request):
    """Run the scout agent on an uploaded PDF and stream progress via SSE.

    Returns an SSE stream with status events during processing, then a
    final 'scout_complete' event containing the full infopack JSON.

    Accepts an optional JSON body ``{"scanned_pdf": bool}`` — when true,
    the scout's notes-inventory tool skips the PyMuPDF-regex fast path
    and runs the vision pass directly. Use this when the operator knows
    the uploaded PDF is image-only.
    """
    session_dir = OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found. Upload first.")

    # Body is optional — absent/empty/non-JSON all mean "default behaviour".
    # We don't fail the request on malformed JSON because the old callers
    # (Phase 7.1 UI) post no body at all.
    force_vision_inventory = False
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    if isinstance(body, dict) and body.get("scanned_pdf") is True:
        force_vision_inventory = True

    load_dotenv(ENV_FILE, override=True)
    api_key = _resolve_api_key()
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    global_model = os.environ.get("TEST_MODEL", "openai.gpt-5.4")

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
                force_vision_inventory=force_vision_inventory,
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


def _fail_run(db_conn: "Optional[Any]", run_id: Optional[int], msg: str):
    """Build the error + run_complete SSE events and mark the run row failed.

    Extracted to dedupe the five-line failure quartet
    (error → run_complete → mark_run_finished → terminal_status → return)
    that appeared in six input-validation paths inside
    ``run_multi_agent_stream`` (PR B.5).

    Returns a ``(events, new_terminal_status)`` tuple. ``events`` is a list
    of SSE events the caller must yield in order. ``new_terminal_status``
    is ``"failed"`` when the DB write succeeded, ``None`` otherwise — the
    caller updates its own ``terminal_status`` book-keeping accordingly so
    the try/finally block still retries the mark if we couldn't write it.

    Not a generator because the caller is an ``async def`` generator and
    ``yield from`` is a syntax error in that context — see
    https://docs.python.org/3/reference/expressions.html#yieldexpr.

    Usage::

        events, new_status = _fail_run(db_conn, run_id, msg)
        for ev in events:
            yield ev
        terminal_status = new_status or terminal_status
        return
    """
    events = [
        {"event": "error", "data": {"message": msg}},
        {"event": "run_complete", "data": {"success": False, "message": msg}},
    ]
    new_status = "failed" if _safe_mark_finished(db_conn, run_id, "failed") else None
    return events, new_status


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
    from notes.coordinator import (
        NotesAgentResult,
        NotesRunConfig,
        run_notes_extraction,
        NotesCoordinatorResult,
    )
    from notes_types import NotesTemplateType
    from statement_types import StatementType, get_variant, variants_for
    from workbook_merger import merge as merge_workbooks
    from cross_checks.framework import run_all as run_cross_checks, DEFAULT_TOLERANCE_RM
    from cross_checks.notes_consistency import check_notes_consistency
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
                events, new_status = _fail_run(db_conn, run_id, f"Unknown statement type: {s}")
                for ev in events:
                    yield ev
                terminal_status = new_status or terminal_status
                return

        # Parse notes templates up-front (before pre-creating run_agents rows),
        # so DB rows and live events line up one-to-one. LIST_OF_NOTES is
        # rejected until Phase C (Sheet-12 sub-coordinator + row-112 unmatched
        # logic) lands — without it the generic agent would run with a
        # placeholder prompt.
        notes_to_run: Set[NotesTemplateType] = set()
        for n in run_config.notes_to_run:
            try:
                parsed_note = NotesTemplateType(n)
            except ValueError:
                events, new_status = _fail_run(db_conn, run_id, f"Unknown notes template: {n}")
                for ev in events:
                    yield ev
                terminal_status = new_status or terminal_status
                return
            if parsed_note not in _PUBLIC_NOTES_TEMPLATES:
                events, new_status = _fail_run(db_conn, run_id, f"Notes template not available yet: {n}")
                for ev in events:
                    yield ev
                terminal_status = new_status or terminal_status
                return
            notes_to_run.add(parsed_note)

        # Build variant map — fall back to first registered variant if not specified
        for stmt in statements_to_run:
            if stmt.value in run_config.variants:
                variants[stmt] = run_config.variants[stmt.value]
            # else: coordinator will resolve from infopack / registry default.

        # Reject variant/standard mismatches BEFORE launching the coordinator.
        # Without this, the run would progress through row creation, model
        # construction, and task launch before a FileNotFoundError bubbles up
        # mid-extraction, leaving a confusing run_agents trail. Caught here,
        # the user sees a single crisp error naming the offending variant and
        # the standard in play (e.g. "SoRE is not available on MFRS — ...").
        for stmt, variant_name in variants.items():
            try:
                v = get_variant(stmt, variant_name)
            except KeyError as e:
                events, new_status = _fail_run(
                    db_conn, run_id, f"Unknown variant for {stmt.value}: {e}",
                )
                for ev in events:
                    yield ev
                terminal_status = new_status or terminal_status
                return
            if run_config.filing_standard not in v.applies_to_standard:
                allowed = (
                    ", ".join(sorted(v.applies_to_standard)).upper() or "(none)"
                )
                events, new_status = _fail_run(
                    db_conn, run_id,
                    f"{stmt.value}/{variant_name} is not available on "
                    f"{run_config.filing_standard.upper()} filings — "
                    f"only {allowed}.",
                )
                for ev in events:
                    yield ev
                terminal_status = new_status or terminal_status
                return

        # Build model overrides — resolve each through _create_proxy_model so
        # per-agent overrides use the same proxy/direct wiring as the default.
        # Wrap in try/except so a broken override key also produces a clean
        # failed-row rather than bubbling out of the generator.
        notes_models: Dict[NotesTemplateType, Any] = {}
        try:
            for stmt in statements_to_run:
                if stmt.value in run_config.models:
                    override_name = run_config.models[stmt.value]
                    models[stmt] = _create_proxy_model(override_name, proxy_url, api_key)
            # Same treatment for per-notes-template overrides. Silently ignore
            # entries whose key isn't a known NotesTemplateType — they won't
            # match any requested template and we don't want a typo in the
            # frontend payload to fail the whole run.
            for nt_key, nt_model_name in run_config.notes_models.items():
                try:
                    nt_parsed = NotesTemplateType(nt_key)
                except ValueError:
                    continue
                notes_models[nt_parsed] = _create_proxy_model(
                    nt_model_name, proxy_url, api_key,
                )
        except Exception as e:
            logger.exception(
                "Override model construction failed for session %s", session_id,
            )
            events, new_status = _fail_run(db_conn, run_id, f"Model override failed: {e}")
            for ev in events:
                yield ev
            terminal_status = new_status or terminal_status
            return

        # Resolve infopack
        infopack = None
        if run_config.infopack:
            from scout.infopack import Infopack
            try:
                # from_json expects a JSON string; request body gives us a dict
                infopack = Infopack.from_json(json.dumps(run_config.infopack))
            except Exception as e:
                events, new_status = _fail_run(db_conn, run_id, f"Invalid infopack: {e}")
                for ev in events:
                    yield ev
                terminal_status = new_status or terminal_status
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
            events, new_status = _fail_run(db_conn, run_id, f"Model setup failed: {e}")
            for ev in events:
                yield ev
            terminal_status = new_status or terminal_status
            return

        config = RunConfig(
            pdf_path=str(session_dir / "uploaded.pdf"),
            output_dir=output_dir,
            model=model,
            statements_to_run=statements_to_run,
            variants=variants,
            models=models,
            filing_level=run_config.filing_level,
            filing_standard=run_config.filing_standard,
        )

        yield {"event": "status", "data": {
            "phase": "starting",
            "message": f"Starting extraction for {len(statements_to_run)} statements...",
            # Surface the new run_id so clients that kicked off a
            # rerun / regenerate (which creates a fresh run row) can
            # navigate to the new run once it finishes, instead of
            # sitting on the stale id they POSTed to. Matches the
            # run_complete event below which also carries it now.
            "run_id": run_id,
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
        # Same idea for notes — keyed by NotesTemplateType so the post-run
        # loop can find the row to finalize.
        run_agent_ids_by_notes: Dict[NotesTemplateType, int] = {}
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
                # Notes templates — statement_type is prefixed "NOTES_" so the
                # column is unambiguous vs. face statements, and the agent_id
                # key matches notes/coordinator.py's f"notes:{template.value}"
                # emission (lowercased to match persist_event's lookup).
                for nt in sorted(notes_to_run, key=lambda n: n.value):
                    # Resolve the per-template model the coordinator will
                    # actually use so History shows the right model id for
                    # each notes agent (falls back to the run-wide default).
                    nt_model = notes_models.get(nt, config.model)
                    rai = repo.create_run_agent(
                        db_conn, run_id,
                        statement_type=f"NOTES_{nt.value}",
                        variant=None,
                        model=_model_id(nt_model),
                    )
                    run_agent_ids_by_agent_id[f"notes:{nt.value}".lower()] = rai
                    run_agent_ids_by_notes[nt] = rai
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

        # Launch coordinator as a background task so we can drain events while agents run.
        # push_sentinel=False: we're multiplexing face + notes into one queue;
        # the orchestrator below pushes a single sentinel after BOTH complete.
        coordinator_task = asyncio.create_task(
            coordinator_run(
                config,
                infopack=infopack,
                event_queue=event_queue,
                session_id=session_id,
                push_sentinel=False,
            )
        )

        # Derive a union of note-bearing pages across every face statement
        # scout scored. Gives the notes agents a tight starting viewport on
        # scanned PDFs where scout's deterministic notes_inventory is empty
        # (observed in real FINCO runs: NOTES_ACC_POLICIES rendered 33 pages
        # for 15 output rows, consuming the majority of total run time).
        # If scout was off or failed, hints stay empty and the notes agents
        # fall back to their previous any-page exploration behaviour.
        notes_page_hints: List[int] = []
        if infopack is not None:
            try:
                notes_page_hints = infopack.notes_page_hints()
            except Exception:  # noqa: BLE001 — advisory only, never block the run
                logger.warning(
                    "Failed to derive notes_page_hints from infopack",
                    extra={"session_id": session_id},
                    exc_info=True,
                )

        notes_config = NotesRunConfig(
            pdf_path=str(session_dir / "uploaded.pdf"),
            output_dir=output_dir,
            model=model,
            notes_to_run=notes_to_run,
            filing_level=run_config.filing_level,
            filing_standard=run_config.filing_standard,
            models=notes_models,
            page_hints=notes_page_hints,
            # Step 6 of the notes rich-editor plan: hand the audit run_id
            # + DB path down so the coordinator persists each agent's
            # per-cell HTML to `notes_cells` on success. Skipped cleanly
            # if the row-creation above failed (run_id is None).
            run_id=run_id,
            audit_db_path=str(AUDIT_DB_PATH),
        )
        notes_task = asyncio.create_task(
            run_notes_extraction(
                notes_config,
                infopack=infopack,
                event_queue=event_queue,
                session_id=session_id,
            )
        )

        # Fan-in sentinel: push None onto the queue only after BOTH coords
        # have finished so the drain loop doesn't exit prematurely.
        async def _push_sentinel_when_done() -> None:
            try:
                await asyncio.gather(coordinator_task, notes_task, return_exceptions=True)
            finally:
                await event_queue.put(None)

        sentinel_task = asyncio.create_task(_push_sentinel_when_done())

        # Drain events from the queue as they arrive from concurrent agents.
        #
        # Client-disconnect contract (Option B, April 2026): if the SSE
        # client drops mid-stream we do NOT kill the coordinator. The
        # agents may have already written their workbooks (the real-world
        # trigger was a rerun where save_result had completed on disk but
        # the post-save LLM wrap-up call stalled long enough for the
        # browser to close the stream). Throwing away that work — and
        # leaving the runs row as 'aborted' with run_agents frozen at
        # 'running' — was the original bug.
        #
        # Instead we:
        #   1. Swallow GeneratorExit / CancelledError at the yield point,
        #      flip ``client_connected`` to False, and keep draining so the
        #      coordinator isn't blocked pushing into a full queue.
        #   2. Fall through to the post-pipeline (merge + cross-checks +
        #      DB finalization) as if nothing happened.
        #   3. Skip the trailing ``yield`` of run_complete — once a
        #      generator has caught GeneratorExit it can never yield again
        #      without raising RuntimeError, and there's no one listening.
        client_connected = True
        try:
            while True:
                event = await event_queue.get()
                if event is None:
                    # Sentinel: all agents finished
                    break
                persist_event(event)
                if client_connected:
                    try:
                        yield event
                    except (asyncio.CancelledError, GeneratorExit):
                        client_connected = False
                        logger.info(
                            "Client disconnected; continuing post-pipeline",
                            extra={"session_id": session_id},
                        )
        except Exception as e:
            # Drain failure is unrecoverable: the queue contract is broken
            # and we can't safely merge partial results. Cancel the
            # coordinator, mark the run failed, and bail — previously the
            # code fell through to merge, which could corrupt output.
            logger.exception("Event queue drain failed", extra={"session_id": session_id})
            coordinator_task.cancel()
            notes_task.cancel()
            if _safe_mark_finished(db_conn, run_id, "failed"):
                terminal_status = "failed"
            if client_connected:
                yield {"event": "error", "data": {"message": f"Stream error: {e}"}}
            return

        # Await the coordinator task to get CoordinatorResult for post-processing
        try:
            coordinator_result = await coordinator_task
        except asyncio.CancelledError:
            logger.info("Coordinator cancelled", extra={"session_id": session_id})
            if _safe_mark_finished(db_conn, run_id, "aborted"):
                terminal_status = "aborted"
            if client_connected:
                yield {"event": "error", "data": {"message": "Run cancelled"}}
            return
        except Exception as e:
            logger.exception("Coordinator failed", extra={"session_id": session_id})
            if _safe_mark_finished(db_conn, run_id, "failed"):
                terminal_status = "failed"
            if client_connected:
                yield {"event": "error", "data": {"message": f"Coordinator error: {e}"}}
            return

        # Peer-review C1: the main drain loop has exited (it stopped when
        # the fan-in sentinel arrived). The post-pipeline stages below —
        # correction agent + notes post-validator — still push events into
        # `event_queue`. Without this helper those events would be
        # stranded: no DB persistence, no SSE yields. The helper drains
        # the queue while a helper task runs, persisting and yielding
        # each event through the outer generator so the frontend + DB
        # see live updates from the pseudo-agents.
        async def _drain_while_running(task: asyncio.Task):
            """Yield events from the queue while ``task`` runs, then flush
            anything left behind after it completes. Swallows the sentinel
            value (None) so a stale sentinel doesn't break the outer loop."""
            while not task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.3)
                except asyncio.TimeoutError:
                    continue
                if event is None:
                    continue
                yield event
            # Final non-blocking sweep — agent events enqueued in the last
            # ms before the task completed might still be sitting here.
            while True:
                try:
                    event = event_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if event is None:
                    continue
                yield event

        # Notes coordinator: per-agent failures are already captured in each
        # NotesAgentResult.status and don't raise here. If the coordinator
        # ITSELF raises (setup bug, unexpected asyncio error, etc.) we must
        # NOT silently drop the failure — otherwise run_complete.success
        # flips to True even though the user asked for notes and got none.
        # Synthesize a failed NotesCoordinatorResult with one entry per
        # requested template so overall-status logic and the finalization
        # loop above both see the failure.
        notes_result: Optional[NotesCoordinatorResult] = None
        try:
            notes_result = await notes_task
        except asyncio.CancelledError:
            logger.info("Notes coordinator cancelled", extra={"session_id": session_id})
            if notes_to_run:
                notes_result = NotesCoordinatorResult(agent_results=[
                    NotesAgentResult(
                        template_type=nt,
                        status="cancelled",
                        error="Cancelled by user",
                    ) for nt in sorted(notes_to_run, key=lambda n: n.value)
                ])
        except Exception as e:
            logger.exception("Notes coordinator failed", extra={"session_id": session_id})
            if notes_to_run:
                notes_result = NotesCoordinatorResult(agent_results=[
                    NotesAgentResult(
                        template_type=nt,
                        status="failed",
                        error=f"Notes coordinator crashed: {e}",
                    ) for nt in sorted(notes_to_run, key=lambda n: n.value)
                ])

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

        # Same pattern for notes workbooks — pick up prior partial runs + this run's output.
        all_notes_workbook_paths: Dict[NotesTemplateType, str] = {}
        for nt in NotesTemplateType:
            wb_path = session_dir / f"NOTES_{nt.value}_filled.xlsx"
            if wb_path.exists():
                all_notes_workbook_paths[nt] = str(wb_path)
        if notes_result is not None:
            all_notes_workbook_paths.update(notes_result.workbook_paths)

        # Merge workbooks (Phase 7.4). Notes sheets land after face sheets.
        merge_result = merge_workbooks(
            all_workbook_paths,
            merged_path,
            notes_workbook_paths=all_notes_workbook_paths,
        )

        # Record the merged workbook path on the runs row. History's download
        # endpoint reads this as the single source of truth — never derived
        # from session_id. Peer-review C6: we deliberately DO NOT commit
        # here; the write stays in the pending transaction and is flushed
        # alongside per-agent state (line below) so there is no moment
        # between commits where `merged_workbook_path` is durable but the
        # final status is not yet written. Hard-kill (SIGKILL/OOM) between
        # the next commit and `mark_run_finished` can still leave
        # `status='running'` — that corner needs startup recovery, which is
        # explicitly out of scope for this round.
        if merge_result.success and db_conn is not None and run_id is not None:
            try:
                repo.mark_run_merged(db_conn, run_id, merged_path)
            except Exception:
                logger.warning(
                    "Failed to mark run_merged on run %s", run_id, exc_info=True,
                )

        # Run cross-checks (Phase 5 wiring). See `_build_default_cross_checks`
        # at module scope for the canonical registry the MPERS wiring tests
        # pin against.
        all_checks = _build_default_cross_checks()
        check_config = {
            "statements_to_run": statements_to_run,
            "variants": {stmt: v for stmt, v in variants.items()},
            "filing_level": run_config.filing_level,
            "filing_standard": run_config.filing_standard,
        }
        tolerance = float(os.environ.get("XBRL_TOLERANCE_RM", "1.0"))
        cross_check_results = run_cross_checks(
            all_checks, all_workbook_paths, check_config,
            tolerance=tolerance,
        )

        # Phase 6.1: advisory cross-sheet notes-consistency check. Warns
        # when Sheet 11 and Sheet 12 disagree on the PDF page for the same
        # topic (usually one side cited the printed folio instead of the
        # PDF page). Advisory only — never fails the merge; returns [] on
        # any read error so this block can't break a run.
        #
        # We fold warnings into ``cross_check_results`` with status
        # ``"warning"`` so they ride the same persistence + SSE + UI path
        # as real cross-checks. Deliberately SKIPPED when the merge
        # failed — a missing workbook means there's nothing to compare.
        if merge_result.success:
            try:
                consistency_warnings = check_notes_consistency(merged_path)
            except Exception:
                # The check has its own broad except but defence-in-depth
                # is cheap here: never let an advisory check fail a run.
                logger.warning(
                    "notes-consistency check raised unexpectedly on run %s",
                    run_id, exc_info=True,
                )
                consistency_warnings = []
            from cross_checks.framework import CrossCheckResult
            for w in consistency_warnings:
                cross_check_results.append(CrossCheckResult(
                    name=f"Notes consistency: {w.sheet_11_label} ↔ {w.sheet_12_label}",
                    status="warning",
                    message=w.message,
                ))

        # Peer-review C1: track pseudo-agent outcomes so the persistence
        # block below can finish_run_agent them. None means the helper was
        # never invoked (short-circuited because there was nothing to do)
        # — its row was never created and we shouldn't finalize it.
        correction_outcome: Optional[dict] = None
        validator_outcome: Optional[dict] = None
        correction_run_agent_id: Optional[int] = None
        validator_run_agent_id: Optional[int] = None

        # Phase 3: if any hard cross-check failed, spawn the correction
        # agent once. It edits the merged workbook in place; on completion
        # we re-run the full cross-check registry so the Validator tab
        # shows the post-correction state. Bounded to 1 iteration per
        # PLAN D4 — unresolved failures after this pass surface for human
        # review, they do NOT retry.
        if merge_result.success:
            hard_failures = [cr for cr in cross_check_results if cr.status == "failed"]
            if hard_failures:
                # Create + register the CORRECTION run_agent row lazily —
                # only when we actually launch the agent — so runs without
                # failures don't churn out a "skipped" audit row and the
                # counts match the number of real agents that did work.
                if db_conn is not None and run_id is not None:
                    try:
                        correction_run_agent_id = repo.create_run_agent(
                            db_conn, run_id,
                            statement_type=CORRECTION_AGENT_ID,
                            variant=None,
                            model=_model_id(config.model),
                        )
                        run_agent_ids_by_agent_id[
                            CORRECTION_AGENT_ID.lower()
                        ] = correction_run_agent_id
                        db_conn.commit()
                    except Exception:
                        logger.warning(
                            "Failed to pre-create correction run_agent row",
                            exc_info=True,
                        )
                correction_task = asyncio.create_task(_run_correction_pass(
                    failed_checks=hard_failures,
                    merged_workbook_path=merged_path,
                    pdf_path=str(session_dir / "uploaded.pdf"),
                    infopack=infopack,
                    filing_level=run_config.filing_level,
                    filing_standard=run_config.filing_standard,
                    model=model,
                    output_dir=output_dir,
                    event_queue=event_queue,
                    statements_to_run=set(statements_to_run),
                    variants={stmt: v for stmt, v in variants.items()},
                ))
                async for event in _drain_while_running(correction_task):
                    persist_event(event)
                    if client_connected:
                        try:
                            yield event
                        except (asyncio.CancelledError, GeneratorExit):
                            client_connected = False
                correction_outcome = await correction_task
                if correction_outcome.get("writes_performed", 0) > 0:
                    # Re-run cross-checks against the edited workbook so
                    # the UI + DB see the post-correction state.
                    #
                    # Peer-review C2: point the re-run at merged_path —
                    # the correction agent writes to the merged workbook,
                    # not the per-statement {stmt}_filled.xlsx files.
                    # Feeding all_workbook_paths back in would have the
                    # validator tab parrot the pre-correction failure
                    # status even though filled.xlsx is now correct.
                    # This matches the pattern the correction agent's
                    # own `run_cross_checks` tool uses internally.
                    merged_paths_by_stmt = {
                        stmt: merged_path for stmt in all_workbook_paths
                    }
                    cross_check_results = run_cross_checks(
                        all_checks, merged_paths_by_stmt, check_config,
                        tolerance=tolerance,
                    )
                    if merge_result.success:
                        try:
                            consistency_warnings = check_notes_consistency(merged_path)
                        except Exception:
                            consistency_warnings = []
                        from cross_checks.framework import CrossCheckResult
                        for w in consistency_warnings:
                            cross_check_results.append(CrossCheckResult(
                                name=(
                                    f"Notes consistency: "
                                    f"{w.sheet_11_label} ↔ {w.sheet_12_label}"
                                ),
                                status="warning",
                                message=w.message,
                            ))

        # Phase 5.5: notes post-validator. Runs only when BOTH Sheet 11
        # (ACC_POLICIES) and Sheet 12 (LIST_OF_NOTES) were produced in
        # this run. Operates on the merged workbook (so cross-sheet
        # visibility is real), after cross-checks + any Phase 3
        # correction pass, so it sees the final state the user will
        # download. Bounded to 1 iteration per PLAN D4.
        if merge_result.success and notes_result is not None:
            notes_outputs = {
                r.template_type: r.workbook_path
                for r in notes_result.agent_results
                if r.workbook_path
            }
            have_both_sheets = (
                NotesTemplateType.ACC_POLICIES in notes_outputs
                and NotesTemplateType.LIST_OF_NOTES in notes_outputs
            )
            if have_both_sheets:
                # Lazy pseudo-agent row — created only when the validator
                # will actually run. Without this gate, short-circuit
                # cases (no sheet 11/12) would still mint an audit row
                # and break run_agent counts in tests that expect only
                # the real extraction agents.
                if db_conn is not None and run_id is not None:
                    try:
                        validator_run_agent_id = repo.create_run_agent(
                            db_conn, run_id,
                            statement_type=NOTES_VALIDATOR_AGENT_ID,
                            variant=None,
                            model=_model_id(config.model),
                        )
                        run_agent_ids_by_agent_id[
                            NOTES_VALIDATOR_AGENT_ID.lower()
                        ] = validator_run_agent_id
                        db_conn.commit()
                    except Exception:
                        logger.warning(
                            "Failed to pre-create notes-validator row",
                            exc_info=True,
                        )
                validator_task = asyncio.create_task(_run_notes_validator_pass(
                    merged_workbook_path=merged_path,
                    pdf_path=str(session_dir / "uploaded.pdf"),
                    notes_template_outputs=notes_outputs,
                    filing_level=run_config.filing_level,
                    filing_standard=run_config.filing_standard,
                    model=model,
                    output_dir=output_dir,
                    event_queue=event_queue,
                ))
                async for event in _drain_while_running(validator_task):
                    persist_event(event)
                    if client_connected:
                        try:
                            yield event
                        except (asyncio.CancelledError, GeneratorExit):
                            client_connected = False
                validator_outcome = await validator_task

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

                # Finalize notes agent rows so History can show their status,
                # workbook path, and model for this run. Mirrors the face
                # loop above. `notes_result` may be None if the coordinator
                # itself crashed — the overall-status block below synthesizes
                # a failed result in that case, so we handle None defensively.
                notes_agent_results = (
                    notes_result.agent_results if notes_result is not None else []
                )
                for notes_agent_result in notes_agent_results:
                    run_agent_id = run_agent_ids_by_notes.get(notes_agent_result.template_type)
                    if run_agent_id is None:
                        # Pre-create didn't happen (DB was unhappy earlier).
                        run_agent_id = repo.create_run_agent(
                            db_conn, run_id,
                            statement_type=f"NOTES_{notes_agent_result.template_type.value}",
                            variant=None,
                            model=_model_id(config.model),
                        )
                    repo.finish_run_agent(
                        db_conn, run_agent_id,
                        status=notes_agent_result.status,
                        workbook_path=notes_agent_result.workbook_path,
                    )

                # Peer-review C1: finalise pseudo-agent rows so History
                # doesn't show them stuck at the initial "running" status.
                # `finish_run_agent` is safe to call even if events were
                # persisted live — it just updates the terminal status.
                if correction_run_agent_id is not None:
                    try:
                        if correction_outcome is None:
                            status = "pending"
                        elif correction_outcome.get("error"):
                            status = "failed"
                        else:
                            status = "completed"
                        repo.finish_run_agent(
                            db_conn, correction_run_agent_id,
                            status=status,
                            workbook_path=None,
                        )
                    except Exception:
                        logger.warning(
                            "Failed to finalize CORRECTION run_agent row",
                            exc_info=True,
                        )
                if validator_run_agent_id is not None:
                    try:
                        if validator_outcome is None:
                            status = "pending"
                        elif validator_outcome.get("error"):
                            status = "failed"
                        else:
                            status = "completed"
                        repo.finish_run_agent(
                            db_conn, validator_run_agent_id,
                            status=status,
                            workbook_path=None,
                        )
                    except Exception:
                        logger.warning(
                            "Failed to finalize NOTES_VALIDATOR run_agent row",
                            exc_info=True,
                        )

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
        notes_all_succeeded = notes_result is None or notes_result.all_succeeded
        all_agents_ok = coordinator_result.all_succeeded and notes_all_succeeded
        if all_agents_ok and merge_result.success and not any_check_failed:
            overall_status = "completed"
        elif all_agents_ok and not merge_result.success:
            overall_status = "completed_with_errors"
        elif all_agents_ok and any_check_failed:
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
        cross_checks_partial = False

        # Final run_complete event — success requires agents + merge + cross-checks all passing
        notes_completed = (
            [r.template_type.value for r in notes_result.agent_results if r.status == "succeeded"]
            if notes_result is not None else []
        )
        notes_failed = (
            [r.template_type.value for r in notes_result.agent_results if r.status == "failed"]
            if notes_result is not None else []
        )
        # If the client disconnected mid-stream we cannot yield anymore —
        # a generator that caught GeneratorExit is allowed to run to
        # completion, but any further ``yield`` raises RuntimeError. The
        # run is already fully persisted in the DB (History will show it
        # correctly on reload); the client just won't see this event.
        if client_connected:
            yield {"event": "run_complete", "data": {
                "success": all_agents_ok and merge_result.success and not any_check_failed,
                "merged_workbook": merged_path if merge_result.success else None,
                "merge_errors": merge_result.errors,
                "cross_checks": checks_data,
                "cross_checks_partial": cross_checks_partial,
                "statements_completed": [r.statement_type.value for r in coordinator_result.agent_results
                                          if r.status == "succeeded"],
                "statements_failed": [r.statement_type.value for r in coordinator_result.agent_results
                                       if r.status == "failed"],
                "notes_completed": notes_completed,
                "notes_failed": notes_failed,
                # Peer-review follow-up for regenerate-flow: surface the
                # run_id here (not just in `status: starting`) so a client
                # that connected mid-stream and missed the starting event
                # can still pick up the new run id to navigate to.
                "run_id": run_id,
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
        # Session-wide task_registry cleanup used to live in coordinator.py's
        # finally block, but that erased notes tasks mid-flight whenever face
        # finished before notes. Now the outer orchestrator owns it — one
        # remove_session call covers scout + face + notes after every run.
        try:
            import task_registry
            task_registry.remove_session(session_id)
        except Exception:
            logger.warning(
                "task_registry.remove_session failed for %s", session_id,
                exc_info=True,
            )
        # Peer-review C3: page_cache is a process-global LRU and was
        # previously only reset by tests. On a long-running server it
        # accumulates renders across runs and pays LRU eviction churn
        # at the cap. Bound memory to one in-flight run by clearing
        # at the same teardown point as task_registry. Tests reset
        # the cache themselves so this doesn't disturb them.
        try:
            from tools import page_cache
            page_cache.reset()
        except Exception:
            logger.warning(
                "page_cache.reset failed for %s", session_id,
                exc_info=True,
            )
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

    # Reserve the session BEFORE returning StreamingResponse. If we only
    # reserved inside the generator (as we did pre-fix for I4), two
    # concurrent requests could both pass the `in active_runs` check
    # before either generator started — allowing parallel extractions
    # against the same session directory. The async generator's finally
    # releases the reservation on every exit path (normal completion,
    # exception, client disconnect, garbage-collection close), so the
    # I4 leak on never-started streams is still covered.
    if session_id in active_runs:
        raise HTTPException(status_code=409, detail="Extraction already running for this session.")
    active_runs.add(session_id)

    try:
        load_dotenv(ENV_FILE, override=True)
        api_key = _resolve_api_key()
        proxy_url = os.environ.get("LLM_PROXY_URL", "")
        model_name = os.environ.get("TEST_MODEL", "openai.gpt-5.4")

        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="GEMINI_API_KEY (Mac) or GOOGLE_API_KEY (Windows proxy) must be set. Check Settings.",
            )

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
    except Exception:
        # Anything that prevents the StreamingResponse from being returned
        # (e.g. missing API key HTTPException) must release the reservation
        # we just acquired, or the session would stay locked until restart.
        active_runs.discard(session_id)
        raise


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

@app.post("/api/runs/{run_id}/rerun-notes")
async def rerun_notes(run_id: int):
    """Regenerate the notes sheets for a completed run.

    Peer-review [HIGH] #1: before this endpoint existed, the
    Regenerate-notes button on the History-page run detail redirected
    to `/?session=<id>#notes` — a URL no code consumed. Users clicking
    it landed on the Extract page with no Rerun affordance (that button
    only shows for failed/cancelled agents). This endpoint is the real
    target: it reads the run's session + config from the DB, builds a
    notes-only `RunConfigRequest` server-side, and delegates to the same
    `run_multi_agent_stream` the per-agent rerun uses.

    Keeping the config build server-side (instead of expecting the
    frontend to reconstruct a RunConfigRequest from the run detail
    payload) means the Regenerate flow stays resilient to new
    RunConfigRequest fields landing in the future — only this endpoint
    needs to learn about them.
    """
    # Look up the run row — we need its session_id (for active-runs
    # locking + output dir) and its stored `run_config_json`.
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(AUDIT_DB_PATH))
    try:
        from db.repository import fetch_run as _fetch_run
        run = _fetch_run(conn, run_id)
    finally:
        conn.close()

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found.")

    config = run.config or {}
    notes_to_run = config.get("notes_to_run") or []
    if not notes_to_run:
        raise HTTPException(
            status_code=400,
            detail=(
                "This run has no notes templates in its config — nothing "
                "to regenerate. Run the notes pipeline on a fresh session "
                "instead."
            ),
        )

    session_id = run.session_id
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail=f"Run {run_id} has no session_id — legacy row can't be rerun.",
        )

    if session_id in active_runs:
        raise HTTPException(
            status_code=409,
            detail="Extraction still running for this session. Wait for it to finish before regenerating.",
        )

    session_dir = OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"
    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail="PDF not found for this session — cannot regenerate.",
        )

    # Build a notes-only RunConfigRequest from the stored config. Clear
    # `statements` so only the notes coordinator runs; preserve
    # filing_level, filing_standard, infopack, use_scout, and any
    # per-template model overrides so the regenerated notes match the
    # original run's environment.
    try:
        regen_config = RunConfigRequest(
            statements=[],
            variants={},
            models={},
            infopack=config.get("infopack"),
            use_scout=False,  # no new scout pass — reuse stored infopack
            filing_level=config.get("filing_level", "company"),
            filing_standard=config.get("filing_standard", "mfrs"),
            notes_to_run=list(notes_to_run),
            notes_models=config.get("notes_models") or {},
        )
    except Exception as e:
        # Malformed stored config — surface rather than crash mid-stream.
        raise HTTPException(
            status_code=400,
            detail=f"Stored run config is malformed: {e}",
        )

    load_dotenv(ENV_FILE, override=True)
    api_key = _resolve_api_key()
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    model_name = os.environ.get("TEST_MODEL", "openai.gpt-5.4")

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="API key not set. Check Settings.",
        )

    active_runs.add(session_id)

    async def event_stream():
        try:
            async for evt in run_multi_agent_stream(
                session_id=session_id,
                session_dir=session_dir,
                run_config=regen_config,
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
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/rerun/{session_id}")
async def rerun_agent(session_id: str, body: RunConfigRequest):
    """Re-run extraction for a single agent within an existing session.

    Accepts either exactly one face statement OR exactly one notes template,
    never both — rerun is a targeted retry for one failed/cancelled agent.
    Reuses the same output directory so the new workbook overwrites the old
    one. After the agent finishes, merge + cross-checks run against all
    workbooks in the session (both old successful ones and the new one).
    """
    n_stmts = len(body.statements)
    n_notes = len(body.notes_to_run)
    if n_stmts + n_notes != 1:
        raise HTTPException(
            status_code=400,
            detail="Rerun expects exactly one statement or one notes template.",
        )

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
    model_name = os.environ.get("TEST_MODEL", "openai.gpt-5.4")

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

    model_name = body.get("model") or os.environ.get("TEST_MODEL", "openai.gpt-5.4")
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
    except Exception:
        # LLM SDK exceptions frequently embed the Authorization header or
        # bearer token in str(e). Log the full trace server-side only; the
        # HTTP response stays generic so we never leak credentials to callers.
        logger.exception("Connection test failed", extra={"model": model_name})
        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "message": "Connection test failed. See server logs for details.",
            },
        )


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
    `db_session`).

    The schema is initialised once at FastAPI startup
    (`_init_audit_db_once`). Peer-review I-5: callers that bypass the
    startup hook (ad-hoc CLI scripts importing `server`, some test
    harnesses) would otherwise hit `no such table` errors on the first
    query. We self-heal by running `init_db` if the `schema_version`
    table is missing — cheap (one PRAGMA + one SELECT) in the hot path,
    and sqlite's `CREATE TABLE IF NOT EXISTS` makes `init_db` itself
    idempotent, so the extra call is a no-op once the schema is set up.
    """
    import sqlite3
    conn = sqlite3.connect(str(AUDIT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    # Defensive init for non-lifespan callers. The `sqlite_master` probe
    # is cheap and the `init_db` path short-circuits via `IF NOT EXISTS`
    # when the schema is already present, so the common case (FastAPI
    # has already run lifespan) pays ~one extra query.
    schema_present = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='schema_version' LIMIT 1"
    ).fetchone()
    if schema_present is None:
        conn.close()
        from db.schema import init_db
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
        "filing_level": summary.filing_level,
        "filing_standard": summary.filing_standard,
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
        "filing_level": (run.config or {}).get("filing_level", "company"),
        "filing_standard": (run.config or {}).get("filing_standard", "mfrs"),
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


# ---------------------------------------------------------------------------
# Step 8 (docs/PLAN-NOTES-RICH-EDITOR.md): notes_cells GET/PATCH contract.
#
# The post-run editor reads rich HTML payloads per cell via GET (grouped by
# sheet) and saves edits via PATCH. The wire contract is the one asserted
# in tests/test_server_notes_cells_api.py — both endpoints go through
# _open_audit_conn so the same DB/WAL pragmas apply as the rest of the
# audit path.
# ---------------------------------------------------------------------------


@app.get("/api/runs/{run_id}/notes_cells")
async def list_notes_cells_endpoint(run_id: int):
    """Return every notes cell for ``run_id`` grouped by sheet.

    Shape:
        {
            "sheets": [
                {"sheet": "Notes-CI", "rows": [
                    {"row": 4, "label": ..., "html": ..., "evidence": ...,
                     "source_pages": [...], "updated_at": "..."},
                ]},
                ...
            ]
        }

    404 if the run does not exist (distinguishable from "run exists but
    has no notes yet" — the latter returns an empty sheets array).
    """
    from db import repository as repo
    conn = _open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        cells = repo.list_notes_cells_for_run(conn, run_id)
    finally:
        conn.close()

    # Group by sheet while preserving the (sheet, row) order from
    # list_notes_cells_for_run. A small dict-ordered walk is cheaper than
    # itertools.groupby for the expected payload size (< ~200 cells/run).
    sheets: dict[str, list[dict]] = {}
    for cell in cells:
        sheets.setdefault(cell.sheet, []).append({
            "row": cell.row,
            "label": cell.label,
            "html": cell.html,
            "evidence": cell.evidence,
            "source_pages": cell.source_pages,
            "updated_at": cell.updated_at,
        })
    return {
        "sheets": [
            {"sheet": sheet, "rows": rows}
            for sheet, rows in sheets.items()
        ],
    }


class _NotesCellPatch(BaseModel):
    """PATCH body — only ``html`` is editable.

    ``evidence`` and ``source_pages`` are deliberately omitted: the
    editor treats them as read-only audit data. `extra="forbid"`
    returns a 422 if a caller sends an unknown field — catches
    client-side typos like ``htmll`` early, and makes any future
    attempt to sneak an ``evidence`` override explicit instead of
    silently dropped.
    """
    model_config = ConfigDict(extra="forbid")

    html: str


@app.patch("/api/runs/{run_id}/notes_cells/{sheet}/{row}")
async def patch_notes_cell_endpoint(
    run_id: int, sheet: str, row: int, body: _NotesCellPatch,
):
    """Update one cell's HTML. Sanitises the payload and enforces the
    30k rendered-char cap server-side so the editor cannot bypass it.

    * 404 — no cell at (run_id, sheet, row).
    * 413 — sanitised HTML renders to more than 30 000 characters.
    * 200 — updated row returned in the same shape as GET list rows.

    **Concurrency note:** the SELECT-then-UPSERT here is not wrapped
    in a single transaction. Two concurrent PATCHes against the same
    cell from two browser tabs resolve as last-write-wins at commit
    time. This is intentionally left as the simple-single-user
    trade-off: the deployment target is a desktop tool for one
    accountant per machine (see CLAUDE.md), so cross-tab races are
    vanishingly rare and data loss is bounded to "the newer tab's
    edit wins, which is what the user would expect anyway".

    A parallel race exists between a live PATCH and the coordinator's
    ``persist_notes_cells`` during a regenerate: the regenerate
    clobbers, so any PATCH that raced with it silently loses. This
    is the documented semantics of regenerate (see CLAUDE.md gotcha
    #16) — not a bug.
    """
    from db import repository as repo
    from notes.html_sanitize import sanitize_notes_html
    from notes.html_to_text import rendered_length
    from notes.writer import CELL_CHAR_LIMIT

    # Pre-sanitise size guard (peer-review #4). Reject absurd-length
    # bodies before the sanitiser parses them — a megabyte of tags
    # would cost ~50ms of BeautifulSoup CPU per request and never
    # produce a valid cell. ~7x the rendered cap leaves plenty of
    # headroom for legitimate tag overhead on the 30k rendered limit
    # while cutting off the DOS avenue. Distinct detail string so
    # the pre-guard and post-cap rejections are distinguishable in
    # server logs.
    PRESANITIZE_HTML_CAP = 200_000
    if len(body.html) > PRESANITIZE_HTML_CAP:
        raise HTTPException(
            status_code=413,
            detail=(
                f"HTML too large (pre-sanitiser): {len(body.html):,} > "
                f"{PRESANITIZE_HTML_CAP:,} characters."
            ),
        )
    # Sanitise first so the cap is measured against the stored form.
    cleaned_html, warnings = sanitize_notes_html(body.html)
    if rendered_length(cleaned_html) > CELL_CHAR_LIMIT:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Rendered text exceeds the {CELL_CHAR_LIMIT:,} character "
                "limit. Shorten the cell before saving."
            ),
        )

    conn = _open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        # Peer-review I-3: SELECT+UPSERT must run inside a single write
        # transaction so a concurrent regenerate (which does
        # delete_notes_cells_for_run_sheet + re-INSERT) can't interleave
        # between our existence check and our write. BEGIN IMMEDIATE
        # upgrades the connection to a writer lock immediately; other
        # writers block (busy_timeout=5000ms) until this commit. Without
        # this wrap the PATCH can overwrite a freshly-regenerated row and
        # defeat the "regenerate clobbers" contract documented in CLAUDE.md
        # gotcha #16.
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Locate the existing row first so a PATCH against a non-existent
            # cell is a 404, not a silent insert. The editor only ever edits
            # cells it already listed via GET — phantom inserts would orphan
            # content from the template walk.
            existing = conn.execute(
                "SELECT id, label, evidence, source_pages FROM notes_cells "
                "WHERE run_id = ? AND sheet = ? AND row = ?",
                (run_id, sheet, row),
            ).fetchone()
            if existing is None:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Notes cell not found")

            # Round-trip source_pages so the upsert preserves them unchanged.
            # The column is JSON; list_notes_cells_for_run decodes it on read
            # but the upsert helper re-encodes from a Python list.
            from db.repository import decode_source_pages as _decode_pages
            pages = _decode_pages(existing["source_pages"])

            repo.upsert_notes_cell(
                conn,
                run_id=run_id,
                sheet=sheet,
                row=row,
                label=existing["label"],
                html=cleaned_html,
                evidence=existing["evidence"],
                source_pages=pages,
            )
            conn.commit()
        except HTTPException:
            # Already rolled back above — re-raise so FastAPI returns
            # the intended status/detail to the client.
            raise
        except Exception:
            conn.rollback()
            raise

        # Read back so the client sees the persisted updated_at.
        row_back = conn.execute(
            "SELECT label, html, evidence, source_pages, updated_at "
            "FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
            (run_id, sheet, row),
        ).fetchone()
    finally:
        conn.close()

    from db.repository import decode_source_pages
    return {
        "sheet": sheet,
        "row": row,
        "label": row_back["label"],
        "html": row_back["html"],
        "evidence": row_back["evidence"],
        "source_pages": decode_source_pages(row_back["source_pages"]),
        "updated_at": row_back["updated_at"] or "",
        # Peer-review #7: surface what the sanitiser removed so the
        # editor can tell the user "we dropped a <script> from your
        # paste" instead of silently swapping content. Empty list when
        # the sanitiser was a no-op — always present so clients can
        # treat it as a stable field.
        "sanitizer_warnings": warnings,
    }


@app.get("/api/runs/{run_id}/notes_cells/edited_count")
async def notes_cells_edited_count_endpoint(run_id: int):
    """Step 12 of docs/PLAN-NOTES-RICH-EDITOR.md — count how many
    ``notes_cells`` rows were touched *after* the run finished.

    The Regenerate-notes confirm dialog opens only when this returns
    ``count > 0``. Comparing ``updated_at > runs.ended_at`` is the
    cheap proxy for "user edited this cell post-run" — the writer
    never updates cells after the run's terminal event, so any later
    ``updated_at`` came from the PATCH endpoint.

    404 if the run does not exist. For runs that are still executing
    (``ended_at`` is NULL), we report 0 — there's nothing to lose
    because the agent is still the canonical source.
    """
    from db import repository as repo
    conn = _open_audit_conn()
    try:
        run = repo.fetch_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if not run.ended_at:
            return {"count": 0}
        row = conn.execute(
            "SELECT COUNT(*) FROM notes_cells "
            "WHERE run_id = ? AND updated_at > ?",
            (run_id, run.ended_at),
        ).fetchone()
    finally:
        conn.close()
    return {"count": int(row[0]) if row else 0}


@app.get("/api/runs/{run_id}/download/filled")
async def download_filled_endpoint(run_id: int):
    """Stream the merged workbook for a past run.

    Single source of truth for the file path is `runs.merged_workbook_path`.
    We explicitly do NOT derive the path from session_id or probe the
    filesystem — if the stored path no longer exists on disk we return a
    clear 404 instead of a 500.

    Step 7 of the notes rich-editor plan: when `notes_cells` has rows
    for this run, the canonical notes content lives in the DB (edited
    via the post-run editor). We overlay those cells onto a temp copy
    of the on-disk workbook at stream time so the download always
    reflects the latest HTML → flattened-plaintext rendering.
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
    # Overlay runs synchronously (openpyxl is blocking); push it off
    # the event loop so concurrent downloads don't serialise. Returns
    # the original path unchanged when notes_cells is empty, so the
    # pre-rich-editor behaviour is preserved on older runs.
    try:
        from notes.persistence import overlay_notes_cells_into_workbook
        served_path = await asyncio.to_thread(
            overlay_notes_cells_into_workbook,
            xlsx_path=wb_path,
            run_id=run_id,
            db_path=str(AUDIT_DB_PATH),
        )
    except Exception:  # noqa: BLE001 — fall back to on-disk file
        logger.exception(
            "notes_cells overlay failed for run_id=%s; serving stale xlsx",
            run_id,
        )
        served_path = wb_path
    # The overlay helper either returns the original path unchanged
    # (nothing to clean up) or a new temp file in the system temp dir
    # (must be deleted after streaming completes). Attach a background
    # task only in the second case — never delete the authoritative
    # `merged_workbook_path` on disk.
    cleanup: Optional[BackgroundTask] = None
    if served_path != wb_path:
        cleanup = BackgroundTask(_remove_overlay_tempfile, str(served_path))
    return FileResponse(
        str(served_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"run_{run_id}_filled.xlsx",
        background=cleanup,
    )


def _remove_overlay_tempfile(path: str) -> None:
    """Best-effort cleanup of a notes-overlay temp file after the
    FileResponse has finished streaming. Run as a Starlette
    BackgroundTask; errors are logged but never raised — the response
    has already been sent.
    """
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.debug("Failed to remove overlay temp file %s", path, exc_info=True)


# --- Download endpoints ---

ALLOWED_DOWNLOADS = {"filled.xlsx", "result.json", "conversation_trace.json"}
# Per-statement output files are also downloadable
_STMT_PREFIXES = ("SOFP_", "SOPL_", "SOCI_", "SOCF_", "SOCIE_")


@app.get("/api/result/{session_id}/{filename}")
async def download_result(session_id: str, filename: str):
    # Reject path-traversal tokens in BOTH components. session_id is a
    # UUID in practice (see /api/upload), so `..`, `/`, `\\` are never
    # legitimate. Validating session_id matters: previously, session_id=".."
    # made session_dir the parent of OUTPUT_DIR, and the relative_to()
    # anchor below would have been computed against that malicious parent
    # — escaping the output tree entirely.
    for component in (session_id, filename):
        if ".." in component or "/" in component or "\\" in component:
            raise HTTPException(status_code=400, detail="Invalid path component.")

    # Allow per-statement files (e.g. SOFP_filled.xlsx, SOPL_result.json)
    is_stmt_file = any(filename.startswith(p) for p in _STMT_PREFIXES) and filename.endswith((".xlsx", ".json", ".txt"))
    if filename not in ALLOWED_DOWNLOADS and not is_stmt_file:
        raise HTTPException(status_code=400, detail=f"File not available. Allowed: {ALLOWED_DOWNLOADS}")

    # Belt-and-braces: anchor the resolved path under OUTPUT_DIR itself,
    # not under a session_id-derived path — otherwise a malicious
    # session_id could relocate the anchor outside OUTPUT_DIR.
    output_root = OUTPUT_DIR.resolve()
    try:
        file_path = (output_root / session_id / filename).resolve()
        file_path.relative_to(output_root)
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid path component.")

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
