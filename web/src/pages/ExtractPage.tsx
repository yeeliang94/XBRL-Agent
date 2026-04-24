import { useCallback, useMemo, useState } from "react";
import type { RunConfigPayload } from "../lib/types";
import { pwc } from "../lib/theme";
import type { AppState, AppAction } from "../lib/appReducer";
import { notesTabLabel, agentSubAgentSummary } from "../lib/appReducer";
import { getResultJson, getExtendedSettings } from "../lib/api";
import { UploadPanel } from "../components/UploadPanel";
import { PreRunPanel } from "../components/PreRunPanel";
import { PipelineStages } from "../components/PipelineStages";
import { AgentTimeline } from "../components/AgentTimeline";
import { TokenDashboard } from "../components/TokenDashboard";
import { ResultsView } from "../components/ResultsView";
import { AgentTabs } from "../components/AgentTabs";
import type { AgentTabState } from "../components/AgentTabs";
import { ValidatorTab } from "../components/ValidatorTab";
import { NotesSubTabBar } from "../components/NotesSubTabBar";
import { buildToolTimeline, filterEventsBySubAgent } from "../lib/buildToolTimeline";
import { NOTES_12_AGENT_ID, isNotes12AgentId } from "../lib/notes";
import { isNonAgentTab } from "../lib/agentTabKinds";

// Re-export so existing callers / tests that imported NOTES_12_AGENT_ID
// from ExtractPage keep working. The single source of truth lives in
// lib/notes.ts — this module just gates with it.
export { NOTES_12_AGENT_ID };

// ---------------------------------------------------------------------------
// ExtractPage — the extraction workspace. Split out from App so the header
// and TopNav can stay mounted while switching between Extract and History
// without re-mounting the run pipeline UI.
// ---------------------------------------------------------------------------

// Minimal local alias — avoids circular import of the real UploadResponse type
// while keeping the prop type honest.
type UploadResponseShape = { session_id: string; filename: string };

export interface ExtractPageProps {
  state: AppState;
  dispatch: React.Dispatch<AppAction>;
  handleUpload: (file: File) => Promise<UploadResponseShape>;
  handleMultiRun: (config: RunConfigPayload) => void;
  handleAbortAll: () => Promise<void>;
  handleAbortAgent: (agentId: string) => Promise<void>;
  handleRerunAgent: (agentId: string) => void;
  handleReset: () => void;
}

