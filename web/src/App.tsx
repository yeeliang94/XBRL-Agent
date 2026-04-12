import { useReducer, useCallback, useState, useRef, useEffect } from "react";
import type {
  SSEEvent,
  EventPhase,
  StatusData,
  TokenData,
  ErrorData,
  CompleteData,
  RunCompleteData,
  AgentCompleteData,
  ToolTimelineEntry,
  RunConfigPayload,
  AgentState,
  CrossCheckResult,
} from "./lib/types";
import { createAgentState } from "./lib/types";
import { pwc } from "./lib/theme";
import { uploadPdf, getSettings, updateSettings, testConnection, getResultJson, getExtendedSettings, abortAll, abortAgent } from "./lib/api";
import { createMultiAgentSSE } from "./lib/sse";
import { buildToolTimeline } from "./lib/buildToolTimeline";
import { UploadPanel } from "./components/UploadPanel";
import { PreRunPanel } from "./components/PreRunPanel";
import { PipelineStages } from "./components/PipelineStages";
import { AgentTimeline } from "./components/AgentTimeline";
import { TokenDashboard } from "./components/TokenDashboard";
import { ResultsView } from "./components/ResultsView";
import { SettingsModal } from "./components/SettingsModal";
import { AgentTabs } from "./components/AgentTabs";
import type { AgentTabState } from "./components/AgentTabs";
import { ValidatorTab } from "./components/ValidatorTab";
import { TopNav } from "./components/TopNav";
import { SuccessToast } from "./components/SuccessToast";
import { HistoryPage } from "./pages/HistoryPage";
import { STATEMENT_TYPES } from "./lib/types";
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
  runStartTime: number | null;
  // Phase 6: streaming chat state (thinkingBuffer, activeThinkingId,
  // thinkingBlocks, streamingText, textSegments) was removed when the chat
  // feed was replaced by the tool-call timeline. toolTimeline is now the
  // only derived-from-events field we keep on AppState.
  toolTimeline: ToolTimelineEntry[];
  // Phase 10: Per-agent state for tab UI
  agents: Record<string, AgentState>;
  agentTabOrder: string[];      // ordered agent IDs for tab rendering
  activeTab: string | null;     // currently selected tab
  crossChecks: CrossCheckResult[];
  statementsInRun: string[];    // which statements were requested (for skeleton tabs)
  lastRunConfig: RunConfigPayload | null;  // preserved for rerun with correct variant/model
  // Phase 4: top-nav SPA routing — 'extract' is the main run workspace,
  // 'history' is the past-runs browser. Switching views does NOT reset
  // in-flight extraction state, so a user can peek at history mid-run.
  view: AppView;
  // Phase 9: transient toast surfaced in the top-right corner on run
  // completion. Null when no toast is active. Dismissed via DISMISS_TOAST
  // (either from the manual close button or the auto-dismiss timer).
  toast: ToastState | null;
}

export interface ToastState {
  message: string;
  tone: "success" | "error";
}

export type AppView = "extract" | "history";

export type AppAction =
  | { type: "UPLOADED"; payload: { sessionId: string; filename: string } }
  | { type: "RUN_STARTED"; payload?: { statements?: string[]; config?: RunConfigPayload } }
  | { type: "EVENT"; payload: SSEEvent }
  | { type: "SET_ACTIVE_TAB"; payload: string }
  | { type: "ABORT_AGENT"; payload: { agentId: string } }
  | { type: "RERUN_STARTED"; payload: { agentId: string } }
  | { type: "SET_VIEW"; payload: AppView }
  | { type: "DISMISS_TOAST" }
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
  runStartTime: null,
  toolTimeline: [],
  agents: {},
  agentTabOrder: [],
  activeTab: null,
  crossChecks: [],
  statementsInRun: [],
  lastRunConfig: null,
  view: "extract",
  toast: null,
};

/**
 * Lazy initializer for useReducer that inspects the current URL so deep-links
 * and refreshes to `/history` land in the history tab without a flash of the
 * extract UI. Computed at component mount (not module load) so test suites
 * that rewrite the URL between renders get the correct boot view.
 */
function bootState(): AppState {
  if (typeof window !== "undefined" && window.location.pathname.startsWith("/history")) {
    return { ...initialState, view: "history" };
  }
  return initialState;
}

