# SOFP Agent — Web Frontend Implementation Plan

## Overview

Add a simple web UI to the SOFP agent experiment so users can upload a financial statement PDF, watch the agent extract data in real-time, and download results — all from a browser. The CLI (`run.py`) remains fully functional.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Browser                                                    │
│  ┌───────────┐ ┌────────────┐ ┌──────────────┐ ┌─────────┐ │
│  │ UploadPanel│ │ LiveFeed   │ │ TokenDashboard│ │ Results │ │
│  └─────┬─────┘ └──────┬─────┘ └──────┬───────┘ └────┬────┘ │
│        │               │              │               │       │
│        │  HTTP POST    │   SSE GET    │               │ HTTP  │
│        ▼               ▼              ▼               ▼       │
└────────┼───────────────┼──────────────┼───────────────┼───────┘
         │               │              │               │
┌────────┼───────────────┼──────────────┼───────────────┼───────┐
│  FastAPI (server.py)    │              │               │       │
│  ┌──────┴──────┐ ┌──────┴──────┐ ┌────┴──────────┐ ┌──┴─────┐│
│  │ POST /upload│ │ GET /run/…  │ │ GET /result/… │ │ Settings││
│  └──────┬──────┘ └──────┬──────┘ └────┬──────────┘ └──┬─────┘│
│         │               │              │               │       │
│  ┌──────┴───────────────┴──────────────┴───────────────┴────┐│
│  │  AgentRunner (background thread)                          ││
│  │  ┌─────────────────────────────────────────────────────┐  ││
│  │  │ EventQueue  ◄── agent hooks emit events             │  ││
│  │  │ SSE generator ◄── reads queue, yields to client     │  ││
│  │  └─────────────────────────────────────────────────────┘  ││
│  └───────────────────────────────────────────────────────────┘│
│                                                               │
│  ┌───────────────────────────────────────────────────────────┐│
│  │  Existing Agent (agent.py + tools/)                       ││
│  │  create_sofp_agent() → agent.run_sync()                   ││
│  └───────────────────────────────────────────────────────────┘│
└───────────────────────────────────────────────────────────────┘
```

### Technology Choices

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Backend | FastAPI | Already in project deps, async-native, SSE support |
| Frontend | Vite + React 18 + TypeScript | Fast HMR, small bundle, familiar DX |
| Styling | Tailwind CSS 4 | Consistent with main REAM frontend |
| Real-time | Server-Sent Events (SSE) | Unidirectional stream is perfect for agent events; simpler than WebSockets |
| State | React `useReducer` | Deterministic event replay, no external state library needed |
| Excel preview | `xlsx` (SheetJS) in browser | Client-side Excel parsing, no server rendering needed |
| JSON viewer | Custom collapsible tree | Lightweight, no heavy dependency |

---

## Data Flow

```
1. User opens http://localhost:8002
2. (Optional) User clicks Settings → enters GEMINI_API_KEY, selects model
3. User drags/drops PDF → POST /api/upload → returns { session_id, filename, pages }
4. User clicks "Run Extraction" → GET /api/run/{session_id} (SSE connection opens)
5. Server starts agent in background thread with an EventQueue
6. Agent events stream to browser in real-time:
   - status: "reading_template" → "viewing_pdf" → "filling_workbook" → "verifying" → "complete"
   - tool_call: each tool invocation with args
   - tool_result: result summary + duration
   - token_update: cumulative token counts + cost estimate
7. On completion, SSE sends `complete` event with file paths
8. Frontend shows download buttons for filled.xlsx, result.json, trace.json
9. Frontend shows Excel preview (SheetJS) and JSON viewer inline
```

---

## File Structure

```
experiments/sofp-agent/
├── server.py                     # NEW: FastAPI server with SSE
├── agent.py                      # existing — minor modification for event hooks
├── run.py                        # existing — CLI still works unchanged
├── token_tracker.py              # existing — no changes
├── .env                          # NEW (gitignored) — runtime config
├── .env.example                  # NEW — template with defaults
├── requirements.txt              # NEW — Python deps
├── start.sh                      # NEW — macOS/Linux startup script
├── start.bat                     # NEW — Windows startup script
├── web/                          # NEW: Vite frontend
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   ├── index.html
│   ├── postcss.config.js
│   ├── tailwind.config.ts
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── index.css
│       ├── components/
│       │   ├── UploadPanel.tsx
│       │   ├── LiveFeed.tsx
│       │   ├── TokenDashboard.tsx
│       │   ├── ResultsPanel.tsx
│       │   └── SettingsModal.tsx
│       └── lib/
│           ├── api.ts            # REST API client
│           ├── sse.ts            # SSE event source wrapper
│           └── types.ts          # Shared TypeScript types
├── tools/                        # existing — no changes
│   ├── __init__.py
│   ├── fill_workbook.py
│   ├── pdf_viewer.py
│   ├── template_reader.py
│   └── verifier.py
├── tests/                        # existing — no changes
└── output/                       # existing — per-session subdirs
    └── {session_id}/
        ├── filled.xlsx
        ├── result.json
        ├── cost_report.txt
        └── conversation_trace.json
```

---

## Backend Design (`server.py`)

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/upload` | Accept PDF file, save to `output/{session_id}/`, return session metadata |
| `GET` | `/api/run/{session_id}` | SSE endpoint — streams agent events until completion |
| `GET` | `/api/result/{session_id}/filled.xlsx` | Download filled workbook |
| `GET` | `/api/result/{session_id}/result.json` | Download JSON results |
| `GET` | `/api/result/{session_id}/trace.json` | Download conversation trace |
| `GET` | `/api/settings` | Get current settings (API key masked, model list) |
| `POST` | `/api/settings` | Update settings (writes to `.env`) |
| `GET` | `/` | Serve Vite build output (index.html) |
| `GET` | `/assets/*` | Serve Vite build assets |

### SSE Event Types

Each event is a JSON object sent as `data: {...}\n\n` with an `event:` type header.

```python
# status — phase transitions
event: status
data: {"phase": "reading_template", "message": "Reading template structure...", "timestamp": 1712345678.123}

# tool_call — agent invokes a tool
event: tool_call
data: {"tool_name": "view_pdf_pages", "args": {"pages": [1, 2, 3]}, "timestamp": 1712345679.456}

# tool_result — tool returns
event: tool_result
data: {"tool_name": "view_pdf_pages", "result_summary": "3 pages rendered", "duration_ms": 1240, "timestamp": 1712345680.789}

# token_update — periodic token count sync
event: token_update
data: {"prompt_tokens": 12500, "completion_tokens": 3200, "thinking_tokens": 800, "cumulative": 16500, "cost_estimate": 0.0045, "timestamp": 1712345681.012}

# error — agent or server error
event: error
data: {"message": "Template not found", "traceback": "...", "timestamp": 1712345682.345}

# complete — agent finished
event: complete
data: {
  "success": true,
  "output_path": "output/abc123/result.json",
  "excel_path": "output/abc123/filled.xlsx",
  "trace_path": "output/abc123/conversation_trace.json",
  "total_tokens": 45200,
  "cost": 0.0271,
  "timestamp": 1712345690.000
}
```

### Core Implementation: Event Interception

The key challenge is intercepting agent tool calls and token updates in real-time without modifying PydanticAI internals. The approach: **wrap each tool with an event-emitting decorator** and **poll the TokenReport for updates**.