export function ExtractPage({
  state,
  dispatch,
  handleUpload,
  handleMultiRun,
  handleAbortAll,
  handleAbortAgent,
  handleRerunAgent,
  handleReset,
}: ExtractPageProps) {
  // Memoize the tab-bar props so token-delta events don't churn references.
  // AgentTabs itself is React.memo — without stable refs the memo never fires.
  const agentTabsAgents = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(state.agents).map(([id, a]) => [
          id,
          {
            agentId: a.agentId,
            label: a.label,
            status: a.status,
            role: a.role,
            // Phase 5.2 / peer-review [M1]: only Notes-12's fan-out
            // populates sub-agent batch metadata today; for every other
            // agent `agentSubAgentSummary` returns null and the tab
            // renders as a single-line label as before.
            subLabel: agentSubAgentSummary(a),
          } as AgentTabState,
        ]),
      ),
    [state.agents],
  );
  const agentTabsSkeletons = useMemo(
    () =>
      state.statementsInRun.filter(
        (st) => !state.agentTabOrder.some((id) => state.agents[id]?.role === st),
      ),
    [state.statementsInRun, state.agentTabOrder, state.agents],
  );
  // PLAN §4 Phase D.3: notes skeleton labels — one per selected notes
  // template that hasn't emitted an event yet. Uses the shared
  // notesTabLabel() helper so skeletons, live tabs, and history all pull
  // from the same source of truth.
  const notesSkeletonLabels = useMemo(() => {
    return state.notesInRun
      .filter((n) => !state.agentTabOrder.some((id) => state.agents[id]?.role === n))
      .map((n) => notesTabLabel(n));
  }, [state.notesInRun, state.agentTabOrder, state.agents]);
  // Stable tab-click handler. `dispatch` from useReducer is guaranteed
  // stable, so this callback's identity doesn't change — without this,
  // AgentTabs' React.memo bails on every SSE event because the inline
  // arrow flips identity each render.
  const handleTabClick = useCallback(
    (id: string) => dispatch({ type: "SET_ACTIVE_TAB", payload: id }),
    [dispatch],
  );
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

      {/* Agent tabs + monitor. The activity shell renders whenever a run
          is in flight (`isRunning`) OR any agent tab has already been
          seeded (`agentTabOrder.length > 0`). The `isRunning` half is
          load-bearing: `RUN_STARTED` flips isRunning true but does NOT
          seed `agents` / `agentTabOrder` — those only get populated when
          the first SSE event with an `agent_id` arrives. Without the
          isRunning half, Stop all would be invisible during the window
          between RUN_STARTED and the first event, which can stretch on
          Windows behind the enterprise proxy while LiteLLM/model creation
          initialises (the one window where users most need to abort).
          `AgentTimeline` shows its own "Waiting for the agent to start…"
          placeholder when events are still empty, so the empty state is
          graceful. */}
      {(state.isRunning || state.agentTabOrder.length > 0) && (
        <div style={styles.activitySection}>
          <AgentTabs
            agents={agentTabsAgents}
            tabOrder={state.agentTabOrder}
            // Fallback to "" when both activeTab and agentTabOrder are empty
            // (the no-event window). No tab will match "", which is fine —
            // the strip renders only the skeleton tabs for selected
            // statements/notes until the first agent_id event lands.
            activeTab={state.activeTab || state.agentTabOrder[0] || ""}
            onTabClick={handleTabClick}
            // Phase 8: gate statement tabs so nothing shows until a run starts.
            statementsInRun={state.statementsInRun}
            skeletonTabs={agentTabsSkeletons}
            // PLAN §4 Phase D.3: notes gate mirrors the face gate.
            notesInRun={state.notesInRun}
            notesSkeletons={notesSkeletonLabels}
          />

          <ActiveTabPanel
            state={state}
            onAbortAll={handleAbortAll}
            onAbortAgent={handleAbortAgent}
            onRerunAgent={handleRerunAgent}
          />
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

// ---------------------------------------------------------------------------
// ActiveTabPanel — renders the card attached under the tab bar. The active
// tab picks between the validator results and the per-agent timeline; when
// no tab is selected we fall back to the global event stream (single-agent
// mode). Extracted from a nested IIFE in ExtractPage so the routing logic is
// readable top-to-bottom and the component can be tested independently.
// ---------------------------------------------------------------------------

export interface ActiveTabPanelProps {
  state: AppState;
  // Optional toolbar callbacks. When omitted the activity header renders
  // the title + count only — keeps existing tests (which construct the
  // panel with just `state`) compatible.
  onAbortAll?: () => void;
  onAbortAgent?: (agentId: string) => void;
  onRerunAgent?: (agentId: string) => void;
}

export function ActiveTabPanel({
  state,
  onAbortAll,
  onAbortAgent,
  onRerunAgent,
}: ActiveTabPanelProps) {
  // Sheet-12 sub-agent selection. null === "All" (the current/legacy view
  // that lumps every sub-agent's events together). Selection persists
  // across tab switches — flipping to SOFP and back keeps the same sub
  // highlighted, which matches the "preserve context" feel of the main
  // tab bar. The state lives here (not on AppState) because it's purely a
  // UI detail that doesn't need to survive navigation away from /extract.
  const [notes12SubId, setNotes12SubId] = useState<string | null>(null);

  // Stop-all is meaningful regardless of which tab is active; surfaced on
  // every header (validator included) so users always have one click out.
  const showStopAll = state.isRunning && !!onAbortAll;

  // All hooks MUST run before any conditional return. The validator-tab
  // early return further down used to sit above this useMemo, which
  // changed the hook count across renders whenever the user switched
  // to/from the Cross-checks tab and crashed the app with "Rendered
  // fewer hooks than expected during the previous render." (React 18
  // unmounts the whole tree on an unhandled render error, producing the
  // blank page). Computing these values on the validator branch is a
  // couple of wasted array references — cheap, and the values are
  // simply ignored because the early return below doesn't use them.
  const activeAgent = state.activeTab ? state.agents[state.activeTab] : null;
  const subAgents =
    isNotes12AgentId(state.activeTab) && activeAgent?.subAgentBatchRanges
      ? activeAgent.subAgentBatchRanges
      : [];
  const showSubTabs = subAgents.length > 0;
  const rawEvents = activeAgent ? activeAgent.events : state.events;
  const running = activeAgent ? activeAgent.status === "running" : state.isRunning;
  const aggregateTimeline = activeAgent ? activeAgent.toolTimeline : state.toolTimeline;

  // Memoised sub-agent filter — avoids redoing O(N) filter + timeline
  // rebuild on unrelated rerenders (e.g. token-delta churn from other
  // agents). For the "All" view we reuse the pre-computed aggregate
  // timeline and skip the rebuild entirely.
  const { events, toolTimeline } = useMemo(() => {
    if (showSubTabs && notes12SubId !== null) {
      const filtered = filterEventsBySubAgent(rawEvents, notes12SubId);
      return { events: filtered, toolTimeline: buildToolTimeline(filtered) };
    }
    return { events: rawEvents, toolTimeline: aggregateTimeline };
  }, [rawEvents, notes12SubId, showSubTabs, aggregateTimeline]);

  if (state.activeTab === "validator") {
    return (
      <div style={styles.activityCardAttached}>
        <div style={styles.activityHeader}>
          <span style={styles.activityTitle}>Cross-checks</span>
          <div style={styles.activityHeaderRight}>
            <span style={styles.activityCount}>
              {state.crossChecks.length} checks
            </span>
            {showStopAll && (
              <button
                type="button"
                onClick={onAbortAll}
                style={{ ...styles.toolbarBtnBase, ...styles.destructiveBtn }}
                title="Stop all running agents"
              >
                Stop all
              </button>
            )}
          </div>
        </div>
        <ValidatorTab crossChecks={state.crossChecks} partial={state.crossChecksPartial} />
      </div>
    );
  }

  // Capture as a non-null local so the click handlers below can pass the
  // agent id through without TS re-narrowing on each callback. `state.activeTab`
  // can flip to null between render and click in theory, but the buttons
  // themselves only render when this is truthy.
  const activeTabId = state.activeTab ?? "";
  const isControllable = !!activeTabId && !isNonAgentTab(activeTabId);
  const showStop =
    isControllable && activeAgent?.status === "running" && !!onAbortAgent;
  const showRerun =
    isControllable &&
    !state.isRunning &&
    (activeAgent?.status === "failed" || activeAgent?.status === "cancelled") &&
    !!onRerunAgent;

  return (
    <div style={styles.activityCardAttached}>
      <div style={styles.activityHeader}>
        <div style={styles.activityHeaderLeft}>
          <span style={styles.activityTitle}>Agent Activity</span>
          {showStop && (
            <button
              type="button"
              onClick={() => onAbortAgent!(activeTabId)}
              style={{ ...styles.toolbarBtnBase, ...styles.destructiveBtn }}
              title={`Stop ${activeAgent?.label ?? "agent"}`}
              aria-label={`Stop ${activeAgent?.label ?? "agent"}`}
            >
              Stop
            </button>
          )}
          {showRerun && (
            <button
              type="button"
              onClick={() => onRerunAgent!(activeTabId)}
              style={{ ...styles.toolbarBtnBase, ...styles.primaryBtn }}
              title={`Rerun ${activeAgent?.label ?? "agent"}`}
              aria-label={`Rerun ${activeAgent?.label ?? "agent"}`}
            >
              Rerun
            </button>
          )}
        </div>
        <div style={styles.activityHeaderRight}>
          <span style={styles.activityCount}>
            {events.length} {events.length === 1 ? "event" : "events"}
          </span>
          {showStopAll && (
            <button
              type="button"
              onClick={onAbortAll}
              style={{ ...styles.toolbarBtnBase, ...styles.destructiveBtn }}
              title="Stop all running agents"
            >
              Stop all
            </button>
          )}
        </div>
      </div>
      {showSubTabs && (
        <NotesSubTabBar
          subAgents={subAgents}
          activeSubId={notes12SubId}
          onSelect={setNotes12SubId}
        />
      )}
      <AgentTimeline
        events={events}
        toolTimeline={toolTimeline}
        isRunning={running}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline styles — scoped to the extract workspace. App-level chrome styles
// (page/header/main) live in App.tsx.
// ---------------------------------------------------------------------------

const styles = {
  activitySection: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 0,
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
    gap: pwc.space.md,
    padding: `${pwc.space.md}px ${pwc.space.lg}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
    background: pwc.grey50,
  } as const,
  activityHeaderLeft: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
    minWidth: 0,
  } as const,
  activityHeaderRight: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
  } as const,
  activityTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey900,
    whiteSpace: "nowrap" as const,
  } as const,
  activityCount: {
    fontFamily: pwc.fontMono,
    fontSize: 11,
    color: pwc.grey500,
  } as const,
  // Compact ghost buttons sitting in the activity-header toolbar. Sized
  // to read as toolbar actions, not page CTAs. `destructiveBtn` powers
  // both `stopBtn` and `stopAllBtn` (same visual contract); `primaryBtn`
  // powers `rerunBtn`. Spread base + variant inline at use sites.
  toolbarBtnBase: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: 600,
    borderRadius: pwc.radius.sm,
    padding: `2px ${pwc.space.sm}px`,
    cursor: "pointer",
    lineHeight: 1.4,
  } as const,
  destructiveBtn: {
    color: pwc.errorText,
    background: pwc.errorBg,
    border: `1px solid ${pwc.errorBorder}`,
  } as const,
  primaryBtn: {
    color: pwc.orange700,
    background: pwc.orange50,
    border: `1px solid ${pwc.orange400}`,
  } as const,
  errorBox: {
    background: pwc.errorBg,
    border: `1px solid ${pwc.errorBorder}`,
    borderRadius: pwc.radius.md,
    padding: pwc.space.lg,
  } as const,
  errorTitle: {
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    color: pwc.errorText,
    fontSize: 15,
    margin: 0,
  } as const,
  errorMessage: {
    fontFamily: pwc.fontBody,
    color: pwc.errorTextAlt,
    fontSize: 14,
    marginTop: pwc.space.xs,
  } as const,
  errorTraceback: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.errorTextAlt,
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
