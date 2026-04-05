import { useReducer, useCallback, useState, useRef, useEffect } from "react";
import type {
  SSEEvent,
  EventPhase,
  StatusData,
  ThinkingDeltaData,
  ThinkingEndData,
  TextDeltaData,
  ToolCallData,
  ToolResultData,
  TokenData,
  ErrorData,
  CompleteData,
  ThinkingBlock,
  ToolTimelineEntry,
} from "./lib/types";
import { pwc } from "./lib/theme";
import { uploadPdf, getSettings, updateSettings, testConnection, getResultJson } from "./lib/api";
import { createSSE } from "./lib/sse";
import { UploadPanel } from "./components/UploadPanel";
import { PipelineStages } from "./components/PipelineStages";
import { AgentFeed } from "./components/AgentFeed";
import { TokenDashboard } from "./components/TokenDashboard";
import { ResultsView } from "./components/ResultsView";
import { SettingsModal } from "./components/SettingsModal";
import "./index.css";

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

export interface AppState {
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
  // P0: Streaming state
  runStartTime: number | null;
  thinkingBuffer: string;
  activeThinkingId: string | null;
  thinkingBlocks: ThinkingBlock[];
  toolTimeline: ToolTimelineEntry[];
  streamingText: string;
}

export type AppAction =
  | { type: "UPLOADED"; payload: { sessionId: string; filename: string } }
  | { type: "RUN_STARTED" }
  | { type: "EVENT"; payload: SSEEvent }
  | { type: "RESET" };

export const initialState: AppState = {
  sessionId: null,
  filename: null,
  isRunning: false,
  isComplete: false,
  hasError: false,
  events: [],
  currentPhase: null,
  tokens: null,
  error: null,
  complete: null,
  // P0: Streaming state
  runStartTime: null,
  thinkingBuffer: "",
  activeThinkingId: null,
  thinkingBlocks: [],
  toolTimeline: [],
  streamingText: "",
};

export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "UPLOADED":
      return {
        ...initialState,
        sessionId: action.payload.sessionId,
        filename: action.payload.filename,
      };

    case "RUN_STARTED":
      return { ...state, isRunning: true, runStartTime: Date.now() };

    case "EVENT": {
      const event = action.payload;
      const updates: Partial<AppState> = {
        events: [...state.events, event],
      };

      switch (event.event) {
        case "status":
          updates.currentPhase = (event.data as StatusData).phase;
          break;

        case "thinking_delta": {
          const td = event.data as ThinkingDeltaData;
          updates.thinkingBuffer = state.thinkingBuffer + td.content;
          updates.activeThinkingId = td.thinking_id;
          break;
        }

        case "thinking_end": {
          const te = event.data as ThinkingEndData;
          const block: ThinkingBlock = {
            id: te.thinking_id,
            content: state.thinkingBuffer,
            summary: te.summary,
            timestamp: Date.now(),
            phase: state.currentPhase,
            // Server-measured reasoning time. Falls back to null if the
            // backend didn't send it (older server versions).
            durationMs: te.duration_ms ?? null,
          };
          updates.thinkingBlocks = [...state.thinkingBlocks, block];
          updates.thinkingBuffer = "";
          updates.activeThinkingId = null;
          break;
        }

        case "text_delta": {
          const txtd = event.data as TextDeltaData;
          updates.streamingText = state.streamingText + txtd.content;
          break;
        }

        case "tool_call": {
          const tc = event.data as ToolCallData;
          const entry: ToolTimelineEntry = {
            tool_call_id: tc.tool_call_id,
            tool_name: tc.tool_name,
            args: tc.args,
            result_summary: null,
            duration_ms: null,
            startTime: Date.now(),
            endTime: null,
            phase: state.currentPhase,
          };
          updates.toolTimeline = [...state.toolTimeline, entry];
          break;
        }

        case "tool_result": {
          const tr = event.data as ToolResultData;
          // Pair with matching tool_call by tool_call_id
          updates.toolTimeline = state.toolTimeline.map((entry) =>
            entry.tool_call_id === tr.tool_call_id
              ? {
                  ...entry,
                  result_summary: tr.result_summary,
                  duration_ms: tr.duration_ms,
                  endTime: Date.now(),
                }
              : entry,
          );
          break;
        }

        case "token_update":
          updates.tokens = event.data as TokenData;
          break;

        case "error":
          updates.hasError = true;
          updates.error = event.data as ErrorData;
          updates.isRunning = false;
          break;

        case "complete":
          updates.isComplete = true;
          updates.isRunning = false;
          updates.complete = event.data as CompleteData;
          break;
      }

      return { ...state, ...updates };
    }

    case "RESET":
      return initialState;

    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// Inline styles using PwC theme
// ---------------------------------------------------------------------------