// ---------------------------------------------------------------------------
// Shared derived-state shape — fields common to both AppState and AgentState
// that get recomputed as events arrive. Phase 6 stripped the chat-streaming
// fields (thinkingBuffer/activeThinkingId/thinkingBlocks/streamingText/
// textSegments); what remains is the current pipeline phase and the tool
// timeline derived via buildToolTimeline.
// ---------------------------------------------------------------------------

interface DerivedStreamState {
  events: SSEEvent[];
  toolTimeline: ToolTimelineEntry[];
  currentPhase: EventPhase | null;
}

/**
 * Pure function that computes shared derived-state updates for an event.
 * Used by both agentReducer (per-agent) and appReducer (global).
 */
function applyStreamingEvent(
  state: DerivedStreamState,
  event: SSEEvent,
): Partial<DerivedStreamState> | null {
  switch (event.event) {
    case "status":
      return { currentPhase: (event.data as StatusData).phase };

    case "tool_call":
    case "tool_result":
      // Rebuild the timeline over the full event list — single source of
      // truth shared with history replay. buildToolTimeline also walks
      // status events itself to track phase, so each entry gets the right
      // phase without the reducer keeping a separate cursor.
      return {
        toolTimeline: buildToolTimeline([...state.events, event]),
      };

    default:
      // thinking_delta / thinking_end / text_delta land in events[] via the
      // caller but don't drive any derived state anymore.
      return null;
  }
}

// ---------------------------------------------------------------------------
// Per-agent reducer — handles streaming events within a single agent's slice
// ---------------------------------------------------------------------------

export function agentReducer(agent: AgentState, event: SSEEvent): AgentState {
  const updates: Partial<AgentState> = {
    events: [...agent.events, event],
  };

  // Apply shared streaming state updates
  const streamingUpdates = applyStreamingEvent(agent, event);
  if (streamingUpdates) {
    Object.assign(updates, streamingUpdates);
    if (event.event === "status" || event.event === "thinking_delta") {
      updates.status = "running";
    }
  }

  // Agent-specific event handling
  switch (event.event) {
    case "token_update":
      updates.tokens = event.data as TokenData;
      break;

    case "error":
      updates.error = event.data as ErrorData;
      updates.status = "failed";
      break;

    case "complete": {
      const cd = event.data as AgentCompleteData;
      if (cd.success) {
        updates.status = "complete";
      } else if (cd.error === "Cancelled by user") {
        updates.status = "cancelled";
      } else {
        updates.status = "failed";
      }
      updates.workbookPath = cd.workbook_path ?? null;
      if (cd.error && cd.error !== "Cancelled by user") {
        updates.error = { message: cd.error, traceback: "" };
      }
      break;
    }

    default:
      break;
  }

  return { ...agent, ...updates };
}

// ---------------------------------------------------------------------------
// Helpers for auto-creating agent slots from SSE events
// ---------------------------------------------------------------------------

/** Derive agent_id from an SSE event's data payload, if present. */
function getAgentId(event: SSEEvent): string | null {
  const data = event.data as unknown as Record<string, unknown>;
  if (typeof data.agent_id === "string") return data.agent_id;
  if (typeof data.agent_role === "string") {
    // Some streaming events carry agent_role but no agent_id — derive it.
    // agent_id is the lowercase statement name (e.g. "sofp", "sopl").
    return data.agent_role.toLowerCase();
  }
  return null;
}

/** Ensure an agent slot exists; create it on-the-fly if not. */
function ensureAgent(
  agents: Record<string, AgentState>,
  tabOrder: string[],
  agentId: string,
  role?: string,
): { agents: Record<string, AgentState>; tabOrder: string[] } {
  if (agents[agentId]) return { agents, tabOrder };
  const label = (role || agentId).toUpperCase().replace(/_\d+$/, "");
  return {
    agents: { ...agents, [agentId]: createAgentState(agentId, role || agentId, label) },
    tabOrder: [...tabOrder, agentId],
  };
}

// ---------------------------------------------------------------------------
// Main app reducer
// ---------------------------------------------------------------------------

