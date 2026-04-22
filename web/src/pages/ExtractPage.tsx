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

      {/* Agent tabs + monitor */}
      {state.agentTabOrder.length > 0 && (
        <div
          style={
            state.events.length > 0 ? styles.activitySection : undefined
          }
        >
          <div style={styles.tabBarCard}>
            <AgentTabs
              agents={agentTabsAgents}
              tabOrder={state.agentTabOrder}
              activeTab={state.activeTab || state.agentTabOrder[0]}
              onTabClick={handleTabClick}
              onAbortAgent={handleAbortAgent}
              onRerunAgent={handleRerunAgent}
              isRunning={state.isRunning}
              // Phase 8: gate statement tabs so nothing shows until a run starts.
              statementsInRun={state.statementsInRun}
              skeletonTabs={agentTabsSkeletons}
              // PLAN §4 Phase D.3: notes gate mirrors the face gate.
              notesInRun={state.notesInRun}
              notesSkeletons={notesSkeletonLabels}
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

          {state.events.length > 0 && <ActiveTabPanel state={state} />}
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

export function ActiveTabPanel({ state }: { state: AppState }) {
  // Sheet-12 sub-agent selection. null === "All" (the current/legacy view
  // that lumps every sub-agent's events together). Selection persists
  // across tab switches — flipping to SOFP and back keeps the same sub
  // highlighted, which matches the "preserve context" feel of the main
  // tab bar. The state lives here (not on AppState) because it's purely a
  // UI detail that doesn't need to survive navigation away from /extract.
  const [notes12SubId, setNotes12SubId] = useState<string | null>(null);

  if (state.activeTab === "validator") {
    return (
      <div style={styles.activityCardAttached}>
        <div style={styles.activityHeader}>
          <span style={styles.activityTitle}>Cross-checks</span>
          <span style={styles.activityCount}>
            {state.crossChecks.length} checks
          </span>
        </div>
        <ValidatorTab crossChecks={state.crossChecks} partial={state.crossChecksPartial} />
      </div>
    );
  }

  const activeAgent = state.activeTab ? state.agents[state.activeTab] : null;

  // Sheet-12 branch: show the nested sub-tab bar and route the AgentTimeline
  // through the filter when a specific sub is selected. Gated on the
  // sub-agent list being non-empty so a Notes-12 tab that hasn't split yet
  // (pre-first-`started` event) still renders a flat timeline.
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

  return (
    <div style={styles.activityCardAttached}>
      <div style={styles.activityHeader}>
        <span style={styles.activityTitle}>Agent Activity</span>
        <span style={styles.activityCount}>
          {events.length} {events.length === 1 ? "event" : "events"}
        </span>
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
