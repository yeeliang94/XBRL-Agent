import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RunConfigPayload } from "../lib/types";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import type { AppState, AppAction } from "../lib/appReducer";
import { notesTabLabel, agentSubAgentSummary } from "../lib/appReducer";
import { fetchRunDetail, getResultJson, getExtendedSettings } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { UploadPanel } from "../components/UploadPanel";
import { HomeHero } from "../components/HomeHero";
import { PreRunPanel } from "../components/PreRunPanel";
import { PipelineStages } from "../components/PipelineStages";
import { AgentTimeline } from "../components/AgentTimeline";
import { TokenDashboard } from "../components/TokenDashboard";
import { ResultsView } from "../components/ResultsView";
import { AgentTabs } from "../components/AgentTabs";
import type { AgentTabState } from "../components/AgentTabs";
import { ValidatorTab } from "../components/ValidatorTab";
import { NotesSubTabBar } from "../components/NotesSubTabBar";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { buildToolTimeline, filterEventsBySubAgent } from "../lib/buildToolTimeline";
import { NOTES_12_AGENT_ID, isNotes12AgentId } from "../lib/notes";
import { isNonAgentTab } from "../lib/agentTabKinds";
import { TERMS } from "../lib/vocabulary";
import { describePdfSidecar } from "../lib/pdfSidecar";

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
// while keeping the prop type honest. run_id is forward-compat for the
// persistent-draft contract; existing callers still receive it on success.
type UploadResponseShape = { session_id: string; filename: string; run_id: number | null };

function liveStageMessage(stage: AppState["pipelineStage"]): string {
  switch (stage) {
    case "scouting": return "Scanning the document and preparing page guidance.";
    case "reading_source": return "Reading the source Word document.";
    case "merging": return "Combining completed statements into one Excel file.";
    case "cross_checking": return "Running cross-checks across the extracted statements.";
    case "correcting": return "Reviewing flagged figures against the source document.";
    case "reviewing": return "Tracing flagged figures back to the source document.";
    case "re_checking": return "Re-running cross-checks after the review.";
    case "reviewing_notes": return "Checking extracted notes against the source document.";
    case "formatting_notes": return "Standardising note formatting for mTool.";
    case "validating_notes": return "Validating the completed notes templates.";
    case "done": return "All run stages have finished.";
    default: return "Agents are working in parallel across the selected statements and notes.";
  }
}