```python
# server.py — event queue and agent runner

import asyncio
import json
import os
import queue
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from dotenv import load_dotenv, set_key

# --- Event types ---

class EventQueue:
    """Thread-safe queue for agent events. SSE endpoint reads from this."""
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._done = False

    def put(self, event_type: str, data: dict):
        self._queue.put({
            "event": event_type,
            "data": data,
            "timestamp": time.time(),
        })

    def get(self, timeout: float = 1.0):
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def mark_done(self):
        self._done = True
        self._queue.put(None)  # sentinel

    @property
    def is_done(self):
        return self._done


# --- Tool wrappers that emit events ---

def make_event_emitting_tool(original_fn, event_queue: EventQueue, phase_map: dict[str, str]):
    """Wrap a tool function so it emits tool_call/tool_result events."""
    def wrapper(*args, **kwargs):
        tool_name = original_fn.__name__
        
        # Emit status event if we have a phase mapping
        if tool_name in phase_map:
            event_queue.put("status", {
                "phase": phase_map[tool_name],
                "message": f"Calling {tool_name}...",
            })
        
        # Emit tool_call event
        # Filter out large binary args from the event
        safe_args = {k: v for k, v in kwargs.items() if not isinstance(v, (bytes, bytearray))}
        event_queue.put("tool_call", {
            "tool_name": tool_name,
            "args": safe_args,
        })
        
        t0 = time.monotonic()
        try:
            result = original_fn(*args, **kwargs)
            duration_ms = int((time.monotonic() - t0) * 1000)
            
            # Build a short summary
            if isinstance(result, str):
                summary = result[:500]
            elif hasattr(result, '__dict__'):
                summary = str({k: v for k, v in result.__dict__.items() 
                              if not isinstance(v, (dict, list)) or len(str(v)) < 200})
            else:
                summary = str(result)[:500]
            
            event_queue.put("tool_result", {
                "tool_name": tool_name,
                "result_summary": summary,
                "duration_ms": duration_ms,
            })
            
            return result
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            event_queue.put("tool_result", {
                "tool_name": tool_name,
                "result_summary": f"Error: {e}",
                "duration_ms": duration_ms,
            })
            raise
    
    wrapper.__name__ = original_fn.__name__
    return wrapper


# --- Agent runner (background thread) ---

def run_agent_in_thread(
    pdf_path: str,
    template_path: str,
    model_name: str,
    output_dir: str,
    event_queue: EventQueue,
    api_key: str,
    proxy_url: str,
):
    """Run the SOFP agent in a background thread, emitting events."""
    import os
    os.environ["GOOGLE_API_KEY"] = api_key
    os.environ["LLM_PROXY_URL"] = proxy_url
    os.environ["TEST_MODEL"] = model_name
    
    # Suppress LiteLLM SSL warnings (enterprise firewall blocks GitHub pricing fetch)
    import litellm
    litellm.suppress_debug_info = True
    
    try:
        from pydantic_ai.models.openai import OpenAIModel
        from agent import create_sofp_agent, AgentDeps
        from token_tracker import TokenReport
        
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        
        # Build PydanticAI model routed through the enterprise proxy
        model = OpenAIModel(
            model_name=model_name,
            base_url=proxy_url,
            api_key=api_key,
        )
        
        # Create the agent — pass model object, not string
        agent, deps = create_sofp_agent(
            pdf_path=pdf_path,
            template_path=template_path,
            model=model,
            output_dir=output_dir,
            cache_template=False,
        )
        
        # Phase mapping for status events
        phase_map = {
            "read_template": "reading_template",
            "view_pdf_pages": "viewing_pdf",
            "fill_workbook": "filling_workbook",
            "verify_totals": "verifying",
            "save_result": "complete",
        }
        
        # Wrap agent tools to emit events
        # We need to re-register the tools on the agent with our wrappers.
        # The cleanest approach: monkey-patch the tool implementations on the
        # agent's _function_tools dict after creation.
        
        for tool_name, phase in phase_map.items():
            if tool_name in agent._function_tools:
                original = agent._function_tools[tool_name]
                # The tool is a ToolDefinition or similar — we wrap its function
                # PydanticAI stores tools internally; we intercept at the call level
                # by wrapping the underlying callable
                if hasattr(original, '__call__'):
                    wrapped = make_event_emitting_tool(original, event_queue, {tool_name: phase})
                    agent._function_tools[tool_name] = wrapped
        
        # Start token polling thread
        token_poller = threading.Thread(
            target=_poll_tokens,
            args=(token_report, event_queue),
            daemon=True,
        )
        token_poller.start()
        
        # Run the agent
        result = agent.run_sync(
            "Extract the SOFP data from the PDF into the template. "
            "Follow the strategy in your system prompt. Begin by reading the template.",
            deps=deps,
        )
        
        # Save conversation trace
        _save_trace(result, output_dir)
        
        # Emit completion event
        event_queue.put("complete", {
            "success": bool(result.output),
            "output_path": str(Path(output_dir) / "result.json"),
            "excel_path": deps.filled_path or str(Path(output_dir) / "filled.xlsx"),
            "trace_path": str(Path(output_dir) / "conversation_trace.json"),
            "total_tokens": token_report.grand_total,
            "cost": token_report.estimate_cost(),
        })
        
    except Exception as e:
        event_queue.put("error", {
            "message": str(e),
            "traceback": traceback.format_exc(),
        })
    finally:
        event_queue.mark_done()


def _poll_tokens(token_report, event_queue: EventQueue, interval: float = 2.0):
    """Poll the TokenReport periodically and emit token_update events."""
    last_count = 0
    while not event_queue.is_done:
        if len(token_report.turns) > last_count:
            last_count = len(token_report.turns)
            event_queue.put("token_update", {
                "prompt_tokens": token_report.total_prompt_tokens,
                "completion_tokens": token_report.total_completion_tokens,
                "thinking_tokens": token_report.total_thinking_tokens,
                "cumulative": token_report.grand_total,
                "cost_estimate": token_report.estimate_cost(),
            })
        time.sleep(interval)


def _save_trace(result, output_dir: str):
    """Save conversation trace (adapted from run.py)."""
    import json
    import dataclasses
    from pathlib import Path
    
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
    trace_path.write_text(json.dumps(trace, indent=2, default=str))


def _strip_binary(obj):
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key == "data" and isinstance(obj.get("media_type"), str) and "image" in obj["media_type"]:
                obj[key] = f"[{obj['media_type']} image data stripped]"
            else:
                _strip_binary(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _strip_binary(item)
```

### SSE Endpoint

```python
@app.get("/api/run/{session_id}")
async def run_extraction(session_id: str):
    """SSE endpoint — starts agent if not running, streams events."""
    session_dir = OUTPUT_DIR / session_id
    pdf_path = session_dir / "uploaded.pdf"
    
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found. Upload first.")
    
    # Check if already running
    if session_id in active_runs:
        raise HTTPException(status_code=409, detail="Extraction already running for this session.")
    
    # Create event queue
    eq = EventQueue()
    active_runs[session_id] = eq
    
    # Load settings
    load_dotenv(ENV_FILE)
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    model_name = os.environ.get("TEST_MODEL", "vertex_ai.gemini-3-flash-preview")
    
    if not api_key or not proxy_url:
        raise HTTPException(status_code=400, detail="GOOGLE_API_KEY and LLM_PROXY_URL must be set. Check Settings.")
    
    # Find template
    template_path = _find_template(session_dir)
    
    # Start agent in background thread
    thread = threading.Thread(
        target=run_agent_in_thread,
        args=(str(pdf_path), str(template_path), model_name, str(session_dir), eq, api_key, proxy_url),
        daemon=True,
    )
    thread.start()
    
    # SSE generator
    def event_stream():
        try:
            while True:
                event = eq.get(timeout=30.0)
                if event is None:  # sentinel
                    break
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"
        finally:
            active_runs.pop(session_id, None)
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
```

### Settings Endpoints

```python
@app.get("/api/settings")
async def get_settings():
    load_dotenv(ENV_FILE)
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
    
    # Reload env for current process
    load_dotenv(ENV_FILE, override=True)
    return {"status": "ok"}
```

---

## Frontend Design

### TypeScript Types (`web/src/lib/types.ts`)

```typescript
export interface UploadResponse {
  session_id: string;
  filename: string;
  pages: number;
}

export interface SettingsResponse {
  model: string;
  proxy_url: string;
  api_key_set: boolean;
  api_key_preview: string;
}

export type EventPhase =
  | "reading_template"
  | "viewing_pdf"
  | "filling_workbook"
  | "verifying"
  | "complete";

export interface SSEEvent {
  event: "status" | "tool_call" | "tool_result" | "token_update" | "error" | "complete";
  data: StatusData | ToolCallData | ToolResultData | TokenData | ErrorData | CompleteData;
  timestamp: number;
}

export interface StatusData {
  phase: EventPhase;
  message: string;
}

export interface ToolCallData {
  tool_name: string;
  args: Record<string, unknown>;
}

export interface ToolResultData {
  tool_name: string;
  result_summary: string;
  duration_ms: number;
}

export interface TokenData {
  prompt_tokens: number;
  completion_tokens: number;
  thinking_tokens: number;
  cumulative: number;
  cost_estimate: number;
}

export interface ErrorData {
  message: string;
  traceback: string;
}

export interface CompleteData {
  success: boolean;
  output_path: string;
  excel_path: string;
  trace_path: string;
  total_tokens: number;
  cost: number;
}
```

### SSE Client (`web/src/lib/sse.ts`)

```typescript
import { type SSEEvent } from "./types";

export function createSSE(
  sessionId: string,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
  onError: (error: string) => void,
): AbortController {
  const controller = new AbortController();
  const url = `/api/run/${sessionId}`;

  const connect = () => {
    const eventSource = new EventSource(url);

    const eventTypes = ["status", "tool_call", "tool_result", "token_update", "error", "complete"] as const;

    for (const eventType of eventTypes) {
      eventSource.addEventListener(eventType, (e) => {
        const data = JSON.parse(e.data);
        onEvent({ event: eventType, data, timestamp: Date.now() / 1000 });

        if (eventType === "complete" || eventType === "error") {
          eventSource.close();
          onDone();
        }
      });
    }

    eventSource.onerror = () => {
      eventSource.close();
      onError("SSE connection lost");
    };

    return eventSource;
  };

  const es = connect();
  controller.signal.addEventListener("abort", () => es.close());
  return controller;
}
```

### App State (`web/src/App.tsx`)

Use `useReducer` for deterministic event replay:

```typescript
interface AppState {
  sessionId: string | null;
  filename: string | null;
  isRunning: boolean;
  isComplete: boolean;
  hasError: boolean;
  events: SSEEvent[];
  currentPhase: EventPhase | null;
  tokens: TokenData | null;
  error: ErrorData | null;
  complete: CompleteData | null;
}

type AppAction =
  | { type: "UPLOADED"; payload: { sessionId: string; filename: string } }
  | { type: "RUN_STARTED" }
  | { type: "EVENT"; payload: SSEEvent }
  | { type: "RESET" };

function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "UPLOADED":
      return { ...state, sessionId: action.payload.sessionId, filename: action.payload.filename, isComplete: false, hasError: false, events: [], currentPhase: null, tokens: null, error: null, complete: null };
    case "RUN_STARTED":
      return { ...state, isRunning: true };
    case "EVENT": {
      const event = action.payload;
      const updates: Partial<AppState> = { events: [...state.events, event] };
      if (event.event === "status") updates.currentPhase = (event.data as StatusData).phase;
      if (event.event === "token_update") updates.tokens = event.data as TokenData;
      if (event.event === "error") { updates.hasError = true; updates.error = event.data as ErrorData; updates.isRunning = false; }
      if (event.event === "complete") { updates.isComplete = true; updates.isRunning = false; updates.complete = event.data as CompleteData; }
      return { ...state, ...updates };
    }
    case "RESET":
      return { sessionId: null, filename: null, isRunning: false, isComplete: false, hasError: false, events: [], currentPhase: null, tokens: null, error: null, complete: null };
    default:
      return state;
  }
}
```

