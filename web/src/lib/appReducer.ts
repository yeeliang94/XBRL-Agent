import type {
  SSEEvent,
  EventPhase,
  TokenData,
  ErrorData,
  CompleteData,
  RunCompleteData,
  AgentCompleteData,
  ToolTimelineEntry,
  RunConfigPayload,
  AgentState,
  AgentTabStatus,
  CrossCheckResult,
} from "./types";
import { createAgentState } from "./types";

// Backend-emitted error string for user-cancelled agents. Kept as a named
// constant so the two sites that check it can't drift out of sync.
const CANCELLED_BY_USER = "Cancelled by user";

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
  crossChecksPartial: boolean;
  statementsInRun: string[];    // which statements were requested (for skeleton tabs)
  // PLAN §4 Phase D.3: notes templates requested for this run, mirrored in
  // shape from statementsInRun so AgentTabs can gate + skeleton notes tabs.
  notesInRun: string[];
  lastRunConfig: RunConfigPayload | null;  // preserved for rerun with correct variant/model
  // Phase 4: top-nav SPA routing — 'extract' is the main run workspace,
  // 'history' is the past-runs browser. Switching views does NOT reset
  // in-flight extraction state, so a user can peek at history mid-run.
  view: AppView;
  // Which run's full-page detail is currently open. Lifted to app-level
  // state (rather than kept inside HistoryPage) so the URL router can
  // round-trip /history/<id>: push on select, pop on back button, hydrate
  // on deep-link. Null when the history list itself is visible.
  selectedRunId: number | null;
  // PLAN-persistent-draft-uploads.md (Phase C): the run id encoded in a
  // shareable `/run/{id}` URL. Distinct from `selectedRunId` (which is a
  // History-page concept). When set, ExtractPage rehydrates from
  // `GET /api/runs/{id}` so a refresh or a copy-pasted link picks up the
  // saved PDF + draft config. Null on the bare `/` extract page.
  currentRunId: number | null;
  // Peer-review #3 (HIGH, RUN-REVIEW follow-up): the run id that "owns"
  // the current sessionId. Set in lockstep with sessionId during
  // UPLOADED. ExtractPage uses this to discriminate "fresh upload for
  // this run" (skip re-fetch) from "stale sessionId from a prior
  // upload, navigated to a different draft" (must re-fetch).
  // Without it, navigating /run/A → /run/B with a sessionId still in
  // state silently keeps A's session, so scout runs against the wrong
  // PDF.
  sessionRunId: number | null;
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
  | { type: "UPLOADED"; payload: { sessionId: string; filename: string; runId?: number | null } }
  | { type: "RUN_STARTED"; payload?: { statements?: string[]; notes?: string[]; config?: RunConfigPayload } }
  | { type: "EVENT"; payload: SSEEvent }
  | { type: "SET_ACTIVE_TAB"; payload: string }
  | { type: "ABORT_AGENT"; payload: { agentId: string } }
  | { type: "RERUN_STARTED"; payload: { agentId: string } }
  | { type: "SET_VIEW"; payload: AppView }
  | { type: "SET_SELECTED_RUN_ID"; payload: number | null }
  | { type: "SET_CURRENT_RUN_ID"; payload: number | null }
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
  crossChecksPartial: false,
  statementsInRun: [],
  notesInRun: [],
  lastRunConfig: null,
  view: "extract",
  selectedRunId: null,
  currentRunId: null,
  sessionRunId: null,
  toast: null,
};

// Captures the numeric run id when the pathname is exactly `/history/<n>`
// (optionally with a trailing slash). Used by both bootState and the App's
// popstate handler so URL parsing stays in one place.
const HISTORY_RUN_RE = /^\/history\/(\d+)\/?$/;
// Persistent-draft URL — `/run/<n>` is the shareable link returned from
// the upload endpoint. Same trailing-slash tolerance as the history form.
const RUN_RE = /^\/run\/(\d+)\/?$/;

/** Derive the app view + selected/current run id from a pathname.
 *
 *  - `/history/42` → history view, selectedRunId=42
 *  - `/history` (any non-numeric suffix included) → history view, no selection
 *  - `/run/42` → extract view, currentRunId=42
 *  - `/run/garbage` → extract view, no run id (graceful fallback for typos)
 *  - anything else → extract view
 *
 *  We're permissive on the history side so a URL like `/history/foo` still
 *  lands on the list instead of bouncing to extract — less confusing when
 *  someone mistypes a deep link. The same forgiveness applies to bad
 *  /run/<x> ids: render the generic extract page, don't throw.
 */