export interface ExtractPageProps {
  state: AppState;
  dispatch: React.Dispatch<AppAction>;
  handleUpload: (file: File) => Promise<UploadResponseShape>;
  handleMultiRun: (config: RunConfigPayload) => void;
  handleAbortAll: () => Promise<void>;
  handleAbortAgent: (agentId: string) => Promise<void>;
  handleRerunAgent: (agentId: string) => void;
  handleReset: () => void;
  /** Peer-review #4 (MEDIUM, RUN-REVIEW follow-up): debounced
   *  callback the parent wires to `patchRunConfig(currentRunId, …)`.
   *  Forwarded to PreRunPanel so the persistent-draft contract holds
   *  across refresh/share — pre-fix the only PATCH happened on Run-
   *  click. Optional so legacy callers that never touched the draft
   *  keep working (the no-PATCH path is still valid for ad-hoc
   *  legacy `/` uploads where there's no run id to update). */
  handleConfigChange?: (config: RunConfigPayload) => void;
  /** Homepage split hero (PLAN-homepage-redesign.md): navigation callbacks
   *  for the home-base column. Optional so legacy/test callers that don't
   *  wire them still mount — the column simply won't navigate. App supplies
   *  the same dispatch actions History uses for these jumps. */
  onResumeDraft?: (runId: number) => void;
  onOpenRun?: (runId: number) => void;
  onViewAllRuns?: () => void;
  /** Whether the current user is an admin — gates admin-only pre-run controls
   *  (eval/benchmark grading) inside the Advanced disclosure (Phase 3). */
  isAdmin?: boolean;
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
  handleConfigChange,
  onResumeDraft,
  onOpenRun,
  onViewAllRuns,
  isAdmin = false,
}: ExtractPageProps) {
  // PLAN-persistent-draft-uploads.md (Phase C): when the URL is /run/{id}
  // (currentRunId is non-null on mount) and we have not yet loaded that
  // run's details, fetch the row from the audit DB and seed the workspace
  // with its filename + session_id. This is the load-bearing rehydration
  // for "refresh the page and your upload is still there" — without it
  // the upload card sits empty on every refresh.
  //
  // We compare against state.sessionId (already set when uploading on the
  // same tab) to avoid re-fetching when the user just dropped the file.
  // The ref guards against React-StrictMode double-mounting in dev so we
  // do not fire two GET /api/runs/{id} calls.
  //
  // Peer-review MEDIUM #5: the fetched config blob is held here so it can
  // be passed to PreRunPanel as `initialConfig`. PreRunPanel mounts only
  // after `state.sessionId` is set — we dispatch UPLOADED *after* the
  // config lands in this state so the panel sees the saved config on
  // its very first render.
  const rehydratedRunIdRef = useRef<number | null>(null);
  const [draftConfig, setDraftConfig] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    const id = state.currentRunId;
    if (id == null) {
      // Navigated away from /run/{id} (e.g. Reset / new upload) — clear
      // any prior draft config so the next mount starts fresh.
      setDraftConfig(null);
      return;
    }
    if (rehydratedRunIdRef.current === id) return;
    // Peer-review #3 (HIGH): only treat the existing sessionId as
    // belonging to THIS run when sessionRunId matches. Otherwise it's
    // stale state from a prior upload — navigating from /run/A to
    // /run/B would have skipped the fetch and silently kept A's
    // session, so scout would have run against the wrong PDF.
    if (state.sessionId && state.sessionRunId === id) {
      // Same-tab upload (or already-rehydrated): sessionId is current.
      rehydratedRunIdRef.current = id;
      return;
    }
    let cancelled = false;
    rehydratedRunIdRef.current = id;
    fetchRunDetail(id)
      .then((detail) => {
        if (cancelled) return;
        // A shared /run/{id} link only means "resume this upload" for an
        // actual DRAFT. For a completed / running / failed run, dropping the
        // user into a blank "Start extraction" config panel looks like they're
        // about to re-run a finished job — so redirect to that run's detail
        // view instead (docs/PLAN-design-qa-fixes.md R1).
        if (detail.status && detail.status !== "draft") {
          rehydratedRunIdRef.current = null;
          dispatch({ type: "SET_VIEW", payload: "history" });
          dispatch({ type: "SET_SELECTED_RUN_ID", payload: id });
          dispatch({ type: "SET_CURRENT_RUN_ID", payload: null });
          return;
        }
        // Stash the saved config first so PreRunPanel sees it on its
        // first render after the UPLOADED dispatch below.
        setDraftConfig(detail.config ?? null);
        // UPLOADED is the existing action that seeds sessionId + filename;
        // re-using it keeps the rehydration path identical to a same-tab
        // upload so all downstream effects (PreRunPanel mount, scout flow)
        // pick up state without special-casing.
        dispatch({
          type: "UPLOADED",
          payload: {
            sessionId: detail.session_id,
            filename: detail.pdf_filename,
            // Pin ownership: this sessionId was just fetched FOR this id.
            runId: id,
          },
        });
      })
      .catch(() => {
        // Non-existent run id or audit DB hiccup — clear the ref so the
        // user can navigate away and back to retry. We do not surface a
        // visible error here because /run/{garbage} also lands in this
        // branch, and silently degrading to the bare extract page is
        // gentler than a banner the user can't action.
        rehydratedRunIdRef.current = null;
      });
    return () => {
      cancelled = true;
    };
  }, [state.currentRunId, state.sessionId, state.sessionRunId, dispatch]);

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
            // Honest-completion flag (peer-review F1): forward the
            // acknowledged-gap flag so the tab renders the "needs review"
            // ⚠ chip. Null for the common (clean-completion) case.
            flag: a.flag ?? null,
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
  const workstreamSummary = useMemo(() => {
    const agents = Object.values(agentTabsAgents);
    const requestedRoles = new Set([...state.statementsInRun, ...state.notesInRun]);
    const requestedAgents = agents.filter((agent) => requestedRoles.has(agent.role));
    const monitoredAgents = requestedRoles.size > 0 ? requestedAgents : agents;
    return {
      total: requestedRoles.size > 0 ? requestedRoles.size : agents.length,
      complete: monitoredAgents.filter((agent) => agent.status === "complete").length,
      running: monitoredAgents.filter((agent) => agent.status === "running" || agent.status === "aborting").length,
      // Run-check agents are deliberately outside the requested-workstream
      // denominator, but their failures must still be visible to operators.
      attention: agents.filter((agent) => agent.status === "failed" || agent.flag).length,
    };
  }, [agentTabsAgents, state.statementsInRun, state.notesInRun]);
  // Stable tab-click handler. `dispatch` from useReducer is guaranteed
  // stable, so this callback's identity doesn't change — without this,
  // AgentTabs' React.memo bails on every SSE event because the inline
  // arrow flips identity each render.
  const handleTabClick = useCallback(
    (id: string) => dispatch({ type: "SET_ACTIVE_TAB", payload: id }),
    [dispatch],
  );
  // docs/PLAN-pdf-source-sidecar.md: scanned-PDF source transcript notice.
  // Emitted once before the notes agents launch, only when the Settings
  // toggle is on and the PDF is a scan. Advisory in both outcomes.
  const sidecarNotice = state.pdfSidecar ? describePdfSidecar(state.pdfSidecar) : null;
  return (
    <>
      <PageHeader
        title={state.isRunning ? "Extraction in progress" : state.isComplete ? "Extraction complete" : TERMS.newExtraction}
        description={state.isRunning
          ? "Monitor the overall run, then inspect a workstream only when you need more detail."
          : "Upload an audited financial statement to create an SSM MBRS filing draft for review."}
      />

      {/* Upload + Run. In the empty landing state the upload card sits in
          the left column of HomeHero, with the "home base" (recent runs +
          stat tiles) on the right. Once an upload exists or a run starts
          (`active` goes false), HomeHero collapses to just the upload card
          at full width — and crucially keeps UploadPanel mounted in the same
          tree position so its internal upload state survives the transition. */}
      <HomeHero
        active={state.sessionId == null && !state.isRunning}
        onResumeDraft={onResumeDraft ?? (() => {})}
        onOpenRun={onOpenRun ?? (() => {})}
        onViewAllRuns={onViewAllRuns ?? (() => {})}
      >
        <UploadPanel
          onUpload={handleUpload}
          isRunning={state.isRunning}
          filename={state.filename}
          startTime={state.runStartTime}
        />
      </HomeHero>

      {/* Pre-run configuration panel — shown after upload, hidden once running.
          A `key` tied to currentRunId forces a remount when the page navigates
          to a different /run/{id}, so PreRunPanel re-runs its useState
          initializers with the freshly-fetched draftConfig. Without this a
          back-then-forward between two drafts would leak the first one's
          state into the second's panel. */}
      {state.sessionId && !state.isRunning && !state.isComplete && !state.hasError && (
        <PreRunPanel
          key={state.currentRunId ?? "fresh"}
          sessionId={state.sessionId}
          getSettings={getExtendedSettings}
          onRun={handleMultiRun}
          initialConfig={draftConfig}
          onConfigChange={handleConfigChange}
          isAdmin={isAdmin}
        />
      )}

      {/* One run-level surface replaces the former stack of progress, stage,
          and token cards. Operators first see what the system is doing and
          whether any workstream needs attention; technical usage is secondary. */}
      {(state.isRunning || state.currentPhase || state.tokens) && (
        <section aria-labelledby="live-run-heading" style={styles.runOverview}>
          <div className="live-run-header" style={styles.runOverviewHeader}>
            <div style={styles.runOverviewLead}>
              <div style={styles.runEyebrow}>{state.isRunning ? "Live run" : "Run progress"}</div>
              <h2
                id="live-run-heading"
                data-testid="pipeline-stage-label"
                aria-live="polite"
                aria-atomic="true"
                style={styles.runOverviewTitle}
              >
                {state.isComplete
                  ? "Extraction finished"
                  : state.isRunning
                    ? liveStageMessage(state.pipelineStage)
                    : "Run stopped"}
              </h2>
              <p style={styles.runOverviewMessage}>
                {state.isComplete
                  ? "The filing draft is ready for review."
                  : state.isRunning
                    ? "This can take a few minutes. You can leave this page open while the run continues."
                    : "The run is no longer active. Review any errors below before trying again."}
              </p>
            </div>
            <div className="live-run-summary" style={styles.runSummary} aria-label="Workstream summary">
              <div style={styles.runSummaryPrimary}>
                {workstreamSummary.complete} of {workstreamSummary.total} complete
              </div>
              <div style={styles.runSummaryMeta}>
                {workstreamSummary.running} active
                {workstreamSummary.attention > 0 ? ` · ${workstreamSummary.attention} need attention` : ""}
              </div>
            </div>
          </div>

          {(state.isRunning || state.currentPhase) && (
            <PipelineStages
              currentPhase={state.currentPhase}
              pipelineStage={state.pipelineStage}
              isRunning={state.isRunning}
              isComplete={state.isComplete}
            />
          )}

          {(state.isRunning || state.tokens) && (
            <details style={styles.usageDisclosure}>
              <summary style={styles.usageSummary}>
                Usage and estimated cost
                <span style={styles.usageSummaryEnd}>
                  <span style={styles.usageSummaryValue}>
                    {state.tokens ? `$${state.tokens.cost_estimate.toFixed(4)}` : "Waiting for usage data"}
                  </span>
                  <span aria-hidden="true" style={styles.usageChevron} />
                </span>
              </summary>
              <TokenDashboard tokens={state.tokens} isRunning={state.isRunning} embedded />
            </details>
          )}
        </section>
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
        <div className="multi-agent-workspace" style={styles.activitySection}>
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

      {/* Scout-quality warnings banner. Surfaces the pre-flight completeness
          probe (scout_warnings) and scale-unit reconciliation (scale_conflict)
          findings so a degraded scout pack is visible to the operator before /
          during extraction. Advisory only — the run proceeds (gotcha #13).
          Reuses the warning palette; these are run-level (no agent tab). */}
      {state.scoutWarnings.length > 0 && (
        <div role="status" data-testid="scout-warnings-banner" style={styles.partialMergeBox}>
          <h3 style={styles.partialMergeTitle}>Document pre-scan warnings</h3>
          <p style={styles.partialMergeMessage}>
            The document scout flagged the following before extraction. These are
            advisory — verify against the PDF; the run still proceeds.
          </p>
          <ul style={{ margin: `${pwc.space.xs}px 0 0`, paddingLeft: pwc.space.lg }}>
            {state.scoutWarnings.map((w, i) => (
              <li key={i} style={styles.partialMergeMessage}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Same run-level palette as the scout banner; no agent tab (no agent_id). */}
      {sidecarNotice && (
        <div role="status" data-testid="pdf-sidecar-notice" style={styles.partialMergeBox}>
          <h3 style={styles.partialMergeTitle}>{sidecarNotice.title}</h3>
          <p style={styles.partialMergeMessage}>{sidecarNotice.message}</p>
        </div>
      )}

      {/* PLAN-stop-and-validation-visibility Phase 2.3: partial-merge banner.
          Surfaces immediately above the cancel error so the operator
          knows their work was preserved. Distinct (warning) palette
          keeps it visually separate from the red error block. */}
      {state.partialMerge && (
        state.partialMerge.merged
          || state.partialMerge.statements_included.length > 0
          || state.partialMerge.notes_included.length > 0
      ) && (
        <div role="status" data-testid="partial-merge-banner" style={styles.partialMergeBox}>
          <h3 style={styles.partialMergeTitle}>Saved partial workbook</h3>
          <p style={styles.partialMergeMessage}>
            {state.partialMerge.merged
              ? "Your run was stopped, but the agents that finished have been merged into a downloadable workbook."
              : "Your run was stopped. Some per-statement files survived on disk."}
          </p>
          {state.partialMerge.statements_included.length > 0 && (
            <p style={styles.partialMergeMessage}>
              <strong>Included:</strong>{" "}
              {state.partialMerge.statements_included.join(", ")}
              {state.partialMerge.notes_included.length > 0 && (
                <>{" + notes "}{state.partialMerge.notes_included.join(", ")}</>
              )}
            </p>
          )}
          {state.partialMerge.statements_missing.length > 0 && (
            <p style={styles.partialMergeMessage}>
              <strong>Missing (incomplete when stopped):</strong>{" "}
              {state.partialMerge.statements_missing.join(", ")}
            </p>
          )}
          {state.partialMerge.error && (
            <p style={styles.partialMergeMessage}>
              <em>Note: {state.partialMerge.error}</em>
            </p>
          )}
        </div>
      )}

      {/* Error display. The headline is a plain sentence; the raw traceback is
          tucked into a collapsed "Technical details" disclosure so a
          non-technical user sees what happened, not a Python stack trace. */}
      {state.hasError && state.error && (
        <div style={styles.errorBox}>
          <h3 style={styles.errorTitle}>Extraction couldn't finish</h3>
          <p style={styles.errorMessage}>{userMessage(new Error(state.error.message))}</p>
          <p style={{ ...styles.errorMessage, color: pwc.grey700, fontSize: 14 }}>
            You can try starting the extraction again. If it keeps failing, share
            the technical details below with your support contact.
          </p>
          {state.error.traceback && (
            <details style={{ marginTop: pwc.space.sm }}>
              <summary style={{ color: pwc.grey700, fontSize: 13, cursor: "pointer" }}>
                Technical details (for support)
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
          // De-gated (was conditioned on canonicalEnabled): canonical mode is
          // mandatory now (gotcha #21), so the flag could only ever hide the
          // single bridge back to the review page after a run finished. Offer
          // the review link whenever we know the run id — access to your own
          // just-completed run must not depend on a global feature flag.
          runId={state.complete.runId ?? state.currentRunId}
          onViewConcepts={(id) => {
            dispatch({ type: "SET_VIEW", payload: "concepts" });
            dispatch({ type: "SET_SELECTED_RUN_ID", payload: id });
          }}
          // Flag-independent safety net: open the run's full tabbed detail
          // (/history/{id} — cross-checks, agents, telemetry, download) so the
          // results screen always leads into the complete review surface, not
          // just the Values tab. Reuses App's existing onOpenRun wiring.
          onOpenRunDetail={onOpenRun}
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
// ActiveTabPanel — renders the focused pane beside the workstream navigator.
// The selection picks between validator results and the per-agent timeline; when
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
  // across workstream switches — flipping to SOFP and back keeps the same
  // sub-agent highlighted. The state lives here (not on AppState) because it's purely a
  // UI detail that doesn't need to survive navigation away from /extract.
  const [notes12SubId, setNotes12SubId] = useState<string | null>(null);
  // Confirm before re-running one statement (it discards that statement's
  // current result and starts it over). Shared dialog, like every other
  // destructive action.
  const [confirmRerun, setConfirmRerun] = useState(false);

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
    // PLAN-stop-and-validation-visibility Phase 5.3: prefer the live
    // progress feed while the run is still going. Once `run_complete`
    // fires, `state.crossChecks` is the authoritative copy (it carries
    // the post-correction state if correction ran). The progress feed
    // mirrors the same shape minus phase/index/total — strip those so
    // ValidatorTab's existing table renders the live rows the same as
    // the final ones.
    const liveProgress = state.crossCheckProgress;
    const liveRows = state.crossChecks.length === 0 && liveProgress.results.length > 0
      ? liveProgress.results.map((r) => ({
          name: r.name,
          status: r.status,
          expected: r.expected,
          actual: r.actual,
          diff: r.diff,
          tolerance: r.tolerance,
          message: r.message,
        }))
      : state.crossChecks;
    const showLiveSubtitle = state.crossChecks.length === 0 && liveProgress.phase !== null;
    const phaseLabel = liveProgress.phase === "post_correction"
      ? "Re-checking after correction"
      : "Cross-checks";
    return (
      <div role="tabpanel" aria-label="Cross-check activity" style={styles.activityCardAttached}>
        <div style={styles.activityHeader}>
          <div style={styles.activityHeaderLeft}>
            <div>
              <div style={styles.activityEyebrow}>Selected workstream</div>
              <div style={styles.activityTitle}>
                {showLiveSubtitle ? phaseLabel : "Cross-checks"}
              </div>
            </div>
          </div>
          <div style={styles.activityHeaderRight}>
            <span style={styles.activityCount}>
              {showLiveSubtitle
                ? `${liveProgress.results.length} / ${liveProgress.total}${liveProgress.isComplete ? "" : "…"}`
                : `${state.crossChecks.length} checks`}
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
        <ValidatorTab crossChecks={liveRows} partial={state.crossChecksPartial} />
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
    <div
      role="tabpanel"
      aria-label={`${activeAgent?.label ?? "Run"} activity`}
      style={styles.activityCardAttached}
    >
      <div style={styles.activityHeader}>
        <div style={styles.activityHeaderLeft}>
          <div>
            <div style={styles.activityEyebrow}>Selected workstream</div>
            <div style={styles.activityTitle}>{activeAgent?.label ?? "Run activity"}</div>
          </div>
          {activeAgent && (
            <span style={styles.activeAgentStatus}>
              {activeAgent.status === "complete" ? "Complete" : activeAgent.status.charAt(0).toUpperCase() + activeAgent.status.slice(1)}
            </span>
          )}
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
              onClick={() => setConfirmRerun(true)}
              style={{ ...styles.toolbarBtnBase, ...styles.primaryBtn }}
              title={`Rerun ${activeAgent?.label ?? "agent"}`}
              aria-label={`Rerun ${activeAgent?.label ?? "agent"}`}
            >
              Rerun
            </button>
          )}
          <ConfirmDialog
            isOpen={confirmRerun}
            title={`Re-run ${activeAgent?.label ?? "this statement"}?`}
            message="This starts this statement over from scratch. Its current result is discarded and replaced by the new run."
            confirmLabel="Re-run"
            danger={false}
            onConfirm={() => {
              setConfirmRerun(false);
              onRerunAgent!(activeTabId);
            }}
            onCancel={() => setConfirmRerun(false)}
          />
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
  runOverview: {
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.lg,
    padding: pwc.space.xl,
  } as const,
  runOverviewHeader: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: pwc.space.xl,
    marginBottom: pwc.space.xl,
  } as const,
  runOverviewLead: {
    minWidth: 0,
  } as const,
  runEyebrow: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: pwc.weight.semibold,
    color: pwc.orange700,
    letterSpacing: "0.04em",
    textTransform: "uppercase" as const,
    marginBottom: pwc.space.xs,
  } as const,
  runOverviewTitle: {
    margin: 0,
    fontFamily: pwc.fontHeading,
    fontSize: 20,
    lineHeight: 1.3,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
  } as const,
  runOverviewMessage: {
    margin: `${pwc.space.xs}px 0 0`,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    lineHeight: 1.5,
    color: pwc.grey700,
  } as const,
  runSummary: {
    flexShrink: 0,
    minWidth: 160,
    paddingLeft: pwc.space.xl,
    borderLeft: `1px solid ${pwc.grey200}`,
    textAlign: "right" as const,
  } as const,
  runSummaryPrimary: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
  } as const,
  runSummaryMeta: {
    marginTop: pwc.space.xs,
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey700,
  } as const,
  usageDisclosure: {
    marginTop: pwc.space.lg,
    borderTop: `1px solid ${pwc.grey200}`,
  } as const,
  usageSummary: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
    paddingTop: pwc.space.md,
    cursor: "pointer",
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: pwc.weight.medium,
    color: pwc.grey700,
  } as const,
  usageSummaryValue: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    fontWeight: pwc.weight.regular,
    color: pwc.grey900,
  } as const,
  usageSummaryEnd: {
    marginLeft: "auto",
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as const,
  usageChevron: {
    width: 0,
    height: 0,
    borderLeft: "4px solid transparent",
    borderRight: "4px solid transparent",
    borderTop: `5px solid ${pwc.grey700}`,
  } as const,
  activitySection: {
    display: "grid",
    gridTemplateColumns: "minmax(240px, 280px) minmax(0, 1fr)",
    gap: 0,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.lg,
    overflow: "hidden" as const,
  } as const,
  activityCardAttached: {
    background: pwc.white,
    border: "none",
    borderLeft: `1px solid ${pwc.grey200}`,
    borderRadius: 0,
    overflow: "hidden" as const,
    display: "flex",
    flexDirection: "column" as const,
    minWidth: 0,
  } as const,
  activityHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
    padding: `${pwc.space.md}px ${pwc.space.lg}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
    background: pwc.white,
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
    fontSize: 16,
    fontWeight: 600,
    color: pwc.grey900,
    whiteSpace: "nowrap" as const,
  } as const,
  activityEyebrow: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey700,
    letterSpacing: "0.02em",
    marginBottom: 2,
  } as const,
  activeAgentStatus: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey700,
    paddingLeft: pwc.space.md,
    borderLeft: `1px solid ${pwc.grey200}`,
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
    color: pwc.error,
    background: pwc.white,
    border: `1px solid ${pwc.error}`,
  } as const,
  primaryBtn: {
    color: pwc.orange700,
    background: pwc.orange50,
    border: `1px solid ${pwc.orange400}`,
  } as const,
  errorBox: {
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderLeft: `3px solid ${pwc.error}`,
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
    color: pwc.grey800,
    fontSize: 14,
    marginTop: pwc.space.xs,
  } as const,
  errorTraceback: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey800,
    whiteSpace: "pre-wrap" as const,
    overflow: "auto",
    marginTop: pwc.space.sm,
  } as const,
  // PLAN-stop-and-validation-visibility Phase 2.3 — distinct palette
  // from errorBox so the user can tell "we saved your work" apart from
  // "your work failed". Warning amber matches the chip used for the
  // correction_exhausted run status (RUN-REVIEW P0-1).
  partialMergeBox: {
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderLeft: `3px solid ${pwc.warning}`,
    borderRadius: pwc.radius.md,
    padding: pwc.space.lg,
    marginBottom: pwc.space.md,
  } as const,
  partialMergeTitle: {
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    color: pwc.warningText,
    fontSize: 15,
    margin: 0,
  } as const,
  partialMergeMessage: {
    fontFamily: pwc.fontBody,
    color: pwc.grey800,
    fontSize: 14,
    marginTop: pwc.space.xs,
    marginBottom: 0,
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