### Component Specifications

#### UploadPanel.tsx
- Drag-and-drop zone with file type validation (`.pdf` only)
- On drop: `POST /api/upload` with FormData
- On success: dispatch `UPLOADED` action
- Shows filename and page count after upload
- "Run Extraction" button appears after successful upload
- Disabled state while running

#### LiveFeed.tsx
- Scrollable list of events, newest at bottom
- Collapsible cards per event type:
  - **Status**: colored badge with phase name, progress indicator
  - **Tool call**: tool name + pretty-printed args (truncated)
  - **Tool result**: tool name + summary + duration badge
  - **Token update**: compact inline display (delegated to TokenDashboard)
  - **Error**: red card with full traceback in expandable section
- Auto-scroll to bottom on new events
- "Clear" button to collapse all

#### TokenDashboard.tsx
- Sticky header showing live token counts
- Four metrics in a row: Prompt tokens, Completion tokens, Thinking tokens, Cumulative
- Cost estimate prominently displayed
- Mini bar chart showing token distribution (prompt vs completion vs thinking)
- Updates in real-time from `token_update` events

#### ResultsPanel.tsx
- Shown when `isComplete && complete?.success`
- Three download buttons: Excel, JSON, Trace
- **Excel preview**: Uses SheetJS (`xlsx` npm package) to parse and display the filled workbook
  - Shows sheet tabs
  - Renders as HTML table with highlighting for cells the agent wrote (columns D/E contain evidence)
- **JSON viewer**: Collapsible tree view of `result.json`
  - Color-coded keys/values
  - Expand/collapse all buttons
- **Summary card**: Total tokens, cost, fields written, balance status

#### SettingsModal.tsx
- Opens via gear icon in header
- **Proxy URL**: text input pre-filled from settings (default: `https://genai-sharedservice-emea.pwc.com`)
- **API Key**: masked input (shows preview from `/api/settings`), "Change" button
- **Model name**: editable text input (not a dropdown — proxy model names are user-defined)
- Save button: `POST /api/settings`
- Confirmation toast on success
- Closes on backdrop click or Escape

---

## How to Intercept Agent Events for SSE

There are three approaches, ranked by invasiveness:

### Approach 1: Wrap tools via agent._function_tools (RECOMMENDED)

PydanticAI stores registered tools internally. After `create_sofp_agent()` returns, we can wrap the tool callables:

```python
# In server.py, after creating the agent:
original_tools = dict(agent._function_tools)

for name, tool in original_tools.items():
    # tool is a ToolDefinition; its callable is in tool.function
    original_fn = tool.function
    wrapped_fn = make_event_emitting_tool(original_fn, event_queue, phase_map)
    tool.function = wrapped_fn  # replace in-place
```

**Pros**: Minimal changes to existing code, CLI still works, clean separation.
**Cons**: Relies on internal PydanticAI attribute (`_function_tools`).

### Approach 2: Subclass and override tool registration

Create a wrapper around `create_sofp_agent` that accepts an `event_queue` parameter and wraps tools before returning:

```python
def create_sofp_agent_with_events(pdf_path, template_path, model, output_dir, event_queue):
    agent, deps = create_sofp_agent(pdf_path, template_path, model, output_dir)
    # Wrap tools as in Approach 1
    return agent, deps
```

**Pros**: Clean API, no monkey-patching.
**Cons**: Requires modifying `agent.py` to accept an optional callback.

### Approach 3: Modify agent.py to accept event hooks

Add an optional `event_callback` parameter to `AgentDeps` and have each tool call it:

```python
# agent.py — minimal change
class AgentDeps:
    def __init__(self, ..., event_callback=None):
        ...
        self.event_callback = event_callback  # callable(type, data)

# In each tool:
if ctx.deps.event_callback:
    ctx.deps.event_callback("tool_call", {"tool_name": "read_template", "args": {}})
```

**Pros**: Most robust, explicit, testable.
**Cons**: Modifies `agent.py` (5 lines of optional plumbing).

### Recommendation

Use **Approach 1** for the initial implementation. It requires zero changes to `agent.py` and keeps the CLI path clean. If PydanticAI's internal API changes in a future version, migrate to Approach 3.

### Token Updates

Token counts are tracked in `TokenReport` which is updated synchronously by `_track_turn()` calls inside each tool. A **polling thread** reads the report every 2 seconds and emits `token_update` events:

```python
def _poll_tokens(token_report, event_queue, interval=2.0):
    last_count = 0
    while not event_queue.is_done:
        if len(token_report.turns) > last_count:
            last_count = len(token_report.turns)
            event_queue.put("token_update", {
                "prompt_tokens": token_report.total_prompt_tokens,
                "completion_tokens": token_report.total_completion_tokens,
                "thinking_tokens": token_report.total_thinking_tokens,
                "cumulative": token_report.grand_total,
                "cost_estimate": token_report.estimate_cost(),
            })
        time.sleep(interval)
```

This is simple and reliable. The 2-second interval is imperceptible to users since tool calls themselves take seconds to minutes.

---

## Enterprise Portability (Windows + LiteLLM Proxy)

The enterprise environment **blocks direct Google API calls** (403 Forbidden). All LLM traffic must route through the enterprise GenAI proxy using OpenAI-compatible protocol. See `enterprise_proxy.txt` for full details.

### Key Constraint: PydanticAI Must Use `OpenAIModel`

The agent currently creates models via string: `Agent("google-gla:gemini-3-flash-preview")`. This makes direct Google API calls which are blocked. Instead, use PydanticAI's OpenAI provider pointed at the proxy:

```python
from pydantic_ai.models.openai import OpenAIModel

def create_model() -> OpenAIModel:
    """Create PydanticAI model routed through the enterprise LiteLLM proxy."""
    return OpenAIModel(
        model_name=os.environ.get("TEST_MODEL", "vertex_ai.gemini-3-flash-preview"),
        base_url=os.environ["LLM_PROXY_URL"],
        api_key=os.environ["GOOGLE_API_KEY"],
    )
```

This means `create_sofp_agent(model=...)` must accept `str | Model` (not just `str`), and `server.py` passes a constructed `OpenAIModel` object.

### Vision / BinaryContent Through the Proxy

The agent sends PDF page images via `BinaryContent(data=png_bytes, media_type="image/png")`. PydanticAI's `OpenAIModel` backend encodes these as base64 `data:image/png;base64,...` in OpenAI `image_url` content blocks. The enterprise proxy has confirmed vision works via both URL and base64 methods (see `enterprise_proxy.txt` §4.1). This is the highest-risk integration point — test first with a smoke test (Cycle 0).

### Temperature Constraint

For Gemini 3 models through the proxy, temperature **must** stay at `1.0` (the default). Lower values cause failures or infinite loops. Do not override temperature in agent creation.

### SSL Certificate Warnings

The enterprise firewall blocks LiteLLM's attempt to fetch pricing data from GitHub. This produces a safe-to-ignore warning. Suppress at startup:

```python
import litellm
litellm.suppress_debug_info = True
```

### `.env.example`

```env
# SOFP Agent Web UI Configuration
# Copy this file to .env and fill in your values
#
# Get LLM_PROXY_URL and GOOGLE_API_KEY from:
#   Bruno → Collection → Auth tab → Vars/Secrets

# Required: Enterprise GenAI proxy URL
LLM_PROXY_URL=https://genai-sharedservice-emea.pwc.com

# Required: API key for the proxy (Bearer token)
GOOGLE_API_KEY=

# Model name as registered on the proxy
# Do NOT use google-gla: prefix — the proxy speaks OpenAI protocol
TEST_MODEL=vertex_ai.gemini-3-flash-preview

# WARNING: Do not set temperature below 1.0 for Gemini 3 models — causes failures

# Server host and port
HOST=0.0.0.0
PORT=8002
```

### `start.bat` (Windows — Enterprise)

```batch
@echo off
setlocal enabledelayedexpansion

echo ========================================
echo   SOFP Agent — Web UI
echo ========================================
echo.

:: ---- Check .env exists ----
if not exist ".env" (
    if exist ".env.example" (
        echo .env not found. Copying from .env.example...
        copy .env.example .env >nul
    ) else (
        echo ERROR: No .env or .env.example found.
        pause
        exit /b 1
    )
    echo.
    echo IMPORTANT: Edit .env and set your GOOGLE_API_KEY
    echo   Get it from Bruno → Collection → Auth tab → Vars/Secrets
    echo.
    notepad .env
    pause
)

:: ---- Find Python (may not be on PATH) ----
where python >nul 2>&1
if errorlevel 1 (
    :: Try common install locations
    for %%P in (
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
    ) do (
        if exist %%P (
            echo Found Python at %%~dpP
            set "PATH=%%~dpP;%%~dpPScripts;%PATH%"
            goto :python_found
        )
    )
    echo ERROR: Python not found. Install Python 3.11+ first.
    pause
    exit /b 1
)
:python_found
python --version

:: ---- Find Node.js (installed but not on PATH) ----
where node >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\nodejs\node.exe" (
        echo Found Node.js in Program Files, adding to PATH...
        set "PATH=C:\Program Files\nodejs;%PATH%"
    ) else (
        echo WARNING: Node.js not found. Frontend will not be built.
        echo If Node.js is installed elsewhere, set PATH manually.
        goto :skip_frontend
    )
)
echo Node.js: & node --version

:: ---- Create venv if needed ----
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

:: Activate venv
call venv\Scripts\activate.bat

:: Install Python deps
echo Installing Python dependencies...
pip install -r requirements.txt -q

:: ---- Build frontend (if web/ exists) ----
if exist "web\package.json" (
    echo Installing frontend dependencies...
    cd web
    call npm install
    echo Building frontend...
    call npm run build
    cd ..
) else (
    echo WARNING: web/package.json not found. Skipping frontend build.
)
goto :start_server

:skip_frontend
:: Still need Python deps even if no frontend
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)
call venv\Scripts\activate.bat
echo Installing Python dependencies...
pip install -r requirements.txt -q

:start_server
echo.
echo Starting server on http://localhost:8002
echo.
python server.py

pause
```

