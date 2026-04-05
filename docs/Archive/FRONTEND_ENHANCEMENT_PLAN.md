# SOFP Agent — Frontend Enhancement Plan v2

> **Progress: ~85% — Priorities 0-4 complete**

| Priority | Status | Description |
|----------|--------|-------------|
| P0 | 🟩 Done | Streaming Architecture + Theme Foundation |
| P1 | 🟩 Done | Pipeline Stage Indicator + Processing Animations |
| P2 | 🟩 Done | Agent Reasoning Transparency + Tool Timeline |
| P3 | 🟩 Done | Improved Settings Modal + Connection Test |
| P4 | 🟩 Done | Output/Results View |
| P5 | ⬜ Pending | Run History (Deferred/Optional) |

## Context

The XBRL Agent (this repo) extracts SOFP data from Malaysian financial statement PDFs into SSM XBRL Excel templates. It has a Vite + React frontend with 5 components: UploadPanel, LiveFeed, TokenDashboard, ResultsPanel, SettingsModal. The UI is functional but basic — no stage progression, no processing animations, a flat event log, no agent reasoning visibility, and results are just download links.

This plan adds: PwC-branded visual identity, real-time agent reasoning transparency (thinking tokens, tool decisions streamed live), richer observability, and a proper output view.

### Key Architectural Change

The current backend uses `agent.run_sync()` in a background thread with a manual `event_callback` hack on `AgentDeps`. This limits us to coarse `tool_result` events only. **This plan migrates to PydanticAI's native `agent.iter()` streaming API**, which gives us granular access to thinking tokens, tool call arguments, tool results, and text deltas — all streamed in real time.

### Constraints

- **All components use inline `style={}` props** (not Tailwind). CLAUDE.md says "don't convert back to className-based Tailwind." Existing components still using Tailwind classes must be converted when modified.
- **Red-Green TDD**: Every priority lists its test cases first (RED), then the implementation (GREEN). Tests are written before the feature code.
- **PwC "So You Can" branding**: All new and modified UI follows the brand guidelines below.

---

## PwC Brand Guidelines — SOFP Agent Theme

Based on PwC's 2025 "So You Can" rebrand. The visual identity communicates **momentum** — upward and forward trajectory — with high-contrast black/orange on white.

### Design Tokens

```typescript
// web/src/lib/theme.ts

export const pwc = {
  // Primary
  black: '#000000',
  white: '#FFFFFF',
  orange500: '#FD5108',   // Primary accent — buttons, active states, links
  orange700: '#C52B09',   // Hover/pressed state
  orange400: '#FE7C39',   // Light accent — progress bars, active indicators
  orange100: '#FFE8D4',   // Tint — backgrounds for highlighted content
  orange50:  '#FFF5ED',   // Subtle tint — hover backgrounds, empty field highlight

  // Greys
  grey50:  '#F5F7F8',     // Page background
  grey100: '#EEEFF1',     // Card backgrounds, alternating rows
  grey200: '#DFE3E6',     // Borders, dividers
  grey300: '#CBD1D6',     // Disabled text, pending step connectors
  grey500: '#A1A8B3',     // Secondary text, timestamps
  grey700: '#787E8A',     // Tertiary text
  grey800: '#4C5056',     // Body text
  grey900: '#303236',     // Headings, primary text

  // Semantic
  success: '#16A34A',     // Green — completed steps, success badges
  error:   '#DC2626',     // Red — errors, failed states
  thinking: '#7C3AED',    // Purple — agent thinking/reasoning blocks

  // Typography
  fontHeading: '"Arial", "Helvetica Neue", sans-serif',
  fontBody:    '"Arial", serif',
  fontMono:    '"SF Mono", "Fira Code", "Consolas", monospace',

  // Spacing scale (px)
  space: { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 },

  // Border radius
  radius: { sm: 4, md: 8, lg: 12 },

  // Shadows
  shadow: {
    card: '0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06)',
    elevated: '0 4px 12px rgba(0,0,0,0.1)',
    modal: '0 20px 60px rgba(0,0,0,0.2)',
  },
} as const;
```

### Visual Rules