const styles = {
  page: {
    minHeight: "100vh",
    background: pwc.grey50,
  } as const,
  header: {
    background: pwc.white,
    borderBottom: `1px solid ${pwc.grey200}`,
    padding: `${pwc.space.lg}px ${pwc.space.xl}px`,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  } as const,
  headerTitle: {
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    fontSize: 20,
    color: pwc.grey900,
    margin: 0,
  } as const,
  settingsButton: {
    padding: pwc.space.sm,
    color: pwc.grey500,
    background: "none",
    border: "none",
    borderRadius: pwc.radius.md,
    cursor: "pointer",
  } as const,
  main: {
    maxWidth: 960,
    margin: "0 auto",
    padding: `${pwc.space.xxl}px ${pwc.space.xl}px`,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xl,
  } as const,
  errorBox: {
    background: "#FEF2F2",
    border: `1px solid #FECACA`,
    borderRadius: pwc.radius.md,
    padding: pwc.space.lg,
  } as const,
  errorTitle: {
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    color: "#991B1B",
    fontSize: 15,
    margin: 0,
  } as const,
  errorMessage: {
    fontFamily: pwc.fontBody,
    color: "#B91C1C",
    fontSize: 14,
    marginTop: pwc.space.xs,
  } as const,
  errorTraceback: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: "#B91C1C",
    whiteSpace: "pre-wrap" as const,
    overflow: "auto",
    marginTop: pwc.space.sm,
  } as const,
  resetLink: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.orange500,
    background: "none",
    border: "none",
    cursor: "pointer",
    textDecoration: "underline",
    padding: 0,
  } as const,
};

// ---------------------------------------------------------------------------
// App component
// ---------------------------------------------------------------------------

export default function App() {
  const [state, dispatch] = useReducer(appReducer, initialState);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Hold the SSE controller so we can abort it on reset/unmount.
  // Without this, an in-flight stream would keep dispatching stale events
  // after the user starts a new run or closes the page.
  const sseControllerRef = useRef<AbortController | null>(null);

  // Abort any open stream on unmount
  useEffect(() => {
    return () => {
      sseControllerRef.current?.abort();
    };
  }, []);

  const handleReset = useCallback(() => {
    // Abort any active stream before clearing state, so the old run
    // can't race in events after the user has moved on.
    sseControllerRef.current?.abort();
    sseControllerRef.current = null;
    dispatch({ type: "RESET" });
  }, []);

  const handleUpload = useCallback(async (file: File) => {
    const result = await uploadPdf(file);
    dispatch({
      type: "UPLOADED",
      payload: { sessionId: result.session_id, filename: result.filename },
    });
    return result;
  }, []);

  const handleRun = useCallback(() => {
    if (!state.sessionId) return;
    // Cancel any previous stream before starting a new one
    sseControllerRef.current?.abort();
    dispatch({ type: "RUN_STARTED" });
    sseControllerRef.current = createSSE(
      state.sessionId,
      (event) => dispatch({ type: "EVENT", payload: event }),
      () => {},
      (error) =>
        dispatch({
          type: "EVENT",
          payload: {
            event: "error",
            data: { message: error, traceback: "" },
            timestamp: Date.now() / 1000,
          },
        }),
    );
  }, [state.sessionId]);

  return (
    <div style={styles.page}>
      {/* Header */}
      <header style={styles.header}>
        <h1 style={styles.headerTitle}>SOFP Agent</h1>
        <button
          onClick={() => setSettingsOpen(true)}
          style={styles.settingsButton}
          aria-label="Settings"
        >
          <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        </button>
      </header>

      <main style={styles.main}>
        {/* Upload + Run */}
        <UploadPanel
          onUpload={handleUpload}
          isRunning={state.isRunning}
          filename={state.filename}
          onRun={handleRun}
          canRun={!!state.sessionId && !state.isRunning && !state.isComplete}
          startTime={state.runStartTime}
        />

        {/* Pipeline stage indicator */}
        {(state.isRunning || state.currentPhase) && (
          <PipelineStages
            currentPhase={state.currentPhase}
            isRunning={state.isRunning}
            isComplete={state.isComplete}
          />
        )}

        {/* Token dashboard (sticky while running) */}
        {(state.isRunning || state.tokens) && (
          <TokenDashboard tokens={state.tokens} isRunning={state.isRunning} />
        )}

        {/* Agent feed — replaces LiveFeed with thinking blocks, tool cards, streaming text */}
        {state.events.length > 0 && (
          <AgentFeed
            events={state.events}
            thinkingBlocks={state.thinkingBlocks}
            toolTimeline={state.toolTimeline}
            streamingText={state.streamingText}
            thinkingBuffer={state.thinkingBuffer}
            activeThinkingId={state.activeThinkingId}
            isRunning={state.isRunning}
            currentPhase={state.currentPhase}
          />
        )}

        {/* Error display */}
        {state.hasError && state.error && (
          <div style={styles.errorBox}>
            <h3 style={styles.errorTitle}>Extraction Error</h3>
            <p style={styles.errorMessage}>{state.error.message}</p>
            {state.error.traceback && (
              <details style={{ marginTop: pwc.space.sm }}>
                <summary style={{ color: pwc.error, fontSize: 13, cursor: "pointer" }}>
                  Show traceback
                </summary>
                <pre style={styles.errorTraceback}>{state.error.traceback}</pre>
              </details>
            )}
          </div>
        )}

        {/* Results */}
        {state.isComplete && state.complete && (
          <ResultsView
            complete={state.complete}
            sessionId={state.sessionId!}
            runStartTime={state.runStartTime}
            getResultJson={getResultJson}
          />
        )}

        {/* New extraction button after completion */}
        {(state.isComplete || state.hasError) && (
          <button onClick={handleReset} style={styles.resetLink}>
            Start new extraction
          </button>
        )}
      </main>

      {/* Settings modal */}
      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        getSettings={getSettings}
        saveSettings={updateSettings}
        testConnection={testConnection}
      />
    </div>
  );
}