### `start.sh` (macOS/Linux)

```bash
#!/usr/bin/env bash
set -e

echo "========================================"
echo "  SOFP Agent — Web UI"
echo "========================================"
echo ""

# Create venv if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install Python deps
echo "Installing Python dependencies..."
pip install -r requirements.txt -q

# Check Node.js
if command -v node &> /dev/null; then
    echo "Installing frontend dependencies..."
    cd web
    npm install
    echo "Building frontend..."
    npm run build
    cd ..
else
    echo "WARNING: Node.js not found. Frontend will not be built."
    echo "Install Node.js 18+ from https://nodejs.org/"
fi

echo ""
echo "Starting server on http://localhost:8002"
echo ""

python server.py
```

### `requirements.txt`

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-dotenv>=1.0.0
python-multipart>=0.0.9
pydantic-ai>=0.0.15
openpyxl>=3.1.0
PyMuPDF>=1.24.0
openai>=1.0.0
litellm
```

---

## Vite Frontend Configuration

### `web/package.json`

```json
{
  "name": "sofp-agent-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "xlsx": "^0.18.5"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^4.0.0",
    "typescript": "^5.6.3",
    "vite": "^6.0.0"
  }
}
```

### `web/vite.config.ts`

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8002",
    },
  },
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
});
```

### `web/tailwind.config.ts`

```typescript
import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
} satisfies Config;
```

---

## Modifications to Existing Files

### `agent.py` — ONE SMALL CHANGE REQUIRED

The `create_sofp_agent()` function signature must accept a PydanticAI `Model` object (not just a string), so `server.py` can pass an `OpenAIModel` pointed at the enterprise proxy:

```python
# agent.py — change the type annotation
from pydantic_ai.models import Model

def create_sofp_agent(
    pdf_path: str,
    template_path: str,
    model: str | Model = "google-gla:gemini-3-flash-preview",  # ← accept Model object
    output_dir: str = "experiments/sofp-agent/output",
    cache_template: bool = False,
) -> tuple[Agent[AgentDeps, str], AgentDeps]:
    # ... rest unchanged — Agent() already accepts both str and Model
```

The CLI (`run.py`) continues to pass a string like `"google-gla:gemini-3-flash-preview"` (works on Mac with direct API access). The server passes an `OpenAIModel` object (works on enterprise Windows via proxy). Same agent code, two model backends.

The event interception via `agent._function_tools` wrapping requires no other modifications to `agent.py`.

**Future improvement (optional)**: Add an optional `event_callback` parameter to `AgentDeps.__init__` for a cleaner integration:

```python
class AgentDeps:
    def __init__(self, ..., event_callback: Callable[[str, dict], None] | None = None):
        self.event_callback = event_callback
```

Then in each tool, after `_track_turn`:
```python
if ctx.deps.event_callback:
    ctx.deps.event_callback("tool_call", {"tool_name": "read_template", "args": {}})
```

### `run.py` — NO CHANGES

The CLI entry point is completely unaffected.

### `token_tracker.py` — NO CHANGES

The polling approach reads `TokenReport` as-is.

### `tools/` — NO CHANGES

All tools remain unchanged.

---

## Session Management

Sessions are file-based with UUID names:

```
output/
├── a1b2c3d4-e5f6-.../
│   ├── uploaded.pdf          # user's uploaded PDF
│   ├── filled.xlsx           # agent output
│   ├── result.json           # agent output
│   ├── cost_report.txt       # agent output
│   ├── conversation_trace.json  # agent output
│   └── images/               # rendered pages (if any)
├── f7g8h9i0-j1k2-.../
│   └── ...
```

- Session ID is a UUID4 generated on upload
- No database — filesystem is the source of truth
- Old sessions can be cleaned up manually or via a cron job
- Concurrent runs: each session has its own thread and event queue

### Active Runs Tracking

```python
# server.py — global state
active_runs: dict[str, EventQueue] = {}

# In POST /api/upload:
session_id = str(uuid.uuid4())
session_dir = OUTPUT_DIR / session_id
session_dir.mkdir(parents=True)

# In GET /api/run/{session_id}:
if session_id in active_runs:
    raise HTTPException(409, "Already running")
active_runs[session_id] = EventQueue()

# In SSE generator finally block:
active_runs.pop(session_id, None)
```

---

## Error Handling

| Scenario | Backend Response | Frontend Behavior |
|----------|-----------------|-------------------|
| Upload non-PDF | 400 Bad Request | Show error toast |
| Upload too large (>50MB) | 413 Payload Too Large | Show size limit message |
| No API key configured | 400 in /api/run | Redirect to Settings |
| Agent throws exception | `error` SSE event | Red error card with traceback |
| SSE disconnects mid-run | Client reconnects with same session_id | Resume viewing events (events are not replayed, but results are still downloadable) |
| Concurrent run attempt | 409 Conflict | Show "already running" message |
| Template not found | `error` SSE event | Show error, suggest uploading template |

---

## TDD Development Cycles

Each cycle follows **Red → Green → Refactor**. Tests are written first, then code is written to pass them.

---

### Cycle 1: Event Queue (Red → Green)

**Red**: `tests/test_event_queue.py`

```python
def test_put_and_get():
    q = EventQueue()
    q.put("status", {"phase": "reading_template"})
    event = q.get(timeout=1.0)
    assert event["event"] == "status"
    assert event["data"]["phase"] == "reading_template"

def test_get_timeout_returns_none():
    q = EventQueue()
    assert q.get(timeout=0.1) is None

def test_mark_done_sends_sentinel():
    q = EventQueue()
    q.mark_done()
    assert q.get(timeout=1.0) is None
    assert q.is_done

def test_multiple_events_fifo():
    q = EventQueue()
    q.put("tool_call", {"tool_name": "read_template"})
    q.put("tool_result", {"tool_name": "read_template", "duration_ms": 50})
    assert q.get(timeout=1.0)["event"] == "tool_call"
    assert q.get(timeout=1.0)["event"] == "tool_result"
```

**Green**: Implement `EventQueue` class in `server.py`
- Thread-safe `queue.Queue` wrapper
- `put(event_type, data)` — adds timestamp
- `get(timeout)` — returns event dict or None
- `mark_done()` — sets flag, pushes sentinel
- `is_done` property

**Verify**: `pytest tests/test_event_queue.py -v` — 4/4 pass

---

### Cycle 2: Tool Event Wrapper (Red → Green)

**Red**: `tests/test_tool_wrapper.py`

```python
def test_wrapper_emits_tool_call_and_result():
    q = EventQueue()
    def dummy(x, y):
        return x + y
    wrapped = make_event_emitting_tool(dummy, q, {})
    result = wrapped(3, 4)
    assert result == 7
    call_event = q.get(timeout=1.0)
    assert call_event["event"] == "tool_call"
    assert call_event["data"]["tool_name"] == "dummy"
    result_event = q.get(timeout=1.0)
    assert result_event["event"] == "tool_result"
    assert result_event["data"]["duration_ms"] >= 0

def test_wrapper_emits_status_for_mapped_tools():
    q = EventQueue()
    def read_template():
        return "fields: 50"
    wrapped = make_event_emitting_tool(read_template, q, {"read_template": "reading_template"})
    wrapped()
    status_event = q.get(timeout=1.0)
    assert status_event["event"] == "status"
    assert status_event["data"]["phase"] == "reading_template"

def test_wrapper_emits_error_on_exception():
    q = EventQueue()
    def failing():
        raise ValueError("boom")
    wrapped = make_event_emitting_tool(failing, q, {})
    with pytest.raises(ValueError, match="boom"):
        wrapped()
    result_event = q.get(timeout=1.0)
    assert result_event["event"] == "tool_result"
    assert "Error: boom" in result_event["data"]["result_summary"]

def test_wrapper_strips_binary_from_args():
    q = EventQueue()
    def with_binary(data: bytes):
        return len(data)
    wrapped = make_event_emitting_tool(with_binary, q, {})
    wrapped(b"hello")
    call_event = q.get(timeout=1.0)
    assert "data" not in call_event["data"]["args"]
```

**Green**: Implement `make_event_emitting_tool()` in `server.py`
- Wraps any callable
- Emits `tool_call` before execution (strips binary args)
- Emits `tool_result` after (with duration and summary)
- Emits `status` if tool name is in phase_map
- Re-raises exceptions after emitting error result

**Verify**: `pytest tests/test_tool_wrapper.py -v` — 4/4 pass

---

### Cycle 3: Token Poller (Red → Green)

**Red**: `tests/test_token_poller.py`