1. **Page background**: `grey50` (`#F5F7F8`). Cards/panels: `white` with `grey200` border and `shadow.card`.
2. **Headings**: Arial sans-serif, `grey900`, `font-weight: 600`. Body text: Georgia serif, `grey800`, `font-weight: 400`.
3. **Primary buttons**: `orange500` background, white text, `radius.md`. Hover: `orange700`. Never use orange for destructive actions.
4. **Secondary buttons**: White background, `grey900` border, black text. Hover: `grey50` fill.
5. **Active/running indicators**: `orange400` pulsing dot or spinner. Completed: `success` green checkmark. Pending: `grey300`.
6. **Thinking blocks**: `thinking` purple left-border, `grey50` background, monospace font. Streams in real-time, auto-collapses when done.
7. **Tool call cards**: White card, `grey200` border. Icon + verb label. Collapsible detail section. Active card: `orange50` background with `orange500` left-border.
8. **Error states**: `error` red text/border, never orange.
9. **Monospace for data**: Token counts, tool names, field values, elapsed time — all use `fontMono`.
10. **No gradients in the app UI** — save the orange gradient for marketing. App uses flat solid colors.

### Component Style Pattern

All components use a styles object at the top of the file:

```tsx
const styles = {
  container: { background: pwc.white, borderRadius: pwc.radius.md, border: `1px solid ${pwc.grey200}`, boxShadow: pwc.shadow.card, padding: pwc.space.xl } as const,
  heading: { fontFamily: pwc.fontHeading, fontWeight: 600, color: pwc.grey900, fontSize: 18, margin: 0 } as const,
  body: { fontFamily: pwc.fontBody, color: pwc.grey800, fontSize: 15, lineHeight: 1.6 } as const,
  mono: { fontFamily: pwc.fontMono, fontSize: 13 } as const,
};
```

---

## Priority 0: Streaming Architecture + Theme Foundation

**Why first**: Everything else depends on the event stream and theme tokens. The current `run_sync()` + thread + `event_callback` pattern cannot emit thinking tokens, tool call arguments before execution, or streaming text. We must migrate to `agent.iter()` first.

### TDD — Red Phase

**`web/src/__tests__/theme.test.ts`**
```
- pwc theme object exports all required color tokens
- pwc theme object exports typography, spacing, radius, shadow tokens
- all color values are valid hex strings
```

**`web/src/__tests__/appReducer.test.ts`** (extend existing)
```
- THINKING event appends to thinkingBuffer in state
- THINKING_END event finalizes thinkingBuffer and clears it
- TOOL_CALL event adds entry to toolTimeline with start timestamp
- TOOL_RESULT event pairs with matching tool_call by tool_call_id and adds duration
- TEXT_DELTA event appends to streamingText in state
- RUN_STARTED sets runStartTime to current timestamp
- Events accumulate in order for full audit trail
```

**`tests/test_streaming.py`** (backend)
```
- iter_agent_events() yields ThinkingDelta events when model thinks
- iter_agent_events() yields ToolCall event before tool execution
- iter_agent_events() yields ToolResult event after tool execution
- iter_agent_events() yields TextDelta events for model text
- iter_agent_events() yields TokenUpdate with running totals
- SSE endpoint streams events as newline-delimited JSON
- SSE endpoint handles agent errors gracefully with error event
- SSE endpoint sends complete event with final usage stats
```

### Green Phase — Implementation

#### New: `web/src/lib/theme.ts`
- Export `pwc` design tokens object as defined above

#### Modify: `web/src/lib/types.ts`
- Add new SSE event types for the streaming architecture:

```typescript
// New event types
export type SSEEventType =
  | "status"           // Phase transitions
  | "thinking_delta"   // Streaming thinking token chunk
  | "thinking_end"     // Thinking block complete
  | "text_delta"       // Streaming model text chunk
  | "tool_call"        // Tool invocation (before execution)
  | "tool_result"      // Tool completion (after execution)
  | "token_update"     // Running token totals
  | "error"            // Agent/system error
  | "complete";        // Run finished

export interface ThinkingDeltaData {
  content: string;       // Incremental thinking text chunk
  thinking_id: string;   // Groups chunks into blocks
}

export interface ThinkingEndData {
  thinking_id: string;
  summary: string;        // One-line summary (first ~80 chars)
  full_length: number;    // Character count of full thinking block
}

export interface TextDeltaData {
  content: string;        // Incremental text chunk
}

export interface ToolCallData {
  tool_name: string;
  tool_call_id: string;   // For pairing with result
  args: Record<string, unknown>;
}

export interface ToolResultData {
  tool_name: string;
  tool_call_id: string;   // Matches the tool_call
  result_summary: string;
  duration_ms: number;
}

// Extended AppState
export interface AppState {
  sessionId: string | null;
  filename: string | null;
  isRunning: boolean;
  isComplete: boolean;
  hasError: boolean;
  events: SSEEvent[];              // Full audit trail
  currentPhase: EventPhase | null;
  tokens: TokenData | null;
  error: string | null;
  complete: CompleteData | null;
  // New fields
  runStartTime: number | null;     // P0: timestamp when run began
  thinkingBuffer: string;          // P0: accumulating thinking text
  activeThinkingId: string | null; // P0: current thinking block ID
  thinkingBlocks: ThinkingBlock[]; // P0: completed thinking blocks
  toolTimeline: ToolTimelineEntry[];// P0: paired tool_call + tool_result
  streamingText: string;           // P0: accumulating model text
}

export interface ThinkingBlock {
  id: string;
  content: string;
  summary: string;
  timestamp: number;
  phase: EventPhase | null;        // Which pipeline phase this was in
}

export interface ToolTimelineEntry {
  tool_call_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  result_summary: string | null;   // Null until tool_result arrives
  duration_ms: number | null;
  startTime: number;
  endTime: number | null;
  phase: EventPhase | null;
}

export interface ResultJsonData {
  fields: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}
```