export function parseRouteFromPath(
  pathname: string,
): { view: AppView; selectedRunId: number | null; currentRunId: number | null } {
  if (pathname.startsWith("/run")) {
    const m = RUN_RE.exec(pathname);
    return {
      view: "extract",
      selectedRunId: null,
      currentRunId: m ? Number(m[1]) : null,
    };
  }
  if (!pathname.startsWith("/history")) {
    return { view: "extract", selectedRunId: null, currentRunId: null };
  }
  const m = HISTORY_RUN_RE.exec(pathname);
  return {
    view: "history",
    selectedRunId: m ? Number(m[1]) : null,
    currentRunId: null,
  };
}

/**
 * Lazy initializer for useReducer that inspects the current URL so deep-links
 * and refreshes to `/history` (or `/history/<id>`) land in the history tab
 * without a flash of the extract UI. Computed at component mount (not module
 * load) so test suites that rewrite the URL between renders get the correct
 * boot view.
 */
export function bootState(): AppState {
  if (typeof window === "undefined") return initialState;
  const { view, selectedRunId, currentRunId } = parseRouteFromPath(window.location.pathname);
  return { ...initialState, view, selectedRunId, currentRunId };
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
 *
 * Live path: applies each event incrementally against the current timeline
 * — tool_call appends, tool_result mutates-in-place by id. This keeps the
 * per-event cost O(M) (M = # tool calls so far) instead of the previous
 * O(N) rebuild from the full event list, which made the total stream
 * O(N²). History replay still calls `buildToolTimeline` over the full
 * event list — both paths must produce equivalent timelines and the test
 * `live and replay tool timelines stay equivalent` locks that invariant.
 */
function applyStreamingEvent(
  state: DerivedStreamState,
  event: SSEEvent,
): Partial<DerivedStreamState> | null {
  switch (event.event) {
    case "status":
      return { currentPhase: event.data.phase };

    case "tool_call": {
      const data = event.data;
      if (!data || !data.tool_call_id) return null;
      // Idempotent append: a duplicate tool_call (same id) is a no-op so
      // re-entries from e.g. a replayed event list don't create twin rows.
      if (state.toolTimeline.some((e) => e.tool_call_id === data.tool_call_id)) {
        return null;
      }
      const startTime = event.timestamp ? event.timestamp * 1000 : Date.now();
      const entry: ToolTimelineEntry = {
        tool_call_id: data.tool_call_id,
        tool_name: data.tool_name,
        args: data.args ?? {},
        result_summary: null,
        duration_ms: null,
        startTime,
        endTime: null,
        phase: state.currentPhase,
      };
      return { toolTimeline: [...state.toolTimeline, entry] };
    }

    case "tool_result": {
      const data = event.data;
      if (!data || !data.tool_call_id) return null;
      const endTime = event.timestamp ? event.timestamp * 1000 : Date.now();
      let changed = false;
      const nextTimeline = state.toolTimeline.map((entry) => {
        if (entry.tool_call_id !== data.tool_call_id) return entry;
        changed = true;
        return {
          ...entry,
          result_summary: data.result_summary,
          duration_ms: data.duration_ms,
          endTime,
        };
      });
      // Orphan result (no matching call) — drop it, matches buildToolTimeline
      // defensive behaviour so live and replay still agree.
      return changed ? { toolTimeline: nextTimeline } : null;
    }

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

  // Phase 5.2 / peer-review [M1]: Sheet-12 sub-agents emit `started`
  // status events with `batch_note_range` + `batch_page_range`. We
  // accumulate them on AgentState so the Notes-12 tab label can render
  // a live summary ("Notes 1-15, pp 18-37") instead of a bare title.
  // The double-cast through `unknown` is because StatusData is a
  // closed shape in types.ts — the extra fields ride on the SSE event
  // payload without being declared there.
  if (event.event === "status") {
    const d = event.data as unknown as Record<string, unknown>;
    if (d.phase === "started" && Array.isArray(d.batch_note_range) && Array.isArray(d.batch_page_range)) {
      const noteRange = d.batch_note_range as number[];
      const pageRange = d.batch_page_range as number[];
      const subId = typeof d.sub_agent_id === "string" ? d.sub_agent_id : "unknown";
      if (noteRange.length === 2 && pageRange.length === 2) {
        const existing = agent.subAgentBatchRanges ?? [];
        // Replace any prior entry for the same sub_agent_id (retry-safe)
        // and append new ones in first-seen order.
        const filtered = existing.filter(e => e.subAgentId !== subId);
        updates.subAgentBatchRanges = [
          ...filtered,
          {
            subAgentId: subId,
            notes: [noteRange[0], noteRange[1]],
            pages: [pageRange[0], pageRange[1]],
          },
        ];
      }
    }
  }

  // Agent-specific event handling
  switch (event.event) {
    case "token_update":
      updates.tokens = event.data;
      break;

    case "error":
      updates.error = event.data;
      updates.status = "failed";
      break;

    case "complete": {
      // agentReducer is only reached for events routed through
      // handlePerAgentEvent — i.e. events carrying agent_id — so in this
      // branch the union narrows to AgentCompleteData.
      const cd = event.data as AgentCompleteData;
      if (cd.success) {
        updates.status = "complete";
      } else if (cd.error === CANCELLED_BY_USER) {
        updates.status = "cancelled";
      } else {
        updates.status = "failed";
      }
      updates.workbookPath = cd.workbook_path ?? null;
      if (cd.error && cd.error !== CANCELLED_BY_USER) {
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
  if (typeof event.data.agent_id === "string") return event.data.agent_id;
  // Some streaming events carry agent_role but no agent_id — derive it.
  // agent_id is the lowercase statement name (e.g. "sofp", "sopl").
  if (typeof event.data.agent_role === "string") return event.data.agent_role.toLowerCase();
  return null;
}

// Short display labels for notes tabs — full marketing labels are in
// NOTES_TEMPLATE_LABELS but those are too verbose for a tab chip.
// Exported so skeleton tabs in ExtractPage and history rendering in
// RunDetailView can reuse the single source of truth. Keying is on the
// bare template value (e.g. "CORP_INFO") — callers that have a prefixed
// agent_id or DB statement_type should strip the prefix first via
// notesTabLabel().
export const NOTES_TAB_LABELS: Record<string, string> = {
  CORP_INFO: "Notes 10: Corp Info",
  ACC_POLICIES: "Notes 11: Acc Policies",
  LIST_OF_NOTES: "Notes 12: List of Notes",
  ISSUED_CAPITAL: "Notes 13: Issued Capital",
  RELATED_PARTY: "Notes 14: Related Party",
};

/**
 * Normalize any notes identifier to its short display label. Accepts:
 *  - bare template value:   "CORP_INFO"              -> "Notes 10: Corp Info"
 *  - live agent_id:         "notes:CORP_INFO"        -> "Notes 10: Corp Info"
 *  - persisted DB type:     "NOTES_CORP_INFO"        -> "Notes 10: Corp Info"
 * Unknown keys fall back to "Notes: <key>" so a newly-added template
 * still renders a sane chip before the map is updated.
 */
export function notesTabLabel(identifier: string): string {
  let key = identifier;
  if (key.startsWith("notes:")) key = key.slice("notes:".length);
  else if (key.startsWith("NOTES_")) key = key.slice("NOTES_".length);
  return NOTES_TAB_LABELS[key] ?? `Notes: ${key}`;
}


/**
 * Phase 5.2 / peer-review [M1]: compose a tab-level sub-title for an
 * agent that carries sub-agent batch metadata (currently only
 * Sheet-12's LIST_OF_NOTES fan-out). Returns null when there are no
 * sub-agents recorded — the caller renders only the main label.
 *
 * Output is terse by design: tabs are narrow. A 5-way fan-out over
 * notes 1-15 on pp 18-37 renders as "Notes 1-15, pp 18-37".
 */
export function agentSubAgentSummary(agent: AgentState): string | null {
  const ranges = agent.subAgentBatchRanges ?? [];
  if (ranges.length === 0) return null;
  const minNote = Math.min(...ranges.map(r => r.notes[0]));
  const maxNote = Math.max(...ranges.map(r => r.notes[1]));
  const minPage = Math.min(...ranges.map(r => r.pages[0]));
  const maxPage = Math.max(...ranges.map(r => r.pages[1]));
  const noteSpan = minNote === maxNote ? `Note ${minNote}` : `Notes ${minNote}-${maxNote}`;
  const pageSpan = minPage === maxPage ? `p ${minPage}` : `pp ${minPage}-${maxPage}`;
  return `${noteSpan}, ${pageSpan}`;
}

// Phase 7: display labels for pseudo-agents that emit under a fixed ID
// (correction pass + notes post-validator). Kept as a dedicated map so the
// upper-case uppercasing in deriveAgentLabel can't accidentally munge them
// and a test can pin the exact tab text users see.
//
// VALIDATOR is synthetic: created in handleRunComplete when cross_checks
// arrive, to carry the cross-check table. The friendly label "Cross-checks"
// stops it from visually colliding with the "Notes Validator" agent tab.
const PSEUDO_AGENT_LABELS: Record<string, string> = {
  CORRECTION: "Correction",
  NOTES_VALIDATOR: "Notes Validator",
  VALIDATOR: "Cross-checks",
};

/** Derive the display label for a newly created agent slot. */
function deriveAgentLabel(agentId: string, role: string): string {
  if (agentId.startsWith("notes:")) {
    return notesTabLabel(role || agentId);
  }
  const upper = (role || agentId).toUpperCase();
  if (PSEUDO_AGENT_LABELS[upper]) return PSEUDO_AGENT_LABELS[upper];
  return upper.replace(/_\d+$/, "");
}

/** Ensure an agent slot exists; create it on-the-fly if not. */
function ensureAgent(
  agents: Record<string, AgentState>,
  tabOrder: string[],
  agentId: string,
  role?: string,
): { agents: Record<string, AgentState>; tabOrder: string[] } {
  if (agents[agentId]) return { agents, tabOrder };
  const effectiveRole = role || agentId;
  const label = deriveAgentLabel(agentId, effectiveRole);
  return {
    agents: { ...agents, [agentId]: createAgentState(agentId, effectiveRole, label) },
    tabOrder: [...tabOrder, agentId],
  };
}

// ---------------------------------------------------------------------------
// EVENT-case helpers — extracted so the reducer switch stays linear and each
// concern is independently readable. All helpers are pure: they take the
// current state (plus any in-progress updates the caller has accumulated)
// and return a Partial<AppState> that the caller Object.assigns in.
// ---------------------------------------------------------------------------

/**
 * Route an SSE event to per-agent state: ensure the agent slot exists,
 * fan the event through `agentReducer`, and auto-select the first tab.
 * Returns null when the event has no agent_id (or is the terminal
 * `run_complete`, which is handled by `handleRunComplete` instead).
 */
function handlePerAgentEvent(
  state: AppState,
  event: SSEEvent,
): Partial<AppState> | null {
  if (event.event === "run_complete") return null;
  const agentId = getAgentId(event);
  if (!agentId) return null;
  const { agents: withSlot, tabOrder } = ensureAgent(
    state.agents,
    state.agentTabOrder,
    agentId,
    event.data.agent_role,
  );
  const nextAgents = { ...withSlot, [agentId]: agentReducer(withSlot[agentId], event) };
  const out: Partial<AppState> = {
    agents: nextAgents,
    agentTabOrder: tabOrder,
  };
  // Auto-select the first tab when the first agent event arrives
  if (!state.activeTab) {
    out.activeTab = agentId;
  }
  return out;
}

/**
 * Sum per-agent token counters into a single aggregate. Called on every
 * token_update event with an agent_id so the dashboard reflects the
 * whole run, not just the last agent that emitted.
 */
function aggregateTokens(agents: Record<string, AgentState>): TokenData {
  let prompt = 0;
  let completion = 0;
  let thinking = 0;
  let cumulative = 0;
  let cost = 0;
  for (const a of Object.values(agents)) {
    if (a.tokens) {
      prompt += a.tokens.prompt_tokens;
      completion += a.tokens.completion_tokens;
      thinking += a.tokens.thinking_tokens;
      cumulative += a.tokens.cumulative;
      cost += a.tokens.cost_estimate;
    }
  }
  return {
    prompt_tokens: prompt,
    completion_tokens: completion,
    thinking_tokens: thinking,
    cumulative,
    cost_estimate: cost,
  };
}

/**
 * Finalize the run on `run_complete`: flip the terminal flags, build the
 * CompleteData summary, store cross-checks, surface the success toast, and
 * add the validator tab when cross-checks arrived. Takes the in-progress
 * `agents`/`agentTabOrder` so any per-agent slot added earlier in the same
 * reducer pass (via handlePerAgentEvent) isn't clobbered.
 */
function handleRunComplete(
  state: AppState,
  rc: RunCompleteData,
  currentAgents: Record<string, AgentState>,
  currentTabOrder: string[],
): Partial<AppState> {
  const currentTokens = state.tokens;
  const out: Partial<AppState> = {
    isComplete: true,
    isRunning: false,
    complete: {
      success: rc.success,
      output_path: "",
      excel_path: rc.merged_workbook || "",
      trace_path: "",
      total_tokens: currentTokens?.cumulative ?? 0,
      cost: currentTokens?.cost_estimate ?? 0,
      statementsCompleted: rc.statements_completed,
      // Preserve the backend's failure reason (validation/model-setup paths
      // in server.py emit `{success: false, message}`). Without this the
      // diagnostic falls off the floor and the UI shows a bare "Failed".
      error: !rc.success && rc.message ? rc.message : null,
    } as CompleteData,
    crossChecks: rc.cross_checks || [],
    crossChecksPartial: rc.cross_checks_partial || false,
  };
  // Phase 9: success toast only — the red path shows its error in-panel.
  if (rc.success) {
    out.toast = { message: "Run completed successfully", tone: "success" };
  }

  // Start from the caller's in-progress snapshot so any slot added in
  // this reducer pass (notes reconciliation below, or validator tab) is
  // layered on consistently. We only commit out.agents / out.agentTabOrder
  // if something actually changed — avoids gratuitous object-identity
  // churn when a run has no cross-checks and no notes rollups.
  let agents = currentAgents;
  let tabOrder = currentTabOrder;
  let mutated = false;

  // Notes-tab reconciliation (peer-review #3). When the notes coordinator
  // crashes before per-agent `complete` events land, the backend still
  // synthesises `NotesAgentResult` entries and ships them in the
  // run_complete payload as `notes_completed` / `notes_failed`. Without
  // this block those tabs sit at "pending" forever even though the
  // backend has already assigned them a terminal state. We materialise
  // any missing tab and flip it to its terminal status — but do not
  // overwrite a tab that already reached a terminal state via its own
  // per-agent `complete` event (the live-event reducer is authoritative
  // when it fired; this is only a backstop for silent drops).
  const TERMINAL: AgentTabStatus[] = ["complete", "failed", "cancelled"];
  const reconcileNotes = (templateValue: string, nextStatus: AgentTabStatus) => {
    const agentId = `notes:${templateValue}`;
    const ensured = ensureAgent(agents, tabOrder, agentId, templateValue);
    agents = ensured.agents;
    tabOrder = ensured.tabOrder;
    const existing = agents[agentId];
    // Already terminal from its own complete event — don't clobber.
    if (TERMINAL.includes(existing.status)) return;
    agents = { ...agents, [agentId]: { ...existing, status: nextStatus } };
    mutated = true;
  };
  for (const t of rc.notes_completed ?? []) reconcileNotes(t, "complete");
  for (const t of rc.notes_failed ?? []) reconcileNotes(t, "failed");

  // Bug 1 — statement-tab backstop. Same shape as the notes reconcile above:
  // on Windows (enterprise proxy) per-agent `complete` SSE events can get
  // buffered/dropped when the stream closes, leaving face tabs stuck at
  // "running" even though the backend finished, merged and cross-checked.
  // The server always ships `statements_completed` / `statements_failed` on
  // run_complete, so we materialise missing tabs and flip any that are still
  // non-terminal. A tab that reached terminal via its own live complete
  // event is left alone — the live event is authoritative (it carries the
  // actual error on failure; the rollup array carries only the name).
  const reconcileStatement = (role: string, nextStatus: AgentTabStatus) => {
    // Face agent_ids are the lowercase statement name — mirror what
    // coordinator.py emits (`stmt_type.value.lower()`).
    const agentId = role.toLowerCase();
    const ensured = ensureAgent(agents, tabOrder, agentId, role);
    agents = ensured.agents;
    tabOrder = ensured.tabOrder;
    const existing = agents[agentId];
    if (TERMINAL.includes(existing.status)) return;
    agents = { ...agents, [agentId]: { ...existing, status: nextStatus } };
    mutated = true;
  };
  for (const s of rc.statements_completed ?? []) reconcileStatement(s, "complete");
  for (const s of rc.statements_failed ?? []) reconcileStatement(s, "failed");

  if (rc.cross_checks && rc.cross_checks.length > 0) {
    const ensured = ensureAgent(agents, tabOrder, "validator", "validator");
    agents = ensured.agents;
    tabOrder = ensured.tabOrder;
    agents = { ...agents, validator: { ...agents.validator, status: "complete" } };
    mutated = true;
  }

  if (mutated) {
    out.agents = agents;
    out.agentTabOrder = tabOrder;
  }
  return out;
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
        // PLAN-persistent-draft-uploads.md: UPLOADED is dispatched both
        // on first-upload AND on rehydration from `/run/{id}`. Preserving
        // currentRunId keeps the shareable URL alive across the dispatch
        // — without this the URL effect snaps back to `/` mid-rehydration
        // and the address bar lies about what the user is looking at.
        currentRunId: state.currentRunId,
        sessionId: action.payload.sessionId,
        filename: action.payload.filename,
        // Peer-review #3 (HIGH): record which run id this sessionId
        // belongs to. Falls back to currentRunId when payload doesn't
        // carry runId (legacy callers); same-tab uploads pass it
        // explicitly so the rehydrate effect can tell "fresh upload
        // for this run" from "stale session from a prior upload".
        sessionRunId:
          action.payload.runId !== undefined
            ? action.payload.runId
            : state.currentRunId,
      };

    case "RUN_STARTED": {
      const stmts = action.payload?.statements || [];
      const notes = action.payload?.notes || [];
      return {
        ...state,
        isRunning: true,
        runStartTime: Date.now(),
        statementsInRun: stmts,
        notesInRun: notes,
        lastRunConfig: action.payload?.config || state.lastRunConfig,
      };
    }

    case "SET_ACTIVE_TAB":
      return { ...state, activeTab: action.payload };

    case "SET_VIEW":
      return { ...state, view: action.payload };

    case "SET_SELECTED_RUN_ID":
      // Clearing selection (payload === null) returns the user to the
      // history list; setting a number opens the full-page run detail.
      // Kept purely as a state update — the URL round-trip happens in an
      // App-level effect watching this field.
      return { ...state, selectedRunId: action.payload };

    case "SET_CURRENT_RUN_ID":
      // Persistent-draft `/run/{id}` URL lives here. Setting a non-null
      // value tells ExtractPage to fetch + rehydrate that run; clearing
      // it brings the user back to the bare /` extract page (e.g. after
      // a "Start new extraction" reset).
      return { ...state, currentRunId: action.payload };

    case "DISMISS_TOAST":
      return { ...state, toast: null };

    case "EVENT": {
      const event = action.payload;
      // Always accumulate in the global event list
      const updates: Partial<AppState> = {
        events: [...state.events, event],
      };

      // Route to per-agent state if event has agent_id
      const perAgentUpdates = handlePerAgentEvent(state, event);
      if (perAgentUpdates) {
        Object.assign(updates, perAgentUpdates);
      }

      // Shared streaming state (currentPhase, toolTimeline) for the global view
      const globalStreamingUpdates = applyStreamingEvent(state, event);
      if (globalStreamingUpdates) {
        Object.assign(updates, globalStreamingUpdates);
      }

      switch (event.event) {
        case "token_update":
          // Aggregate across agents when the event carries an agent_id;
          // otherwise treat it as a legacy single-agent token payload.
          updates.tokens = getAgentId(event)
            ? aggregateTokens(updates.agents ?? state.agents)
            : event.data;
          break;

        case "error":
          // Per-agent errors (with agent_id) are already routed to
          // agentReducer above and must not kill the global run —
          // other agents may still be running. Only promote to global
          // failure when the error is not scoped to a specific agent.
          if (!getAgentId(event)) {
            updates.hasError = true;
            updates.error = event.data;
            updates.isRunning = false;
          }
          break;

        case "complete":
          // Multi-agent: handled by agentReducer above; in single-agent
          // mode this IS the terminal event. AgentCompleteData always
          // carries agent_id; CompleteData never does — so a missing
          // agent_id unambiguously identifies the legacy path.
          if (!event.data.agent_id) {
            updates.isComplete = true;
            updates.isRunning = false;
            updates.complete = event.data as CompleteData;
          }
          break;

        case "run_complete":
          Object.assign(
            updates,
            handleRunComplete(
              state,
              event.data,
              updates.agents ?? state.agents,
              updates.agentTabOrder ?? state.agentTabOrder,
            ),
          );
          break;
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
        crossChecksPartial: false,
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