```python
def test_poller_emits_on_new_turns():
    q = EventQueue()
    report = TokenReport()
    thread = threading.Thread(target=_poll_tokens, args=(report, q, 0.1), daemon=True)
    thread.start()
    # Add a turn
    report.add_turn(TurnRecord(turn=1, tool_name="read_template", prompt_tokens=100,
                                completion_tokens=50, total_tokens=150, thinking_tokens=0,
                                cumulative_tokens=150, duration_ms=10, timestamp=time.time()))
    time.sleep(0.3)
    event = q.get(timeout=1.0)
    assert event["event"] == "token_update"
    assert event["data"]["prompt_tokens"] == 100
    assert event["data"]["cost_estimate"] > 0
    q.mark_done()
    thread.join(timeout=2.0)

def test_poller_does_not_emit_duplicate():
    q = EventQueue()
    report = TokenReport()
    thread = threading.Thread(target=_poll_tokens, args=(report, q, 0.1), daemon=True)
    thread.start()
    report.add_turn(TurnRecord(turn=1, tool_name="read_template", prompt_tokens=100,
                                completion_tokens=50, total_tokens=150, thinking_tokens=0,
                                cumulative_tokens=150, duration_ms=10, timestamp=time.time()))
    time.sleep(0.5)
    # Should only get ONE token_update event
    events = []
    while True:
        e = q.get(timeout=0.2)
        if e is None:
            break
        if e["event"] == "token_update":
            events.append(e)
    assert len(events) == 1
    q.mark_done()
    thread.join(timeout=2.0)
```

**Green**: Implement `_poll_tokens()` in `server.py`
- Polls `TokenReport` at configurable interval
- Tracks `last_count` to avoid duplicate emissions
- Emits `token_update` with all token counts + cost estimate
- Stops when `event_queue.is_done`

**Verify**: `pytest tests/test_token_poller.py -v` — 2/2 pass

---

### Cycle 4: Settings Endpoints (Red → Green)

**Red**: `tests/test_settings_api.py`

```python
from fastapi.testclient import TestClient
from server import app, ENV_FILE

client = TestClient(app)

def test_get_settings_default(tmp_path, monkeypatch):
    """Returns defaults when no .env exists."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "vertex_ai.gemini-3-flash-preview"
    assert data["api_key_set"] is False
    assert "proxy_url" in data

def test_post_settings_writes_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    resp = client.post("/api/settings", json={
        "model": "vertex_ai.gemini-3-flash-preview",
        "api_key": "test-key-123",
        "proxy_url": "https://genai-sharedservice-emea.pwc.com",
    })
    assert resp.status_code == 200
    assert env_file.exists()
    content = env_file.read_text()
    assert "TEST_MODEL" in content
    assert "GOOGLE_API_KEY=test-key-123" in content
    assert "LLM_PROXY_URL=https://genai-sharedservice-emea.pwc.com" in content

def test_get_settings_shows_masked_key(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_API_KEY=abcdef1234567890abcdef\nTEST_MODEL=vertex_ai.gemini-3-flash-preview\nLLM_PROXY_URL=https://proxy.example.com\n")
    monkeypatch.setattr(server, "ENV_FILE", env_file)
    monkeypatch.setattr(server, "_env_cache", {})  # clear cache
    resp = client.get("/api/settings")
    data = resp.json()
    assert data["api_key_set"] is True
    assert "abcdef" not in data["api_key_preview"] or "..." in data["api_key_preview"]
```

**Green**: Implement `/api/settings` GET and POST in `server.py`
- GET reads from `.env`, masks API key, returns available models
- POST writes to `.env` using `dotenv.set_key`, reloads env
- Uses `ENV_FILE` constant (overridable for testing)

**Verify**: `pytest tests/test_settings_api.py -v` — 3/3 pass

---

### Cycle 5: Upload Endpoint (Red → Green)

**Red**: `tests/test_upload_api.py`

```python
from fastapi.testclient import TestClient
from server import app, OUTPUT_DIR
import io

client = TestClient(app)

def test_upload_pdf_creates_session(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    
    pdf_content = b"%PDF-1.4 fake pdf content"
    resp = client.post("/api/upload", files={"file": ("test.pdf", io.BytesIO(pdf_content), "application/pdf")})
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data["filename"] == "test.pdf"
    
    # Verify file saved
    session_dir = output_dir / data["session_id"]
    assert (session_dir / "uploaded.pdf").exists()

def test_upload_rejects_non_pdf(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    
    resp = client.post("/api/upload", files={"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")})
    assert resp.status_code == 400

def test_upload_rejects_oversized_file(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "MAX_UPLOAD_SIZE", 1024)  # 1KB for test
    
    large_content = b"x" * (2 * 1024)
    resp = client.post("/api/upload", files={"file": ("big.pdf", io.BytesIO(large_content), "application/pdf")})
    assert resp.status_code == 413
```

**Green**: Implement `POST /api/upload` in `server.py`
- Validates `.pdf` extension and content-type
- Enforces `MAX_UPLOAD_SIZE` (50MB default)
- Generates UUID4 session ID
- Saves to `output/{session_id}/uploaded.pdf`
- Returns `{session_id, filename}`

**Verify**: `pytest tests/test_upload_api.py -v` — 3/3 pass

---

### Cycle 6: SSE Endpoint (Red → Green)

**Red**: `tests/test_sse_api.py`

```python
import json

def test_sse_streams_events(tmp_path, monkeypatch):
    """SSE endpoint streams events from a pre-populated EventQueue."""
    output_dir = tmp_path / "output"
    session_dir = output_dir / "test-session"
    session_dir.mkdir(parents=True)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    
    # Pre-populate event queue and active_runs
    from server import EventQueue, active_runs
    eq = EventQueue()
    active_runs["test-session"] = eq
    
    # Feed events in background thread
    def feed_events():
        time.sleep(0.1)
        eq.put("status", {"phase": "reading_template", "message": "Starting..."})
        eq.put("tool_call", {"tool_name": "read_template", "args": {}})
        eq.put("tool_result", {"tool_name": "read_template", "result_summary": "50 fields", "duration_ms": 100})
        eq.put("complete", {"success": True, "output_path": "", "excel_path": "", "trace_path": "", "total_tokens": 5000, "cost": 0.003})
        eq.mark_done()
    
    threading.Thread(target=feed_events, daemon=True).start()
    
    resp = client.get("/api/run/test-session")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    
    lines = resp.text.strip().split("\n\n")
    events = []
    for block in lines:
        event_line = [l for l in block.split("\n") if l.startswith("event:")]
        data_line = [l for l in block.split("\n") if l.startswith("data:")]
        if event_line and data_line:
            events.append({
                "event": event_line[0].replace("event: ", ""),
                "data": json.loads(data_line[0].replace("data: ", "")),
            })
    
    assert len(events) == 4
    assert events[0]["event"] == "status"
    assert events[1]["event"] == "tool_call"
    assert events[2]["event"] == "tool_result"
    assert events[3]["event"] == "complete"

def test_sse_rejects_missing_pdf():
    resp = client.get("/api/run/nonexistent-session")
    assert resp.status_code == 404

def test_sse_rejects_concurrent_run(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    session_dir = output_dir / "dup-session"
    session_dir.mkdir(parents=True)
    (session_dir / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    
    # Simulate already running
    from server import EventQueue, active_runs
    active_runs["dup-session"] = EventQueue()
    
    resp = client.get("/api/run/dup-session")
    assert resp.status_code == 409
    active_runs.pop("dup-session", None)  # cleanup
```

**Green**: Implement `GET /api/run/{session_id}` SSE endpoint
- Validates session exists and PDF is present
- Rejects if already running (409)
- Starts background thread with agent
- Streams events via `StreamingResponse` with `text/event-stream`
- Cleans up `active_runs` on disconnect

**Verify**: `pytest tests/test_sse_api.py -v` — 3/3 pass

---

### Cycle 7: File Download Endpoints (Red → Green)

**Red**: `tests/test_download_api.py`

```python
def test_download_filled_excel(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    session_dir = output_dir / "sess1"
    session_dir.mkdir(parents=True)
    (session_dir / "filled.xlsx").write_bytes(b"fake excel content")
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    
    resp = client.get("/api/result/sess1/filled.xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert resp.content == b"fake excel content"

def test_download_result_json(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    session_dir = output_dir / "sess2"
    session_dir.mkdir(parents=True)
    (session_dir / "result.json").write_text('{"fields": []}')
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    
    resp = client.get("/api/result/sess2/result.json")
    assert resp.status_code == 200
    assert resp.json() == {"fields": []}

def test_download_missing_file_returns_404(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    
    resp = client.get("/api/result/nope/filled.xlsx")
    assert resp.status_code == 404
```

**Green**: Implement download endpoints
- `GET /api/result/{session_id}/filled.xlsx`
- `GET /api/result/{session_id}/result.json`
- `GET /api/result/{session_id}/trace.json`
- Returns 404 if file doesn't exist
- Correct `Content-Type` headers

**Verify**: `pytest tests/test_download_api.py -v` — 3/3 pass

---

### Cycle 8: Frontend Scaffolding + Types (Red → Green)

**Red**: `web/src/lib/__tests__/types.test.ts` (TypeScript compilation as test)

```typescript
// This test verifies all types compile and are usable
import type { SSEEvent, StatusData, ToolCallData, TokenData, CompleteData } from "../types";

test("SSEEvent types are correctly discriminated", () => {
  const statusEvent: SSEEvent = {
    event: "status",
    data: { phase: "reading_template", message: "Starting..." } as StatusData,
    timestamp: 1234567890,
  };
  expect(statusEvent.event).toBe("status");

  const tokenEvent: SSEEvent = {
    event: "token_update",
    data: { prompt_tokens: 100, completion_tokens: 50, thinking_tokens: 0, cumulative: 150, cost_estimate: 0.001 } as TokenData,
    timestamp: 1234567890,
  };
  expect(tokenEvent.data.cumulative).toBe(150);
});
```