#### Modify: `server.py` — Replace thread+callback with async streaming

The core change: replace `run_agent_in_thread()` with an async generator `iter_agent_events()` that uses PydanticAI's `agent.iter()`.

```python
# New imports
from pydantic_ai import (
    Agent, PartStartEvent, PartDeltaEvent, PartEndEvent,
    FinalResultEvent, FunctionToolCallEvent, FunctionToolResultEvent,
    TextPartDelta, ThinkingPartDelta, ToolCallPartDelta,
)

async def iter_agent_events(
    pdf_path: Path, template_path: Path, model_name: str,
    output_dir: Path, api_key: str, proxy_url: str | None,
    session_id: str,
) -> AsyncIterator[dict]:
    """Yields SSE event dicts from a PydanticAI agent.iter() run."""

    model = _create_proxy_model(model_name, api_key, proxy_url)
    agent = create_sofp_agent()
    deps = AgentDeps(pdf_path=pdf_path, template_path=template_path, ...)

    thinking_id_counter = 0
    current_thinking_id = None

    yield {"event": "status", "data": {"phase": "reading_template", "message": "Starting extraction..."}}

    async with agent.iter(SYSTEM_PROMPT, deps=deps, model=model,
                          model_settings={"thinking": True}) as run:
        async for node in run:
            if Agent.is_model_request_node(node):
                async with node.stream(run.ctx) as stream:
                    async for event in stream:
                        if isinstance(event, PartStartEvent):
                            if hasattr(event.part, 'part_kind') and event.part.part_kind == 'thinking':
                                thinking_id_counter += 1
                                current_thinking_id = f"think_{thinking_id_counter}"
                        elif isinstance(event, PartDeltaEvent):
                            if isinstance(event.delta, ThinkingPartDelta):
                                yield {"event": "thinking_delta", "data": {
                                    "content": event.delta.content_delta,
                                    "thinking_id": current_thinking_id,
                                }}
                            elif isinstance(event.delta, TextPartDelta):
                                yield {"event": "text_delta", "data": {
                                    "content": event.delta.content_delta,
                                }}
                        elif isinstance(event, PartEndEvent):
                            if current_thinking_id:
                                yield {"event": "thinking_end", "data": {
                                    "thinking_id": current_thinking_id,
                                    "summary": "...",  # first ~80 chars
                                    "full_length": 0,
                                }}
                                current_thinking_id = None

            elif Agent.is_call_tools_node(node):
                async with node.stream(run.ctx) as handle_stream:
                    async for event in handle_stream:
                        if isinstance(event, FunctionToolCallEvent):
                            yield {"event": "tool_call", "data": {
                                "tool_name": event.part.tool_name,
                                "tool_call_id": event.part.tool_call_id,
                                "args": event.part.args,
                            }}
                            # Emit phase change based on tool_name → phase mapping
                        elif isinstance(event, FunctionToolResultEvent):
                            yield {"event": "tool_result", "data": {
                                "tool_name": ...,
                                "tool_call_id": event.tool_call_id,
                                "result_summary": str(event.result.content)[:200],
                                "duration_ms": ...,
                            }}

            # Emit token_update after each node
            if run.usage:
                yield {"event": "token_update", "data": {
                    "prompt_tokens": run.usage.input_tokens,
                    "completion_tokens": run.usage.output_tokens,
                    "thinking_tokens": run.usage.details.get("thinking_tokens", 0),
                    "cumulative": run.usage.total_tokens,
                    "cost_estimate": _calc_cost(run.usage),
                }}

    # Final complete event
    yield {"event": "complete", "data": {...}}
```

The SSE endpoint becomes:

