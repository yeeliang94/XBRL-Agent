"""SOFP Agent — FastAPI web server with SSE streaming.

Provides a web UI for uploading PDFs, running the SOFP extraction agent,
and streaming progress events in real-time via Server-Sent Events.

Uses PydanticAI's agent.iter() streaming API to emit granular events:
thinking tokens, tool calls/results, text deltas, and token updates.
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
from typing import AsyncIterator

from dotenv import load_dotenv, set_key
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB

# Phase mapping: tool name → EventPhase
PHASE_MAP = {
    "read_template": "reading_template",
    "view_pdf_pages": "viewing_pdf",
    "fill_workbook": "filling_workbook",
    "verify_totals": "verifying",
    "save_result": "complete",
}


# ---------------------------------------------------------------------------
# Model creation
# ---------------------------------------------------------------------------

def _create_proxy_model(model_name: str, proxy_url: str, api_key: str):
    """Create a PydanticAI model.

    - If ``proxy_url`` is set → enterprise LiteLLM proxy (Windows). Uses
      OpenAI-compatible API with an ``sk-...`` key.
    - If ``proxy_url`` is empty → direct Google Gemini API (Mac). Uses the
      ``google-gla`` provider with a ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``.
    """
    if not proxy_url:
        # Direct Gemini path — pydantic-ai picks up GEMINI_API_KEY from env,
        # but we pass it explicitly via the provider so concurrent sessions
        # cannot clobber each other.
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        # Strip any "vertex_ai." / "google-gla:" prefix from the model name,
        # since GoogleModel expects bare names like "gemini-3-flash-preview".
        bare = model_name.split(":", 1)[-1]
        if bare.startswith("vertex_ai."):
            bare = bare[len("vertex_ai."):]
        provider = GoogleProvider(api_key=api_key)
        return GoogleModel(bare, provider=provider)

    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(base_url=proxy_url, api_key=api_key)
    return OpenAIChatModel(model_name, provider=provider)


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

async def iter_agent_events(
    pdf_path: str,
    template_path: str,
    model_name: str,
    output_dir: str,
    api_key: str,
    proxy_url: str,
    session_id: str,
) -> AsyncIterator[dict]:
    """Yields SSE event dicts from a PydanticAI agent.iter() streaming run.

    This replaces the old run_agent_in_thread() + EventQueue pattern with native
    async streaming, giving us access to thinking tokens, tool call args before
    execution, and text deltas — all in real time.
    """
    from pydantic_ai import Agent
    from pydantic_ai.messages import (
        PartStartEvent, PartDeltaEvent,
        TextPartDelta, ThinkingPartDelta, ToolCallPartDelta,
        FunctionToolCallEvent, FunctionToolResultEvent,
    )

    # NOTE: credentials/model are passed explicitly via `_create_proxy_model`
    # and `create_sofp_agent` below — we do NOT write them to os.environ here.
    # Concurrent runs for different sessions would otherwise clobber each
    # other's credentials at the process level.

    try:
        from agent import create_sofp_agent

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        model = _create_proxy_model(model_name, proxy_url, api_key)

        agent, deps = create_sofp_agent(
            pdf_path=pdf_path,
            template_path=template_path,
            model=model,
            output_dir=output_dir,
            cache_template=False,
        )

        yield {"event": "status", "data": {"phase": "reading_template", "message": "Starting extraction..."}}

        thinking_id_counter = 0
        current_thinking_id = None
        thinking_buffer = ""
        # Monotonic start time of the current thinking block, for measuring
        # real reasoning duration (not "time since block ended").
        thinking_start_mono: float | None = None
        # Track tool call start times for duration calculation
        tool_start_times: dict[str, float] = {}

        async with agent.iter(
            "Extract the SOFP data from the PDF into the template. "
            "Follow the strategy in your system prompt. Begin by reading the template.",
            deps=deps,
            model=model,
        ) as agent_run:
            async for node in agent_run:
                if Agent.is_model_request_node(node):
                    # Stream model response: thinking tokens, text deltas
                    async with node.stream(agent_run.ctx) as stream:
                        async for event in stream:
                            if isinstance(event, PartStartEvent):
                                # Check if this is a thinking part starting
                                if hasattr(event.part, 'part_kind') and event.part.part_kind == 'thinking':
                                    thinking_id_counter += 1
                                    current_thinking_id = f"think_{thinking_id_counter}"
                                    thinking_buffer = ""
                                    thinking_start_mono = time.monotonic()

                            elif isinstance(event, PartDeltaEvent):
                                if isinstance(event.delta, ThinkingPartDelta):
                                    content = event.delta.content_delta or ""
                                    if content:
                                        thinking_buffer += content
                                        yield {
                                            "event": "thinking_delta",
                                            "data": {
                                                "content": content,
                                                "thinking_id": current_thinking_id or f"think_{thinking_id_counter}",
                                            },
                                        }

                                elif isinstance(event.delta, TextPartDelta):
                                    yield {
                                        "event": "text_delta",
                                        "data": {"content": event.delta.content_delta},
                                    }

                    # End thinking block if one was active
                    if current_thinking_id and thinking_buffer:
                        summary = thinking_buffer[:80]
                        duration_ms = (
                            int((time.monotonic() - thinking_start_mono) * 1000)
                            if thinking_start_mono is not None
                            else 0
                        )
                        yield {
                            "event": "thinking_end",
                            "data": {
                                "thinking_id": current_thinking_id,
                                "summary": summary,
                                "full_length": len(thinking_buffer),
                                "duration_ms": duration_ms,
                            },
                        }
                        current_thinking_id = None
                        thinking_buffer = ""
                        thinking_start_mono = None

                elif Agent.is_call_tools_node(node):
                    # Stream tool calls and results
                    async with node.stream(agent_run.ctx) as tool_stream:
                        async for event in tool_stream:
                            if isinstance(event, FunctionToolCallEvent):
                                tool_name = event.part.tool_name
                                tool_call_id = event.part.tool_call_id or f"tc_{tool_name}_{time.time()}"

                                # Record start time for duration calculation
                                tool_start_times[tool_call_id] = time.monotonic()

                                # Emit phase change if tool has a mapping
                                base_name = tool_name.split("(")[0]
                                if base_name in PHASE_MAP:
                                    yield {
                                        "event": "status",
                                        "data": {
                                            "phase": PHASE_MAP[base_name],
                                            "message": f"Running {tool_name}...",
                                        },
                                    }

                                # Strip binary data from args
                                args = event.part.args
                                if isinstance(args, str):
                                    try:
                                        args = json.loads(args)
                                    except (json.JSONDecodeError, TypeError):
                                        args = {"raw": args}
                                elif not isinstance(args, dict):
                                    args = {"raw": str(args)}

                                yield {
                                    "event": "tool_call",
                                    "data": {
                                        "tool_name": tool_name,
                                        "tool_call_id": tool_call_id,
                                        "args": args,
                                    },
                                }

                            elif isinstance(event, FunctionToolResultEvent):
                                tool_call_id = getattr(event.result, 'tool_call_id', '') or ''
                                tool_name = getattr(event.result, 'tool_name', '') or ''
                                content = getattr(event.result, 'content', '')
                                result_summary = str(content)[:200] if content else "completed"

                                # Compute real duration from recorded start time
                                start_t = tool_start_times.pop(tool_call_id, None)
                                duration_ms = int((time.monotonic() - start_t) * 1000) if start_t else 0

                                yield {
                                    "event": "tool_result",
                                    "data": {
                                        "tool_name": tool_name,
                                        "tool_call_id": tool_call_id,
                                        "result_summary": result_summary,
                                        "duration_ms": duration_ms,
                                    },
                                }

                # Emit token_update after each node
                # NOTE: agent_run.usage is a method in pydantic-ai 1.x (will be a
                # property in v2). Call it to get the RunUsage object.
                usage = agent_run.usage() if callable(agent_run.usage) else agent_run.usage
                if usage:
                    yield {
                        "event": "token_update",
                        "data": {
                            "prompt_tokens": getattr(usage, "input_tokens", 0) or 0,
                            "completion_tokens": getattr(usage, "output_tokens", 0) or 0,
                            "thinking_tokens": getattr(usage, "cache_read_tokens", 0) or 0,
                            "cumulative": getattr(usage, "total_tokens", 0) or 0,
                            "cost_estimate": _calc_cost(usage),
                        },
                    }

            # Save conversation trace
            run_result = agent_run.result
            if run_result:
                _save_trace(run_result, output_dir)

            # Final token stats
            total_tokens = 0
            cost = 0.0
            usage = agent_run.usage() if callable(agent_run.usage) else agent_run.usage
            if usage:
                total_tokens = getattr(usage, "total_tokens", 0) or 0
                cost = _calc_cost(usage)

            # Emit final token_update
            if usage:
                yield {
                    "event": "token_update",
                    "data": {
                        "prompt_tokens": getattr(usage, "input_tokens", 0) or 0,
                        "completion_tokens": getattr(usage, "output_tokens", 0) or 0,
                        "thinking_tokens": getattr(usage, "cache_read_tokens", 0) or 0,
                        "cumulative": total_tokens,
                        "cost_estimate": cost,
                    },
                }

            yield {
                "event": "complete",
                "data": {
                    "success": bool(run_result and run_result.output),
                    "output_path": str(Path(output_dir) / "result.json"),
                    "excel_path": deps.filled_path or str(Path(output_dir) / "filled.xlsx"),
                    "trace_path": str(Path(output_dir) / "conversation_trace.json"),
                    "total_tokens": total_tokens,
                    "cost": cost,
                },
            }

    except Exception as e:
        logger.exception("Agent run failed", extra={"session_id": session_id})
        yield {
            "event": "error",
            "data": {
                "message": str(e),
                "traceback": traceback.format_exc(),
            },
        }


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(title="SOFP Agent", version="0.2.0")

# Track active extraction runs by session_id
active_runs: dict[str, bool] = {}


# --- Settings endpoints ---

@app.get("/api/settings")
async def get_settings():
    load_dotenv(ENV_FILE, override=True)
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else ""
    return {
        "model": os.environ.get("TEST_MODEL", "vertex_ai.gemini-3-flash-preview"),
        "proxy_url": os.environ.get("LLM_PROXY_URL", ""),
        "api_key_set": bool(api_key),
        "api_key_preview": masked,
    }


@app.post("/api/settings")
async def update_settings(body: dict):
    """Update .env file with new settings."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    if "model" in body:
        set_key(str(ENV_FILE), "TEST_MODEL", body["model"])
    if "api_key" in body and body["api_key"]:
        set_key(str(ENV_FILE), "GOOGLE_API_KEY", body["api_key"])
    if "proxy_url" in body and body["proxy_url"]:
        set_key(str(ENV_FILE), "LLM_PROXY_URL", body["proxy_url"])

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