**Green**: Set up Vite + React + TypeScript + Tailwind
- `web/package.json` with dependencies
- `web/vite.config.ts` with `/api` proxy
- `web/tsconfig.json`
- `web/tailwind.config.ts`
- `web/src/lib/types.ts` — all TypeScript interfaces
- `web/src/index.css` — Tailwind directives

**Verify**: `cd web && npm run build` succeeds, `npm test` passes

---

### Cycle 9: SSE Client Library (Red → Green)

**Red**: `web/src/lib/__tests__/sse.test.ts`

```typescript
import { createSSE } from "../sse";
import type { SSEEvent } from "../types";

test("createSSE calls onEvent for each received event", async () => {
  const events: SSEEvent[] = [];
  let done = false;

  // Mock EventSource for test environment
  global.EventSource = class MockEventSource {
    listeners: Record<string, Function[]> = {};
    onerror: Function | null = null;

    constructor(public url: string) {}

    addEventListener(type: string, fn: Function) {
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(fn);
    }

    close() {}

    // Simulate receiving events
    _emit(type: string, data: string) {
      this.listeners[type]?.forEach(fn => fn({ data }));
    }

    _triggerError() {
      this.onerror?.();
    }
  } as any;

  const controller = createSSE(
    "test-session",
    (e) => events.push(e),
    () => { done = true; },
    () => {},
  );

  // Simulate server sending events
  const mockES = global.EventSource as any;
  const instance = new mockES("/api/run/test-session");
  instance._emit("status", JSON.stringify({ phase: "reading_template", message: "Start" }));
  instance._emit("complete", JSON.stringify({ success: true, output_path: "", excel_path: "", trace_path: "", total_tokens: 100, cost: 0.001 }));

  expect(events).toHaveLength(2);
  expect(events[0].event).toBe("status");
  expect(events[1].event).toBe("complete");
  expect(done).toBe(true);

  controller.abort();
});
```

**Green**: Implement `web/src/lib/sse.ts`
- Wraps browser `EventSource`
- Registers listeners for all 6 event types
- Calls `onEvent`, `onDone`, `onError` callbacks
- Returns `AbortController` for cleanup

**Verify**: `cd web && npm test` — passes

---

### Cycle 10: API Client Library (Red → Green)

**Red**: `web/src/lib/__tests__/api.test.ts`

```typescript
import { uploadPdf, getSettings, updateSettings } from "../api";

// Mock fetch
global.fetch = vi.fn();

test("uploadPdf sends FormData and returns session", async () => {
  const file = new File(["%PDF-1.4"], "test.pdf", { type: "application/pdf" });
  (fetch as any).mockResolvedValueOnce({
    ok: true,
    json: async () => ({ session_id: "abc123", filename: "test.pdf" }),
  });

  const result = await uploadPdf(file);
  expect(result.session_id).toBe("abc123");
  expect(fetch).toHaveBeenCalledWith("/api/upload", expect.objectContaining({
    method: "POST",
    body: expect.any(FormData),
  }));
});

test("getSettings returns config", async () => {
  (fetch as any).mockResolvedValueOnce({
    ok: true,
    json: async () => ({ model: "google-gla:gemini-3-flash-preview", api_key_set: false, api_key_preview: "", available_models: [] }),
  });

  const settings = await getSettings();
  expect(settings.api_key_set).toBe(false);
});

test("updateSettings POSTs new config", async () => {
  (fetch as any).mockResolvedValueOnce({ ok: true, json: async () => ({ status: "ok" }) });
  await updateSettings({ api_key: "new-key", model: "google-gla:gemini-3.1-pro-preview" });
  expect(fetch).toHaveBeenCalledWith("/api/settings", expect.objectContaining({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: "new-key", model: "google-gla:gemini-3.1-pro-preview" }),
  }));
});
```

**Green**: Implement `web/src/lib/api.ts`
- `uploadPdf(file: File)` → `POST /api/upload`
- `getSettings()` → `GET /api/settings`
- `updateSettings(body)` → `POST /api/settings`

**Verify**: `cd web && npm test` — passes

---

### Cycle 11: App State Machine (Red → Green)

**Red**: `web/src/__tests__/appReducer.test.ts`

```typescript
import { appReducer, initialState } from "../App";

test("UPLOADED sets sessionId and filename", () => {
  const state = appReducer(initialState, {
    type: "UPLOADED",
    payload: { sessionId: "abc", filename: "test.pdf" },
  });
  expect(state.sessionId).toBe("abc");
  expect(state.filename).toBe("test.pdf");
  expect(state.isRunning).toBe(false);
});

test("RUN_STARTED sets isRunning", () => {
  const withSession = appReducer(initialState, {
    type: "UPLOADED",
    payload: { sessionId: "abc", filename: "test.pdf" },
  });
  const state = appReducer(withSession, { type: "RUN_STARTED" });
  expect(state.isRunning).toBe(true);
});

test("EVENT accumulates events and updates derived state", () => {
  const withSession = appReducer(initialState, {
    type: "UPLOADED",
    payload: { sessionId: "abc", filename: "test.pdf" },
  });
  const running = appReducer(withSession, { type: "RUN_STARTED" });

  const withStatus = appReducer(running, {
    type: "EVENT",
    payload: { event: "status", data: { phase: "reading_template", message: "Start" }, timestamp: 1 },
  });
  expect(withStatus.currentPhase).toBe("reading_template");
  expect(withStatus.events).toHaveLength(1);

  const withTokens = appReducer(withStatus, {
    type: "EVENT",
    payload: { event: "token_update", data: { prompt_tokens: 100, completion_tokens: 50, thinking_tokens: 0, cumulative: 150, cost_estimate: 0.001 }, timestamp: 2 },
  });
  expect(withTokens.tokens?.cumulative).toBe(150);

  const withComplete = appReducer(withTokens, {
    type: "EVENT",
    payload: { event: "complete", data: { success: true, output_path: "", excel_path: "", trace_path: "", total_tokens: 5000, cost: 0.003 }, timestamp: 3 },
  });
  expect(withComplete.isComplete).toBe(true);
  expect(withComplete.isRunning).toBe(false);
});

test("EVENT with error sets hasError and stops running", () => {
  const running = appReducer(initialState, { type: "UPLOADED", payload: { sessionId: "abc", filename: "test.pdf" } });
  const state = appReducer(running, {
    type: "EVENT",
    payload: { event: "error", data: { message: "API key invalid", traceback: "" }, timestamp: 1 },
  });
  expect(state.hasError).toBe(true);
  expect(state.isRunning).toBe(false);
});

test("RESET clears all state", () => {
  const state = appReducer(initialState, { type: "UPLOADED", payload: { sessionId: "abc", filename: "test.pdf" } });
  const reset = appReducer(state, { type: "RESET" });
  expect(reset.sessionId).toBeNull();
  expect(reset.events).toHaveLength(0);
});
```

**Green**: Implement `appReducer` and `initialState` in `web/src/App.tsx`
- `UPLOADED`, `RUN_STARTED`, `EVENT`, `RESET` actions
- Derived state: `currentPhase`, `tokens`, `error`, `complete`
- Export for component consumption

**Verify**: `cd web && npm test` — passes

---

### Cycle 12: UploadPanel Component (Red → Green)

**Red**: `web/src/components/__tests__/UploadPanel.test.tsx`

```typescript
import { render, screen, fireEvent } from "@testing-library/react";
import { UploadPanel } from "../UploadPanel";

test("shows upload zone initially", () => {
  render(<UploadPanel onUpload={vi.fn()} isRunning={false} />);
  expect(screen.getByText(/drop.*pdf/i)).toBeInTheDocument();
});

test("calls onUpload when file is selected", async () => {
  const onUpload = vi.fn().mockResolvedValue({ session_id: "abc", filename: "test.pdf" });
  render(<UploadPanel onUpload={onUpload} isRunning={false} />);
  
  const file = new File(["%PDF-1.4"], "test.pdf", { type: "application/pdf" });
  const input = screen.getByLabelText(/upload/i) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
  
  await vi.waitFor(() => {
    expect(onUpload).toHaveBeenCalledWith(file);
  });
});

test("shows filename after upload", async () => {
  const onUpload = vi.fn().mockResolvedValue({ session_id: "abc", filename: "test.pdf" });
  render(<UploadPanel onUpload={onUpload} isRunning={false} />);
  
  const file = new File(["%PDF-1.4"], "test.pdf", { type: "application/pdf" });
  const input = screen.getByLabelText(/upload/i) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
  
  await vi.waitFor(() => {
    expect(screen.getByText("test.pdf")).toBeInTheDocument();
  });
});

test("disables upload while running", () => {
  render(<UploadPanel onUpload={vi.fn()} isRunning={true} />);
  expect(screen.getByLabelText(/upload/i)).toBeDisabled();
});
```

**Green**: Implement `UploadPanel.tsx`
- Hidden file input + styled drop zone
- `onChange` handler calls `onUpload(file)`
- Shows filename after success
- Disabled state when `isRunning`

**Verify**: `cd web && npm test` — passes

---

### Cycle 13: LiveFeed Component (Red → Green)

**Red**: `web/src/components/__tests__/LiveFeed.test.tsx`