```python
@app.get("/api/run/{session_id}")
async def run_extraction(session_id: str):
    async def event_stream():
        async for evt in iter_agent_events(...):
            yield f"data: {json.dumps(evt)}\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

#### Modify: `agent.py`
- Remove the `event_callback` field from `AgentDeps` (no longer needed)
- Remove `_track_turn()` wrapper (events come from `agent.iter()` natively)
- Keep `AgentDeps` for pdf_path, template_path, output_dir, token_report, etc.
- The tools themselves remain unchanged — they are still sync functions called by PydanticAI

#### Modify: `web/src/App.tsx`
- Import `pwc` theme tokens, apply to all inline styles
- Add new reducer cases: `THINKING_DELTA`, `THINKING_END`, `TEXT_DELTA`, `TOOL_CALL` (new fields), `TOOL_RESULT` (pairing)
- Set `runStartTime` in `RUN_STARTED` action
- Update SSE parsing in `useEffect` to handle new event types
- Convert any remaining Tailwind classes to inline styles

#### Modify: `web/src/index.css`
- Remove `@import "tailwindcss"` (no longer needed)
- Add keyframe animations:
```css
@keyframes pulse-subtle {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
@keyframes fade-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes slide-down {
  from { max-height: 0; opacity: 0; }
  to { max-height: 500px; opacity: 1; }
}
```

#### Modify: `web/src/lib/api.ts`
- Improve error handling: parse JSON error bodies, surface `detail`/`message` text
```typescript
async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      detail = body.detail || body.message || detail;
    } catch { /* no JSON body */ }
    throw new Error(detail);
  }
  return res.json();
}
```

**Backend changes:** Major — `server.py` streaming rewrite, `agent.py` cleanup

---

## Priority 1: Pipeline Stage Indicator + Processing Animations

### TDD — Red Phase

**`web/src/__tests__/PipelineStages.test.tsx`**
```
- renders all 5 phases as step items
- marks completed phases with green checkmark
- marks active phase with pulsing orange dot
- marks pending phases with grey circle
- draws connector lines between steps
- shows no active phase when isRunning=false and isComplete=false
- shows all phases complete when isComplete=true
- applies PwC theme colors (orange500 active, success completed, grey300 pending)
```

**`web/src/__tests__/ElapsedTimer.test.tsx`**
```
- renders 00:00 initially
- increments display every second when isRunning=true
- stops incrementing when isRunning=false
- formats minutes and seconds with zero-padding (e.g., 02:07)
- cleans up interval on unmount
- uses monospace font from theme
```

**`web/src/__tests__/UploadPanel.test.tsx`** (extend)
```
- shows CSS spinner when isRunning=true
- shows ElapsedTimer when isRunning=true with startTime
- hides spinner and timer when isRunning=false
- upload button uses PwC orange500 background
- drag-drop zone uses grey50 background with grey200 dashed border
```

### Green Phase — Implementation

#### New: `web/src/components/PipelineStages.tsx`
- Vertical step indicator showing 5 agent phases: `reading_template` → `viewing_pdf` → `filling_workbook` → `verifying` → `complete`
- Each stage renders: numbered circle (24px), connector line (2px wide), phase label
- **Active state**: `orange400` pulsing dot (uses `pulse-subtle` keyframe), bold label in `grey900`
- **Completed state**: `success` green circle with checkmark icon (SVG), `grey700` label
- **Pending state**: `grey300` circle with number, `grey500` label
- Connector lines: `success` green for completed→completed, `grey200` for others
- Props: `currentPhase: EventPhase | null`, `isRunning: boolean`, `isComplete: boolean`
- All inline styles using `pwc` theme tokens

#### New: `web/src/components/ElapsedTimer.tsx`
- Shows `mm:ss` elapsed time using `useState` + `setInterval(1000ms)`
- Props: `startTime: number`, `isRunning: boolean`
- Uses `pwc.fontMono`, `pwc.grey700` color, 14px font size
- Cleans up interval on unmount and when `isRunning` becomes false

#### Modify: `web/src/components/UploadPanel.tsx`
- When `isRunning`: show CSS spinner (20px, `orange500` border, `spin` animation) + `ElapsedTimer`
- Replace static "Running..." text
- Apply PwC theme: `orange500` upload button, `grey50` drag zone, `grey200` dashed border
- All inline styles (already mostly inline — just apply theme tokens)

#### Modify: `web/src/App.tsx`
- Render `PipelineStages` between UploadPanel and LiveFeed when `isRunning || currentPhase`
- Pass `runStartTime` to UploadPanel as `startTime`

#### Modify: `web/src/components/TokenDashboard.tsx`
- Convert all Tailwind classes to inline styles using `pwc` theme
- Add pulsing `orange400` dot (8px circle, `pulse-subtle` animation) next to "Est. Cost" while running
- Props: add `isRunning: boolean`

**Backend changes:** None

---

## Priority 2: Agent Reasoning Transparency + Tool Timeline

This is the core UX differentiator — users see exactly what the AI agent is thinking and doing in real time. Inspired by:

- **Collapsible thinking blocks** (Claude-style): Streaming thinking text in a muted purple-bordered container. Auto-collapses when thinking ends, showing "Thought for Xs" summary. Expandable for full text.
- **Tool call cards** (Cursor/Claude Code-style): Each tool invocation gets a compact card showing the tool name as a verb ("Reading template...", "Viewing page 3..."), with collapsible args/result detail.
- **Streaming text at the insertion point** (ChatGPT-style): Model text streams token-by-token with a blinking caret, so users start reading immediately.

### TDD — Red Phase

**`web/src/__tests__/ThinkingBlock.test.tsx`**
```
- renders streaming thinking text in monospace font
- shows "Thinking..." label with purple left border while streaming
- auto-collapses when thinking_end event received
- collapsed state shows summary text and duration
- expands on click to show full thinking text
- uses pwc.thinking color for left border
- uses pwc.grey50 background
- applies fade-in animation when appearing
```

**`web/src/__tests__/ToolCallCard.test.tsx`**
```
- renders tool name as human-readable verb (read_template → "Reading template")
- shows args summary in collapsed state
- active card (no result yet) has orange50 background and orange500 left border
- completed card has grey200 border and grey100 background
- shows duration badge when result arrives
- expands on click to show full args and result_summary
- error result shows red border and error icon
```

**`web/src/__tests__/AgentFeed.test.tsx`** (replaces LiveFeed tests)
```
- renders thinking blocks, tool cards, and text in chronological order
- "Timeline" view groups tool_call + tool_result by tool_call_id
- "Raw Log" view shows flat event list (legacy behavior)
- toggle persists between rerenders
- defaults to Timeline view
- auto-scrolls to bottom on new events
- filters out token_update events
- shows phase markers as horizontal dividers with phase label
```

**`web/src/__tests__/StreamingText.test.tsx`**
```
- renders text_delta content as it arrives
- shows blinking caret at end while streaming
- removes caret when next non-text event arrives
- uses Georgia serif body font
```

### Green Phase — Implementation

#### New: `web/src/components/ThinkingBlock.tsx`
- Collapsible container for agent thinking/reasoning
- **While streaming**: Purple (`pwc.thinking`) 3px left border, `grey50` background, `fontMono` 13px text. Content streams in with no scroll — latest text visible. "Thinking..." label with pulsing purple dot.
- **When collapsed** (after `thinking_end`): Single line showing summary + "Thought for Xs" in `grey500`. Click to expand. `fade-in` animation on collapse transition.
- **When expanded**: Full thinking text, scrollable if > 200px tall, "Collapse" link at bottom.
- Props: `block: ThinkingBlock`, `isStreaming: boolean`, `streamingContent: string`

#### New: `web/src/components/ToolCallCard.tsx`
- Compact card for each tool invocation
- **Header row**: Tool icon (wrench emoji or SVG), human-readable tool name (mapped: `read_template` → "Reading template", `view_pdf_pages` → "Viewing PDF pages", `fill_workbook` → "Filling workbook", `verify_totals` → "Verifying totals", `save_result` → "Saving result"), duration badge (right-aligned, `fontMono`)
- **Active state** (awaiting result): `orange50` background, `orange500` 3px left border, spinner icon instead of duration
- **Completed state**: `white` background, `grey200` border, `success` green duration badge
- **Expandable detail**: args as key-value pairs in `fontMono`, result_summary text
- Props: `entry: ToolTimelineEntry`

#### New: `web/src/components/StreamingText.tsx`
- Renders accumulating model text with a blinking caret
- Uses `pwc.fontBody` (Georgia), `grey800`, 15px
- Caret: 2px wide, `orange500`, blinks via CSS animation
- Props: `text: string`, `isStreaming: boolean`

#### New: `web/src/components/AgentFeed.tsx` (replaces LiveFeed)
- Two-mode feed with toggle: **"Timeline"** (default) | **"Raw Log"**
- **Timeline mode**: Renders a mixed chronological stream of:
  - `ThinkingBlock` components for thinking events
  - `ToolCallCard` components for tool events (paired by `tool_call_id`)
  - `StreamingText` for model text output
  - Phase dividers (thin `grey200` line with phase label pill in center) on phase transitions
- **Raw Log mode**: Flat list of all events (current LiveFeed behavior, for debugging)
- Toggle: Two buttons in `grey100` pill, active button has `orange500` background + white text
- Auto-scrolls to bottom on new events (with user-scroll-override: if user scrolls up, pause auto-scroll; resume when they scroll to bottom)
- Convert all Tailwind → inline styles with `pwc` theme

#### Remove: `web/src/components/LiveFeed.tsx`
- Replaced by `AgentFeed.tsx`. Delete the file.

#### Modify: `web/src/App.tsx`
- Replace `<LiveFeed>` with `<AgentFeed>`
- Pass `thinkingBlocks`, `toolTimeline`, `streamingText`, `activeThinkingId`, `thinkingBuffer` to AgentFeed

**Backend changes:** None beyond P0 (streaming already emits thinking + tool events)

---

## Priority 3: Improved Settings Modal + Connection Test

### TDD — Red Phase

**`web/src/__tests__/SettingsModal.test.tsx`**
```
- validates proxy URL starts with https:// on blur
- shows inline error "Proxy URL must start with https://" for invalid URL
- validates API key minimum length (8 chars) on blur
- shows inline error "API key too short" for short keys
- validates model name is non-empty on blur
- shows inline error "Model name is required" for empty model
- disables Save button when any field has validation errors
- Enter key triggers save when form is valid
- "Test Connection" button calls testConnection API
- shows spinner during connection test
- shows green checkmark + latency on success
- shows red X + error message on failure
- helper text renders below each field
- uses PwC theme colors for validation states (error red, success green)
- error messages surface backend detail text (not generic "Failed" message)
```

**`tests/test_server.py`** (extend)
```
- POST /api/test-connection returns ok + latency_ms for valid config
- POST /api/test-connection returns error + message for invalid API key
- POST /api/test-connection returns error + message for unreachable proxy
- POST /api/test-connection uses provided settings, not .env defaults
- POST /api/test-connection logs attempt with session context
```

### Green Phase — Implementation

#### Modify: `web/src/components/SettingsModal.tsx`
- Convert all Tailwind → inline styles with `pwc` theme
- **Field validation** (on blur + on save):
  - Proxy URL: must start with `https://` → red border + error text below field
  - API key: minimum 8 characters when changed → red border + error text
  - Model name: non-empty → red border + error text