export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "UPLOADED":
      return {
        ...initialState,
        // Preserve the current view so an upload from /history doesn't silently
        // punt the user back to the extract tab.
        view: state.view,
        sessionId: action.payload.sessionId,
        filename: action.payload.filename,
      };

    case "RUN_STARTED": {
      const stmts = action.payload?.statements || [];
      return {
        ...state,
        isRunning: true,
        runStartTime: Date.now(),
        statementsInRun: stmts,
        lastRunConfig: action.payload?.config || state.lastRunConfig,
      };
    }

    case "SET_ACTIVE_TAB":
      return { ...state, activeTab: action.payload };

    case "SET_VIEW":
      return { ...state, view: action.payload };

    case "DISMISS_TOAST":
      return { ...state, toast: null };

    case "EVENT": {
      const event = action.payload;
      // Always accumulate in the global event list
      const updates: Partial<AppState> = {
        events: [...state.events, event],
      };

      // --- Route to per-agent state if event has agent_id ---
      const agentId = getAgentId(event);
      if (agentId && event.event !== "run_complete") {
        let { agents, tabOrder } = ensureAgent(
          state.agents,
          state.agentTabOrder,
          agentId,
          (event.data as unknown as Record<string, unknown>).agent_role as string | undefined,
        );
        const agentState = agents[agentId];
        agents = { ...agents, [agentId]: agentReducer(agentState, event) };
        updates.agents = agents;
        updates.agentTabOrder = tabOrder;

        // Auto-select first tab when first agent event arrives
        if (!state.activeTab) {
          updates.activeTab = agentId;
        }
      }

      // --- Global state updates (shared streaming logic + app-specific) ---
      const globalStreamingUpdates = applyStreamingEvent(state, event);
      if (globalStreamingUpdates) {
        Object.assign(updates, globalStreamingUpdates);
      }

      switch (event.event) {
        case "token_update": {
          // If event has an agent_id, aggregate tokens across all agents.
          // Per-agent tokens are already updated in agentReducer above.
          const tokenAgentId = getAgentId(event);
          if (tokenAgentId) {
            const allAgents = updates.agents ?? state.agents;
            let totalPrompt = 0, totalCompletion = 0, totalThinking = 0, totalCumulative = 0, totalCost = 0;
            for (const a of Object.values(allAgents)) {
              if (a.tokens) {
                totalPrompt += a.tokens.prompt_tokens;
                totalCompletion += a.tokens.completion_tokens;
                totalThinking += a.tokens.thinking_tokens;
                totalCumulative += a.tokens.cumulative;
                totalCost += a.tokens.cost_estimate;
              }
            }
            updates.tokens = {
              prompt_tokens: totalPrompt,
              completion_tokens: totalCompletion,
              thinking_tokens: totalThinking,
              cumulative: totalCumulative,
              cost_estimate: totalCost,
            };
          } else {
            // No agent_id (legacy single-agent mode) — use as-is
            updates.tokens = event.data as TokenData;
          }
          break;
        }

        case "error": {
          // Per-agent errors (with agent_id) should NOT kill the global run —
          // other agents may still be running. Only promote to global failure
          // when the error is not scoped to a specific agent.
          const errAgentId = getAgentId(event);
          if (!errAgentId) {
            updates.hasError = true;
            updates.error = event.data as ErrorData;
            updates.isRunning = false;
          }
          // Agent-scoped errors are already routed to agentReducer above
          break;
        }

        case "complete":
          // In multi-agent mode this is a per-agent completion — don't
          // mark the run as finished. In legacy single-agent mode this IS
          // the terminal event.
          if ("agent_id" in (event.data as unknown as Record<string, unknown>)) {
            // Per-agent completion handled above in agentReducer
            break;
          }
          // Legacy single-agent terminal event
          updates.isComplete = true;
          updates.isRunning = false;
          updates.complete = event.data as CompleteData;
          break;

        case "run_complete": {
          // Final aggregate event for multi-agent runs
          updates.isComplete = true;
          updates.isRunning = false;
          const rc = event.data as RunCompleteData;
          // Phase 9: surface a minimal success toast. Failures already show
          // their error in-panel, so no toast for the red path.
          if (rc.success) {
            updates.toast = {
              message: "Run completed successfully",
              tone: "success",
            };
          }
          const currentTokens = state.tokens;
          updates.complete = {
            success: rc.success,
            output_path: "",
            excel_path: rc.merged_workbook || "",
            trace_path: "",
            total_tokens: currentTokens?.cumulative ?? 0,
            cost: currentTokens?.cost_estimate ?? 0,
            statementsCompleted: rc.statements_completed,
          } as CompleteData;
          // Store cross-check results for the Validator tab
          updates.crossChecks = rc.cross_checks || [];
          // Ensure validator tab exists after run completes
          if (rc.cross_checks && rc.cross_checks.length > 0) {
            const { agents, tabOrder } = ensureAgent(
              updates.agents || state.agents,
              updates.agentTabOrder || state.agentTabOrder,
              "validator",
              "validator",
            );
            // Mark validator as complete
            agents.validator = {
              ...agents.validator,
              status: "complete",
            };
            updates.agents = agents;
            updates.agentTabOrder = tabOrder;
          }
          break;
        }
      }

      return { ...state, ...updates };
    }

    case "ABORT_AGENT": {
      // Optimistic UI — mark agent as "aborting" while the backend cancels it.
      // The real "cancelled" status arrives via the SSE complete event.
      const { agentId } = action.payload;
      const agent = state.agents[agentId];
      if (!agent) return state;
      return {
        ...state,
        agents: {
          ...state.agents,
          [agentId]: { ...agent, status: "aborting" },
        },
      };
    }

    case "RERUN_STARTED": {
      // Reset one agent's state back to pending so the new SSE events
      // flow into a clean slate. Tab position is preserved.
      //
      // Peer-review [HIGH] fix: also wipe the prior run's completion
      // state so stale ResultsView, stale cross-checks, stale error
      // state, and the stale Phase 9 success toast don't linger during
      // the rerun window. The new run_complete refreshes all of these
      // when it lands; until then the UI should reflect "running" only.
      const { agentId } = action.payload;
      const agent = state.agents[agentId];
      if (!agent) return state;
      return {
        ...state,
        isRunning: true,
        isComplete: false,
        complete: null,
        crossChecks: [],
        hasError: false,
        error: null,
        toast: null,
        agents: {
          ...state.agents,
          [agentId]: createAgentState(agentId, agent.role, agent.label),
        },
        activeTab: agentId,
      };
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
  headerLeft: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xl,
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
  abortAllButton: {
    position: "absolute" as const,
    right: pwc.space.sm,
    top: "50%",
    transform: "translateY(-50%)",
    padding: `${pwc.space.xs}px ${pwc.space.md}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: 600,
    color: pwc.white,
    background: pwc.error,
    border: "none",
    borderRadius: pwc.radius.sm,
    cursor: "pointer",
    outline: "none",
  } as const,
  tabBarCard: {
    position: "relative" as const,
  } as const,
  activitySection: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 0,
  } as const,
  activityCard: {
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    boxShadow: pwc.shadow.card,
    overflow: "hidden" as const,
    display: "flex",
    flexDirection: "column" as const,
  } as const,
  activityCardAttached: {
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderTop: "none",
    borderRadius: `0 0 ${pwc.radius.md}px ${pwc.radius.md}px`,
    boxShadow: pwc.shadow.card,
    overflow: "hidden" as const,
    display: "flex",
    flexDirection: "column" as const,
    marginTop: -1,
  } as const,
  activityHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: `${pwc.space.md}px ${pwc.space.lg}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
    background: pwc.grey50,
  } as const,
  activityTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey900,
  } as const,
  activityCount: {
    fontFamily: pwc.fontMono,
    fontSize: 11,
    color: pwc.grey500,
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
  const [state, dispatch] = useReducer(appReducer, undefined, bootState);
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

  // --- Phase 4: URL <-> view sync ----------------------------------------
  // Push the view into the address bar whenever it changes, so deep-linking
  // and copy/paste of the URL work. Listen for popstate so browser Back and
  // Forward buttons update the in-memory view without a full reload.
  useEffect(() => {
    const expected = state.view === "history" ? "/history" : "/";
    if (window.location.pathname !== expected) {
      window.history.pushState({ view: state.view }, "", expected);
    }
  }, [state.view]);

  useEffect(() => {
    const onPop = () => {
      const nextView = window.location.pathname.startsWith("/history") ? "history" : "extract";
      dispatch({ type: "SET_VIEW", payload: nextView });
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
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

  // Multi-agent run: receives a RunConfigPayload from PreRunPanel
  const handleMultiRun = useCallback((config: RunConfigPayload) => {
    if (!state.sessionId) return;
    sseControllerRef.current?.abort();
    dispatch({ type: "RUN_STARTED", payload: { statements: config.statements, config } });
    sseControllerRef.current = createMultiAgentSSE(
      state.sessionId,
      config,
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

  // Abort all running agents
  const handleAbortAll = useCallback(async () => {
    if (!state.sessionId) return;
    try {
      await abortAll(state.sessionId);
    } catch {
      // 404 = no tasks left; SSE events will handle state updates
    }
  }, [state.sessionId]);

  // Abort a single agent
  const handleAbortAgent = useCallback(async (agentId: string) => {
    if (!state.sessionId) return;
    dispatch({ type: "ABORT_AGENT", payload: { agentId } });
    try {
      await abortAgent(state.sessionId, agentId);
    } catch {
      // 404 = agent already finished
    }
  }, [state.sessionId]);

  // Rerun a single statement after abort/failure.
  // Preserves the original variant, model, and infopack from the initial run.
  const handleRerunAgent = useCallback((agentId: string) => {
    if (!state.sessionId) return;
    const agent = state.agents[agentId];
    if (!agent) return;

    dispatch({ type: "RERUN_STARTED", payload: { agentId } });

    const stmtKey = agent.role;
    const prev = state.lastRunConfig;

    // Rebuild config for just this one statement, preserving original settings
    const config: RunConfigPayload = {
      statements: [stmtKey as RunConfigPayload["statements"][0]],
      variants: prev?.variants[stmtKey] ? { [stmtKey]: prev.variants[stmtKey] } : {},
      models: prev?.models[stmtKey] ? { [stmtKey]: prev.models[stmtKey] } : {},
      infopack: prev?.infopack || null,
      use_scout: false,
    };
    // Use the rerun endpoint so it doesn't conflict with active_runs guard
    sseControllerRef.current = createMultiAgentSSE(
      state.sessionId,
      config,
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
      `/api/rerun/${state.sessionId}`,
    );
  }, [state.sessionId, state.agents, state.lastRunConfig]);

  return (
    <div style={styles.page}>
      {/* Header */}
      <header style={styles.header}>
        <div style={styles.headerLeft}>
          <h1 style={styles.headerTitle}>XBRL Agent</h1>
          <TopNav
            view={state.view}
            onViewChange={(v) => dispatch({ type: "SET_VIEW", payload: v })}
          />
        </div>
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
        {state.view === "history" ? (
          <HistoryPage />
        ) : (
          <ExtractView
            state={state}
            dispatch={dispatch}
            handleUpload={handleUpload}
            handleMultiRun={handleMultiRun}
            handleAbortAll={handleAbortAll}
            handleAbortAgent={handleAbortAgent}
            handleRerunAgent={handleRerunAgent}
            handleReset={handleReset}
          />
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

      {/* Phase 9: Run-complete success toast — top-right, auto-dismiss 4 s */}
      <SuccessToast
        toast={state.toast}
        onDismiss={() => dispatch({ type: "DISMISS_TOAST" })}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// ExtractView — the extraction workspace. Split out from App so the header
// and TopNav can stay mounted while switching between Extract and History
// without re-mounting the run pipeline UI.
// ---------------------------------------------------------------------------

interface ExtractViewProps {
  state: AppState;
  dispatch: React.Dispatch<AppAction>;
  handleUpload: (file: File) => Promise<UploadResponseShape>;
  handleMultiRun: (config: RunConfigPayload) => void;
  handleAbortAll: () => Promise<void>;
  handleAbortAgent: (agentId: string) => Promise<void>;
  handleRerunAgent: (agentId: string) => void;
  handleReset: () => void;
}

// Minimal local alias — avoids circular import of the real UploadResponse type
// while keeping the prop type honest.
type UploadResponseShape = { session_id: string; filename: string };

function ExtractView({
  state,
  dispatch,
  handleUpload,
  handleMultiRun,
  handleAbortAll,
  handleAbortAgent,
  handleRerunAgent,
  handleReset,
}: ExtractViewProps) {
  return (
    <>
        {/* Upload + Run */}
        <UploadPanel
          onUpload={handleUpload}
          isRunning={state.isRunning}
          filename={state.filename}
          startTime={state.runStartTime}
        />

        {/* Pre-run configuration panel — shown after upload, hidden once running */}
        {state.sessionId && !state.isRunning && !state.isComplete && !state.hasError && (
          <PreRunPanel
            sessionId={state.sessionId}
            getSettings={getExtendedSettings}
            onRun={handleMultiRun}
          />
        )}

        {/* Pipeline stage indicator */}
        {(state.isRunning || state.currentPhase) && (
          <PipelineStages
            currentPhase={state.activeTab ? (state.agents[state.activeTab]?.currentPhase ?? state.currentPhase) : state.currentPhase}
            isRunning={state.isRunning}
            isComplete={state.isComplete}
          />
        )}

        {/* Token dashboard (sticky while running) — above the tabs+feed card */}
        {(state.isRunning || state.tokens) && (
          <TokenDashboard tokens={state.tokens} isRunning={state.isRunning} />
        )}

        {/* Agent tabs + monitor */}
        {state.agentTabOrder.length > 0 && (
          <div
            style={
              state.events.length > 0 ? styles.activitySection : undefined
            }
          >
            <div style={styles.tabBarCard}>
              <AgentTabs
                agents={Object.fromEntries(
                  Object.entries(state.agents).map(([id, a]) => [
                    id,
                    { agentId: a.agentId, label: a.label, status: a.status, role: a.role } as AgentTabState,
                  ]),
                )}
                tabOrder={state.agentTabOrder}
                activeTab={state.activeTab || state.agentTabOrder[0]}
                onTabClick={(id) => dispatch({ type: "SET_ACTIVE_TAB", payload: id })}
                onAbortAgent={handleAbortAgent}
                onRerunAgent={handleRerunAgent}
                isRunning={state.isRunning}
                // Phase 8: gate statement tabs so nothing shows until a run starts.
                statementsInRun={state.statementsInRun}
                skeletonTabs={
                  // Show skeleton tabs for statements in this run that haven't reported yet
                  STATEMENT_TYPES.filter(
                    (st) =>
                      state.statementsInRun.includes(st) &&
                      !state.agentTabOrder.some((id) => state.agents[id]?.role === st),
                  )
                }
              />
              {/* Stop All button — top-right of tab bar */}
              {state.isRunning && (
                <button
                  onClick={handleAbortAll}
                  style={styles.abortAllButton}
                  title="Stop all running agents"
                >
                  Stop All
                </button>
              )}
            </div>

            {state.events.length > 0 && (() => {
              if (state.activeTab === "validator") {
                return (
                  <div style={styles.activityCardAttached}>
                    <div style={styles.activityHeader}>
                      <span style={styles.activityTitle}>Cross-checks</span>
                      <span style={styles.activityCount}>
                        {state.crossChecks.length} checks
                      </span>
                    </div>
                    <ValidatorTab crossChecks={state.crossChecks} />
                  </div>
                );
              }
              const activeAgent = state.activeTab ? state.agents[state.activeTab] : null;
              const events = activeAgent ? activeAgent.events : state.events;
              const toolTimeline = activeAgent ? activeAgent.toolTimeline : state.toolTimeline;
              const running = activeAgent ? activeAgent.status === "running" : state.isRunning;
              return (
                <div style={styles.activityCardAttached}>
                  <div style={styles.activityHeader}>
                    <span style={styles.activityTitle}>Agent Activity</span>
                    <span style={styles.activityCount}>
                      {events.length} {events.length === 1 ? "event" : "events"}
                    </span>
                  </div>
                  <AgentTimeline
                    events={events}
                    toolTimeline={toolTimeline}
                    isRunning={running}
                  />
                </div>
              );
            })()}
          </div>
        )}

        {/* Legacy: agent feed without tabs (single-agent mode) */}
        {state.agentTabOrder.length === 0 && state.events.length > 0 && (
          <AgentTimeline
            events={state.events}
            toolTimeline={state.toolTimeline}
            isRunning={state.isRunning}
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
    </>
  );
}