```typescript
import { render, screen } from "@testing-library/react";
import { LiveFeed } from "../LiveFeed";

test("renders status events with colored badge", () => {
  const events = [
    { event: "status", data: { phase: "reading_template", message: "Starting..." }, timestamp: 1 } as SSEEvent,
  ];
  render(<LiveFeed events={events} />);
  expect(screen.getByText("reading_template")).toBeInTheDocument();
});

test("renders tool_call events with args", () => {
  const events = [
    { event: "tool_call", data: { tool_name: "view_pdf_pages", args: { pages: [1, 2, 3] } }, timestamp: 1 } as SSEEvent,
  ];
  render(<LiveFeed events={events} />);
  expect(screen.getByText("view_pdf_pages")).toBeInTheDocument();
  expect(screen.getByText(/\[1, 2, 3\]/)).toBeInTheDocument();
});

test("renders tool_result with duration badge", () => {
  const events = [
    { event: "tool_result", data: { tool_name: "read_template", result_summary: "50 fields", duration_ms: 120 }, timestamp: 1 } as SSEEvent,
  ];
  render(<LiveFeed events={events} />);
  expect(screen.getByText("120ms")).toBeInTheDocument();
});

test("auto-scrolls to bottom on new events", () => {
  const events = Array.from({ length: 20 }, (_, i) =>
    ({ event: "status", data: { phase: "reading_template", message: `Event ${i}` }, timestamp: i }) as SSEEvent,
  );
  const { container } = render(<LiveFeed events={events} />);
  const feed = container.firstChild as HTMLElement;
  expect(feed.scrollTop).toBeGreaterThan(0);
});
```

**Green**: Implement `LiveFeed.tsx`
- Scrollable container, auto-scroll via `useEffect` + ref
- Collapsible event cards by type
- Color-coded badges (status=blue, tool=purple, result=green, error=red)
- Duration badge on tool_result

**Verify**: `cd web && npm test` — passes

---

### Cycle 14: TokenDashboard Component (Red → Green)

**Red**: `web/src/components/__tests__/TokenDashboard.test.tsx`

```typescript
import { render, screen } from "@testing-library/react";
import { TokenDashboard } from "../TokenDashboard";

test("shows all token metrics", () => {
  const tokens = { prompt_tokens: 1000, completion_tokens: 500, thinking_tokens: 200, cumulative: 1700, cost_estimate: 0.0008 };
  render(<TokenDashboard tokens={tokens} />);
  expect(screen.getByText("1,000")).toBeInTheDocument();  // prompt
  expect(screen.getByText("500")).toBeInTheDocument();    // completion
  expect(screen.getByText("200")).toBeInTheDocument();    // thinking
  expect(screen.getByText("1,700")).toBeInTheDocument();  // cumulative
});

test("shows cost estimate", () => {
  const tokens = { prompt_tokens: 1000, completion_tokens: 500, thinking_tokens: 200, cumulative: 1700, cost_estimate: 0.0045 };
  render(<TokenDashboard tokens={tokens} />);
  expect(screen.getByText("$0.0045")).toBeInTheDocument();
});

test("shows placeholder when no data", () => {
  render(<TokenDashboard tokens={null} />);
  expect(screen.getByText(/waiting/i)).toBeInTheDocument();
});
```

**Green**: Implement `TokenDashboard.tsx`
- Four metric cards in a row
- Cost estimate prominently displayed
- Placeholder state when `tokens` is null
- Number formatting with commas

**Verify**: `cd web && npm test` — passes

---

### Cycle 15: ResultsPanel Component (Red → Green)

**Red**: `web/src/components/__tests__/ResultsPanel.test.tsx`

```typescript
import { render, screen } from "@testing-library/react";
import { ResultsPanel } from "../ResultsPanel";

test("shows download buttons when complete", () => {
  const complete = { success: true, output_path: "/out/result.json", excel_path: "/out/filled.xlsx", trace_path: "/out/trace.json", total_tokens: 5000, cost: 0.003 };
  render(<ResultsPanel complete={complete} sessionId="abc" />);
  expect(screen.getByText(/download.*excel/i)).toBeInTheDocument();
  expect(screen.getByText(/download.*json/i)).toBeInTheDocument();
  expect(screen.getByText(/download.*trace/i)).toBeInTheDocument();
});

test("shows summary card with tokens and cost", () => {
  const complete = { success: true, output_path: "", excel_path: "", trace_path: "", total_tokens: 5000, cost: 0.003 };
  render(<ResultsPanel complete={complete} sessionId="abc" />);
  expect(screen.getByText("5,000")).toBeInTheDocument();
  expect(screen.getByText("$0.0030")).toBeInTheDocument();
});

test("shows error state when success is false", () => {
  const complete = { success: false, output_path: "", excel_path: "", trace_path: "", total_tokens: 0, cost: 0 };
  render(<ResultsPanel complete={complete} sessionId="abc" />);
  expect(screen.getByText(/extraction failed/i)).toBeInTheDocument();
});
```

**Green**: Implement `ResultsPanel.tsx`
- Download buttons linking to `/api/result/{session_id}/{file}`
- Summary card: total tokens, cost, success/failure
- Excel preview via SheetJS (optional, can be deferred)
- JSON viewer (collapsible tree)

**Verify**: `cd web && npm test` — passes

---

### Cycle 16: SettingsModal Component (Red → Green)

**Red**: `web/src/components/__tests__/SettingsModal.test.tsx`

```typescript
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SettingsModal } from "../SettingsModal";

test("shows current settings when opened", async () => {
  const getSettings = vi.fn().mockResolvedValue({
    model: "vertex_ai.gemini-3-flash-preview",
    proxy_url: "https://genai-sharedservice-emea.pwc.com",
    api_key_set: true,
    api_key_preview: "sk-xXNO2...xyz",
  });
  render(<SettingsModal isOpen={true} getSettings={getSettings} saveSettings={vi.fn()} onClose={vi.fn()} />);
  await waitFor(() => {
    expect(screen.getByDisplayValue(/genai-sharedservice/i)).toBeInTheDocument();
  });
});

test("saves settings on submit", async () => {
  const saveSettings = vi.fn().mockResolvedValue({ status: "ok" });
  render(<SettingsModal isOpen={true} getSettings={vi.fn().mockResolvedValue({ model: "vertex_ai.gemini-3-flash-preview", proxy_url: "https://genai-sharedservice-emea.pwc.com", api_key_set: false, api_key_preview: "" })} saveSettings={saveSettings} onClose={vi.fn()} />);
  
  const input = await screen.findByPlaceholderText(/api key/i);
  fireEvent.change(input, { target: { value: "new-key-123" } });
  
  const saveBtn = screen.getByText(/save/i);
  fireEvent.click(saveBtn);
  
  await waitFor(() => {
    expect(saveSettings).toHaveBeenCalledWith(expect.objectContaining({ api_key: "new-key-123" }));
  });
});

test("does not show when closed", () => {
  render(<SettingsModal isOpen={false} getSettings={vi.fn()} saveSettings={vi.fn()} onClose={vi.fn()} />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
```

**Green**: Implement `SettingsModal.tsx`
- Modal with backdrop, closes on Escape/backdrop click
- Fetches settings on open
- API key input (masked), model dropdown
- Save button calls `saveSettings`, shows confirmation

**Verify**: `cd web && npm test` — passes

---

### Cycle 17: App Integration (Red → Green)

**Red**: `web/src/__tests__/App.integration.test.tsx`

```typescript
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import App from "../App";

// Mock all API calls
vi.mock("../lib/api", () => ({
  uploadPdf: vi.fn().mockResolvedValue({ session_id: "test-123", filename: "test.pdf" }),
  getSettings: vi.fn().mockResolvedValue({ model: "vertex_ai.gemini-3-flash-preview", proxy_url: "https://genai-sharedservice-emea.pwc.com", api_key_set: true, api_key_preview: "sk-...xyz" }),
  updateSettings: vi.fn().mockResolvedValue({ status: "ok" }),
}));

vi.mock("../lib/sse", () => ({
  createSSE: vi.fn((sessionId, onEvent, onDone) => {
    // Simulate a complete run
    setTimeout(() => {
      onEvent({ event: "status", data: { phase: "reading_template", message: "Start" }, timestamp: 1 });
      onEvent({ event: "tool_call", data: { tool_name: "read_template", args: {} }, timestamp: 2 });
      onEvent({ event: "tool_result", data: { tool_name: "read_template", result_summary: "50 fields", duration_ms: 100 }, timestamp: 3 });
      onEvent({ event: "complete", data: { success: true, output_path: "", excel_path: "", trace_path: "", total_tokens: 5000, cost: 0.003 }, timestamp: 4 });
      onDone();
    }, 50);
    return { abort: vi.fn() };
  }),
}));

test("full flow: upload → run → see results", async () => {
  render(<App />);
  
  // Upload file
  const file = new File(["%PDF-1.4"], "test.pdf", { type: "application/pdf" });
  const input = screen.getByLabelText(/upload/i) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
  
  // Wait for upload to complete
  await waitFor(() => {
    expect(screen.getByText("test.pdf")).toBeInTheDocument();
  });
  
  // Click run
  const runBtn = screen.getByText(/run extraction/i);
  fireEvent.click(runBtn);
  
  // Wait for events to appear
  await waitFor(() => {
    expect(screen.getByText("reading_template")).toBeInTheDocument();
  });
  await waitFor(() => {
    expect(screen.getByText("read_template")).toBeInTheDocument();
  });
  
  // Wait for completion
  await waitFor(() => {
    expect(screen.getByText(/download.*excel/i)).toBeInTheDocument();
  });
});
```