- **Helper text** below each field in `grey500`, 13px:
  - Proxy URL: "Enterprise LiteLLM proxy endpoint (must be HTTPS)"
  - API key: "From Bruno → Collection → Auth tab"
  - Model name: "e.g., vertex_ai.gemini-3-flash-preview"
- **"Test Connection" button**: Secondary button style. On click: shows spinner, calls `testConnection()`. Success: green checkmark + "{model} responded in {latency_ms}ms". Failure: red X + error message from backend.
- **Enter key**: `onKeyDown` handler on the form triggers save when valid
- **Save button**: Disabled (grey) when validation errors exist. `orange500` when valid.

#### Modify: `web/src/lib/api.ts`
- Add `testConnection(body: {proxy_url?, api_key?, model?}): Promise<{status, model?, latency_ms?, message?}>`

#### Modify: `server.py`
- Add `POST /api/test-connection` endpoint:
  ```python
  @app.post("/api/test-connection")
  async def test_connection(body: TestConnectionRequest):
      """Test LLM connectivity with provided or .env settings."""
      start = time.time()
      try:
          model = _create_proxy_model(body.model or ..., body.api_key or ..., body.proxy_url or ...)
          # Minimal completion: "Say OK"
          result = await model.complete("Say OK", max_tokens=5)
          latency_ms = int((time.time() - start) * 1000)
          return {"status": "ok", "model": body.model, "latency_ms": latency_ms}
      except Exception as e:
          logger.exception("Connection test failed", extra={"model": body.model})
          return JSONResponse(status_code=502, content={"status": "error", "message": str(e)})
  ```