# --- SSE extraction endpoint ---

def _find_template(session_dir: Path) -> str:
    """Find the SOFP Excel template. Looks in the session dir first, then project root."""
    # Check session dir for a user-uploaded template
    for f in session_dir.glob("*.xlsx"):
        if "template" in f.name.lower():
            return str(f)
    # Fall back to the project-level template
    project_template = BASE_DIR / "SOFP-Xbrl-template.xlsx"
    if project_template.exists():
        return str(project_template)
    # Try the data/ directory (where bundled sample data lives in this standalone repo)
    data_template = BASE_DIR / "data" / "SOFP-Xbrl-template.xlsx"
    if data_template.exists():
        return str(data_template)
    raise FileNotFoundError("No SOFP template found. Upload one or place SOFP-Xbrl-template.xlsx in the project.")


@app.get("/api/run/{session_id}")
async def run_extraction(session_id: str):
    """SSE endpoint — starts async streaming agent run."""
    session_dir = OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found. Upload first.")

    # Reject if already running
    if session_id in active_runs:
        raise HTTPException(status_code=409, detail="Extraction already running for this session.")

    # Load settings
    load_dotenv(ENV_FILE, override=True)
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    model_name = os.environ.get("TEST_MODEL", "vertex_ai.gemini-3-flash-preview")

    if not api_key:
        raise HTTPException(status_code=400, detail="GOOGLE_API_KEY must be set. Check Settings.")

    # Find template
    try:
        template_path = _find_template(session_dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))

    active_runs[session_id] = True

    async def event_stream():
        try:
            async for evt in iter_agent_events(
                pdf_path=str(pdf_path),
                template_path=template_path,
                model_name=model_name,
                output_dir=str(session_dir),
                api_key=api_key,
                proxy_url=proxy_url,
                session_id=session_id,
            ):
                yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
        finally:
            active_runs.pop(session_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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


@app.get("/api/result/{session_id}/{filename}")
async def download_result(session_id: str, filename: str):
    if filename not in ALLOWED_DOWNLOADS:
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