**Green**: Wire everything together in `App.tsx`
- `useReducer` state machine
- Upload → shows filename → enables "Run Extraction"
- Click run → opens SSE → streams events
- Complete → shows ResultsPanel
- Settings gear icon in header

**Verify**: `cd web && npm test` — passes

---

### Cycle 18: Startup Scripts + Config (Red → Green)

**Red**: `tests/test_startup_config.py`

```python
def test_env_example_exists():
    assert Path(".env.example").exists()

def test_env_example_has_required_keys():
    content = Path(".env.example").read_text()
    assert "GOOGLE_API_KEY" in content
    assert "LLM_PROXY_URL" in content
    assert "TEST_MODEL" in content
    assert "PORT" in content

def test_requirements_txt_has_fastapi():
    content = Path("requirements.txt").read_text()
    assert "fastapi" in content.lower()
    assert "uvicorn" in content.lower()
    assert "python-dotenv" in content.lower()
    assert "python-multipart" in content.lower()
    assert "pydantic-ai" in content.lower()

def test_start_sh_is_executable():
    import stat
    mode = Path("start.sh").stat().st_mode
    assert mode & stat.S_IXUSR

def test_start_bat_exists():
    assert Path("start.bat").exists()
```

**Green**: Create config files
- `.env.example` with all required keys
- `requirements.txt` with pinned deps
- `start.sh` (chmod +x)
- `start.bat`

**Verify**: `pytest tests/test_startup_config.py -v` — 5/5 pass

---

### Cycle 19: End-to-End Integration Test (Red → Green)

**Red**: `tests/test_e2e.py`

```python
from fastapi.testclient import TestClient
from server import app, OUTPUT_DIR, active_runs, EventQueue
import io
import json
import threading
import time

def test_full_extraction_flow(tmp_path, monkeypatch):
    """Simulate a full extraction without a real LLM."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server, "ENV_FILE", tmp_path / ".env")
    
    client = TestClient(app)
    
    # 1. Upload PDF
    pdf_content = b"%PDF-1.4 fake pdf"
    resp = client.post("/api/upload", files={"file": ("test.pdf", io.BytesIO(pdf_content), "application/pdf")})
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    
    # 2. Pre-populate results (simulating agent completion)
    session_dir = output_dir / session_id
    (session_dir / "filled.xlsx").write_bytes(b"fake excel")
    (session_dir / "result.json").write_text(json.dumps({"fields": []}))
    (session_dir / "conversation_trace.json").write_text("{}")
    
    # 3. Start SSE with mock events
    eq = EventQueue()
    active_runs[session_id] = eq
    
    def feed_mock_events():
        time.sleep(0.1)
        eq.put("status", {"phase": "reading_template", "message": "Starting"})
        eq.put("tool_call", {"tool_name": "read_template", "args": {}})
        eq.put("tool_result", {"tool_name": "read_template", "result_summary": "50 fields", "duration_ms": 100})
        eq.put("token_update", {"prompt_tokens": 500, "completion_tokens": 200, "thinking_tokens": 0, "cumulative": 700, "cost_estimate": 0.0003})
        eq.put("complete", {"success": True, "output_path": str(session_dir / "result.json"), "excel_path": str(session_dir / "filled.xlsx"), "trace_path": str(session_dir / "conversation_trace.json"), "total_tokens": 5000, "cost": 0.003})
        eq.mark_done()
    
    threading.Thread(target=feed_mock_events, daemon=True).start()
    
    resp = client.get(f"/api/run/{session_id}")
    assert resp.status_code == 200
    
    # Parse events
    lines = resp.text.strip().split("\n\n")
    events = []
    for block in lines:
        event_line = [l for l in block.split("\n") if l.startswith("event:")]
        data_line = [l for l in block.split("\n") if l.startswith("data:")]
        if event_line and data_line:
            events.append({
                "event": event_line[0].replace("event: ", ""),
                "data": json.loads(data_line[0].replace("data: ", "")),
            })
    
    assert any(e["event"] == "status" for e in events)
    assert any(e["event"] == "tool_call" for e in events)
    assert any(e["event"] == "token_update" for e in events)
    assert any(e["event"] == "complete" for e in events)
    
    # 4. Download files
    resp = client.get(f"/api/result/{session_id}/filled.xlsx")
    assert resp.status_code == 200
    
    resp = client.get(f"/api/result/{session_id}/result.json")
    assert resp.status_code == 200
    assert resp.json() == {"fields": []}
    
    # 5. Settings round-trip
    resp = client.post("/api/settings", json={
        "api_key": "test-key",
        "model": "vertex_ai.gemini-3-flash-preview",
        "proxy_url": "https://genai-sharedservice-emea.pwc.com",
    })
    assert resp.status_code == 200
    
    resp = client.get("/api/settings")
    assert resp.json()["api_key_set"] is True
    assert resp.json()["proxy_url"] == "https://genai-sharedservice-emea.pwc.com"
```

**Green**: No new code — this validates the entire system works together
- All endpoints wired correctly
- Event queue flows end-to-end
- File downloads work
- Settings persist

**Verify**: `pytest tests/test_e2e.py -v` — 1/1 pass

---

## Implementation Order (Summary)

| Cycle | What | Tests | Files |
|-------|------|-------|-------|
| 1 | Event Queue | 4 | `server.py` |
| 2 | Tool Event Wrapper | 4 | `server.py` |
| 3 | Token Poller | 2 | `server.py` |
| 4 | Settings API | 3 | `server.py` |
| 5 | Upload API | 3 | `server.py` |
| 6 | SSE API | 3 | `server.py` |
| 7 | Download API | 3 | `server.py` |
| 8 | Frontend Scaffolding + Types | 1 | `web/` config files, `types.ts` |
| 9 | SSE Client | 1 | `sse.ts` |
| 10 | API Client | 3 | `api.ts` |
| 11 | App State Machine | 5 | `App.tsx` (reducer) |
| 12 | UploadPanel | 4 | `UploadPanel.tsx` |
| 13 | LiveFeed | 4 | `LiveFeed.tsx` |
| 14 | TokenDashboard | 3 | `TokenDashboard.tsx` |
| 15 | ResultsPanel | 3 | `ResultsPanel.tsx` |
| 16 | SettingsModal | 3 | `SettingsModal.tsx` |
| 17 | App Integration | 1 | `App.tsx` (full) |
| 18 | Startup Scripts + Config | 5 | `.env.example`, `requirements.txt`, `start.sh`, `start.bat` |
| 19 | End-to-End | 1 | Integration test |
| **Total** | **19 cycles** | **56 tests** | |

---

## Running the Test Suite

```bash
# Backend tests (all cycles 1-7, 18-19)
cd experiments/sofp-agent
pytest tests/test_event_queue.py tests/test_tool_wrapper.py tests/test_token_poller.py \
       tests/test_settings_api.py tests/test_upload_api.py tests/test_sse_api.py \
       tests/test_download_api.py tests/test_startup_config.py tests/test_e2e.py -v

# Frontend tests (cycles 8-17)
cd experiments/sofp-agent/web
npm test

# Full suite
pytest tests/ -v && cd web && npm test
```

---

## Estimated Implementation Effort

| Component | Lines | Complexity | Notes |
|-----------|-------|------------|-------|
| `server.py` | ~250 | Medium | SSE streaming, thread management, file serving |
| `web/src/lib/types.ts` | ~60 | Low | Straightforward type definitions |
| `web/src/lib/api.ts` | ~40 | Low | Simple fetch wrappers |
| `web/src/lib/sse.ts` | ~35 | Low | EventSource wrapper |
| `web/src/App.tsx` | ~120 | Medium | useReducer state machine, layout |
| `web/src/components/UploadPanel.tsx` | ~80 | Low | Drag-and-drop, file upload |
| `web/src/components/LiveFeed.tsx` | ~150 | Medium | Event rendering, auto-scroll |
| `web/src/components/TokenDashboard.tsx` | ~70 | Low | Metric cards, mini chart |
| `web/src/components/ResultsPanel.tsx` | ~150 | Medium | Excel preview, JSON viewer |
| `web/src/components/SettingsModal.tsx` | ~80 | Low | Form, API calls |
| Config files (package.json, vite, tailwind, tsconfig) | ~80 | Low | Boilerplate |
| Startup scripts + .env.example + requirements.txt | ~60 | Low | Boilerplate |
| **Total** | **~1,175** | | |

---

## Testing Strategy

### Unit Tests (Cycles 1-16)
Each component is tested in isolation before integration. Backend uses `pytest` with `TestClient`, frontend uses `vitest` with `@testing-library/react`.

### Integration Test (Cycle 17)
Full app flow with mocked API and SSE — verifies the state machine transitions correctly through upload → run → complete.

### End-to-End Test (Cycle 19)
Real FastAPI server with mock agent events — validates the entire pipeline from upload through SSE streaming to file downloads.

### Manual E2E (Post-implementation)
1. Start server: `python server.py`
2. Open browser: `http://localhost:8002`
3. Upload a test PDF
4. Click "Run Extraction"
5. Verify events stream in real-time
6. Verify download links work after completion
7. Verify Excel preview shows filled data

---

## Future Enhancements (Out of Scope)

- **Template upload**: Allow users to upload their own SOFP template (currently hardcoded path)
- **Session history**: List previous runs with timestamps and results
- **Cancel run**: Abort button to stop a running extraction
- **Progress estimation**: Show estimated time remaining based on token velocity
- **Multi-document comparison**: Compare SOFP extractions across periods
- **Docker support**: Containerize the web UI
- **Authentication**: Protect the UI with a password or OAuth
- **Agent visualization**: Show the agent's decision tree / reasoning graph