- Add module-level `logger = logging.getLogger(__name__)`
- Replace all `print()` calls with `logger.info()` / `logger.error()`
- Add `session_id` context to worker exception logging

**Backend changes:** One new endpoint, structured logging migration

---

## Priority 4: Output/Results View

### TDD — Red Phase

**`web/src/__tests__/ResultsView.test.tsx`**
```
- renders 3 tabs: Summary, Data Preview, Downloads
- defaults to Summary tab
- Summary tab shows: total tokens, cost, elapsed time, field count, success badge
- Data Preview tab renders table with field name + value columns
- Data Preview tab highlights empty/null fields in orange50 background
- Data Preview tab shows loading spinner while fetching result.json
- Data Preview tab shows error state if fetch fails
- Downloads tab shows 3 download buttons (Excel, JSON, Trace)
- tab switching preserves data (no re-fetch on tab switch)
- uses PwC theme: orange500 active tab, grey200 tab border
```

**`web/src/__tests__/api.test.ts`** (extend)
```
- getResultJson returns parsed JSON for valid session
- getResultJson throws with detail message on 404
```

### Green Phase — Implementation

#### New: `web/src/components/ResultsView.tsx`
- Replaces `ResultsPanel` with a tabbed interface
- **Tab bar**: 3 tabs, active tab has `orange500` bottom border + `grey900` text, inactive has `grey500` text. Tab container has `grey200` bottom border.
- **Summary tab**: Card grid showing:
  - Total tokens (formatted with commas, `fontMono`)
  - Estimated cost (formatted as $X.XX, `fontMono`)
  - Elapsed time (mm:ss from `runStartTime` to completion, `fontMono`)
  - Fields extracted (count from result.json, `fontMono`)
  - Success badge: `success` green pill with checkmark, or `error` red pill
- **Data Preview tab**: 
  - Fetch `/api/result/{sessionId}/result.json` on first tab switch (cache result)
  - Render as two-column table: Field Name | Value
  - Empty/null values: `orange50` background row with "—" placeholder
  - Table header: `grey100` background, `grey900` text, `fontHeading`
  - Table rows: alternating `white` / `grey50`, `fontBody`
  - Loading: skeleton shimmer animation
  - Error: inline error message with retry button
- **Downloads tab**: Existing download buttons styled as PwC secondary buttons with file-type icons

#### Modify: `web/src/lib/api.ts`
- Add `getResultJson(sessionId: string): Promise<Record<string, unknown>>`

#### Modify: `web/src/App.tsx`
- Replace `<ResultsPanel>` with `<ResultsView>`
- Pass `runStartTime` for elapsed time calculation

#### Remove: `web/src/components/ResultsPanel.tsx`
- Replaced by `ResultsView.tsx`. Delete the file.

#### Modify: `web/src/lib/types.ts`
- Add `ResultJsonData` type (already defined in P0 types section)

**Backend changes:** None (result.json already in ALLOWED_DOWNLOADS)

---

## Priority 5: Run History (Deferred/Optional)

Lowest priority — the experiment is primarily single-run. Including for completeness.

### TDD — Red Phase

**`web/src/__tests__/RunHistory.test.tsx`**
```
- renders list of past sessions
- each entry shows: truncated session ID, filename, status badge, timestamp, cost
- click on entry navigates to results view
- empty state shows "No previous runs" message
- loading state shows skeleton rows
```

**`tests/test_server.py`** (extend)
```
- GET /api/sessions returns list of session summaries
- GET /api/sessions returns empty list when no output directory
- session summary includes id, filename, status, timestamp, token count
```

### Green Phase — Implementation

#### New: `web/src/components/RunHistory.tsx`
- Lists past sessions from output directory
- Each row: truncated UUID (first 8 chars), filename, status badge (success/error/unknown), timestamp, cost
- Click to load results into ResultsView
- PwC theme: `grey100` alternating rows, `orange500` hover highlight

#### Modify: `server.py`
- Add `GET /api/sessions` endpoint scanning output directory for session folders
- Return `[{id, filename, status, timestamp, total_tokens, cost}]`

#### Modify: `web/src/lib/api.ts`
- Add `listSessions(): Promise<SessionSummary[]>`

#### Modify: `web/src/lib/types.ts`
- Add `SessionSummary` interface

**Backend changes:** One new endpoint

---

## File Change Summary

| File | Action | Priority | Notes |
|------|--------|----------|-------|
| `web/src/lib/theme.ts` | **Create** | P0 | PwC design tokens |
| `web/src/lib/types.ts` | Modify | P0 | New event types, AppState fields, ThinkingBlock, ToolTimelineEntry |
| `web/src/lib/api.ts` | Modify | P0,P3,P4 | Error handling overhaul, testConnection, getResultJson |
| `web/src/index.css` | Modify | P0 | Remove Tailwind import, add keyframes |
| `web/src/App.tsx` | Modify | P0-P4 | Theme, new reducer cases, new components, SSE parsing |
| `server.py` | **Rewrite (streaming)** | P0 | Replace thread+callback with async `agent.iter()` |
| `agent.py` | Modify | P0 | Remove event_callback, remove _track_turn wrapper |
| `web/src/components/PipelineStages.tsx` | **Create** | P1 | Vertical step indicator |
| `web/src/components/ElapsedTimer.tsx` | **Create** | P1 | mm:ss timer |
| `web/src/components/UploadPanel.tsx` | Modify | P1 | Spinner + timer |
| `web/src/components/TokenDashboard.tsx` | Modify | P1 | Inline styles + pulsing dot |
| `web/src/components/ThinkingBlock.tsx` | **Create** | P2 | Collapsible thinking stream |
| `web/src/components/ToolCallCard.tsx` | **Create** | P2 | Tool invocation card |
| `web/src/components/StreamingText.tsx` | **Create** | P2 | Streaming model text |
| `web/src/components/AgentFeed.tsx` | **Create** | P2 | Replaces LiveFeed |
| `web/src/components/LiveFeed.tsx` | **Delete** | P2 | Replaced by AgentFeed |
| `web/src/components/SettingsModal.tsx` | Modify | P3 | Validation, test connection, inline styles |
| `web/src/components/ResultsView.tsx` | **Create** | P4 | Tabbed results |
| `web/src/components/ResultsPanel.tsx` | **Delete** | P4 | Replaced by ResultsView |
| `web/src/components/RunHistory.tsx` | **Create** | P5 | Session list (deferred) |

### Test Files

| File | Action | Priority |
|------|--------|----------|
| `web/src/__tests__/theme.test.ts` | **Create** | P0 |
| `web/src/__tests__/appReducer.test.ts` | Modify | P0 |
| `tests/test_streaming.py` | **Create** | P0 |
| `web/src/__tests__/PipelineStages.test.tsx` | **Create** | P1 |
| `web/src/__tests__/ElapsedTimer.test.tsx` | **Create** | P1 |
| `web/src/__tests__/UploadPanel.test.tsx` | Modify | P1 |
| `web/src/__tests__/ThinkingBlock.test.tsx` | **Create** | P2 |
| `web/src/__tests__/ToolCallCard.test.tsx` | **Create** | P2 |
| `web/src/__tests__/AgentFeed.test.tsx` | **Create** | P2 |
| `web/src/__tests__/StreamingText.test.tsx` | **Create** | P2 |
| `web/src/__tests__/SettingsModal.test.tsx` | **Create** | P3 |
| `tests/test_server.py` | Modify | P3 |
| `web/src/__tests__/ResultsView.test.tsx` | **Create** | P4 |
| `web/src/__tests__/api.test.ts` | Modify | P4 |
| `web/src/__tests__/RunHistory.test.tsx` | **Create** | P5 |

---

## Peer Review Issues — Resolution Tracker

| Issue | Severity | Resolution | Priority |
|-------|----------|------------|----------|
| AppState missing runStartTime, no PipelineStages | HIGH | Added to P0 (types) + P1 (components) | P0-P1 |
| agent.py only emits tool_result, not tool_call | HIGH | Eliminated — P0 replaces event_callback with `agent.iter()` native events | P0 |
| No field validation, no test-connection in settings | HIGH | Full implementation in P3 | P3 |
| No tabbed ResultsView, no data preview | HIGH | Full implementation in P4 | P4 |
| Components use Tailwind instead of inline styles | MEDIUM | Each component converted when touched (P0 for App, P1 for TokenDashboard/UploadPanel, P2 for LiveFeed→AgentFeed, P3 for SettingsModal) | P0-P3 |
| Logging uses print(), no structured logging | MEDIUM | Replaced with module logger in P3 backend changes | P3 |
| api.ts error handling too generic | MEDIUM | Overhauled in P0 with `apiFetch` helper | P0 |
| Test suite only covers happy paths | MEDIUM | Red-Green TDD approach adds tests for every new feature | P0-P5 |

---

## Verification

Per priority, after each GREEN phase:

1. `cd web && npm run build` — no TypeScript/build errors
2. `cd web && npx vitest run` — all tests pass (new + existing)
3. `python -m pytest tests/ -v` — backend tests pass

### End-to-end manual verification (after P2):
- Start server + frontend, upload a PDF
- **P0**: SSE stream delivers thinking_delta, tool_call, tool_result, text_delta events
- **P1**: Pipeline stages animate through phases, timer counts up, spinner shows
- **P2**: Thinking blocks stream and auto-collapse, tool cards show duration bars, model text streams with caret
- **P3**: Settings validates fields, test-connection shows latency/error, Enter saves
- **P4**: Results view shows summary stats, data preview table with highlighted empty fields, downloads work
