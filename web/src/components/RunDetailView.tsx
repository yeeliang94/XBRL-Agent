import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { pwc, tokens } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { PdfSourcePane } from "./PdfSourcePane";
import { parseEvidencePages } from "../lib/evidencePages";
import { ConceptsPage } from "../pages/ConceptsPage";
import type { ConceptRow } from "../pages/ConceptsPage";
import { runStatusDisplay, agentStatusDisplay, STATUS_SYMBOLS } from "../lib/runStatus";
import { errorGuidance } from "../lib/errorGuidance";
import { StatusIcon } from "./StatusIcon";
import type { RunStatusDisplay } from "../lib/runStatus";
import type { RunDetailJson, RunAgentJson, CrossCheckResult } from "../lib/types";
import { STATEMENT_LABELS } from "../lib/types";
import { AgentTelemetryPanel } from "./AgentTelemetryPanel";
import { ValidatorTab } from "./ValidatorTab";
import { ReviewTab } from "./ReviewTab";
import { MtoolFillModal } from "./MtoolFillModal";
import { HumanFileDialog } from "./HumanFileDialog";
import { getHumanFile, type HumanFileRecord } from "../lib/humanFile";
import type { Denomination } from "../lib/types";
import { ConfirmDialog } from "./ConfirmDialog";
import { AgentTimeline } from "./AgentTimeline";
import { NotesSubTabBar } from "./NotesSubTabBar";
import { TabPanelFade } from "./TabPanelFade";
import { NotesReviewerPanel } from "./NotesReviewerPanel";
import { NotesTablesPanel } from "./NotesTablesPanel";
import { NotesIntegrityPanel } from "./NotesIntegrityPanel";
import {
  buildToolTimeline,
  filterEventsBySubAgent,
  deriveSubAgentRangesFromEvents,
} from "../lib/buildToolTimeline";
import { buildReasoningTimeline } from "../lib/buildReasoningTimeline";
import { displayModelId } from "../lib/modelId";
import { notesTabLabel } from "../lib/appReducer";
import { formatAccounting, formatCost } from "../lib/numberFormat";
import { denominationLabel, pseudoAgentLabel, variantLabel, crossCheckFailureLabel } from "../lib/vocabulary";
import { isNotes12StatementType } from "../lib/notes";
import { statementCodeSubtitle, statementCodeOrder } from "../lib/sheetLabels";
import { describePdfSidecar } from "../lib/pdfSidecar";
import {
  readRunTabFromUrl,
  RUN_TAB_CHANGE_EVENT,
  writeRunTabToUrl,
} from "../lib/runTabs";
import type { RunTabKey } from "../lib/runTabs";

export type { RunTabKey } from "../lib/runTabs";

// ---------------------------------------------------------------------------
// RunDetailView — hydrated detail panel for a single past run.
//
// Pure presentational. The parent owns the detail payload (HistoryPage
// fetches it) and passes callbacks for the destructive / navigational
// actions. Cross-check rendering is reused from ValidatorTab so live runs
// and past runs share a single visual treatment.
// ---------------------------------------------------------------------------

export interface RunDetailViewProps {
  detail: RunDetailJson;
  onDownload?: (runId: number) => void;
  onDelete: (runId: number) => void;
  /** Resume configuration for an unstarted draft. */
  onResumeDraft?: (runId: number) => void;
  /** Rescue a run wedged in `running` status (UX-QA #2). When provided and the
   *  run is `running`, a "Stop run" control replaces the disabled Delete so a
   *  dead run isn't a dead-end. Optional — absent for callers that can't act. */
  onForceAbort?: (runId: number) => void;
  /** Clone this run into a new draft while retaining reusable source work. */
  onRestart?: (runId: number) => void | Promise<void>;
  /** Gate the review link on canonical mode so legacy runs (which
   *  have no concept tree) don't link to an empty page — matches the TopNav
   *  / Results gating (peer-review F6). Defaults to false: hidden unless the
   *  parent explicitly enables it. */
  canonicalEnabled?: boolean;
  /** Which tab to open on first render. Used by the `/concepts/{id}` alias
   *  to land directly on the Values tab. Defaults to "overview". */
  initialTab?: RunTabKey;
}

/** Render a monochrome status label from a precomputed display. Caller picks
 *  runStatusDisplay vs agentStatusDisplay so the right vocabulary is used
 *  in each context (run-level vs per-agent enums differ slightly). */
function statusBadge(display: RunStatusDisplay) {
  return (
    <span style={ui.status}>
      <StatusIcon symbol={display.symbol} />
      {display.label}
    </span>
  );
}

/** Render a nested config key/value section in a compact form. */
function ConfigBlock({
  config,
}: {
  config: Record<string, unknown> | null;
}) {
  if (!config) {
    return <p style={styles.dim}>No run config captured for this run.</p>;
  }
  const entries: { label: string; value: string }[] = [];
  const stmts = Array.isArray(config.statements)
    ? (config.statements as string[]).join(", ")
    : "—";
  entries.push({ label: "Statements", value: stmts });

  const variants = (config.variants ?? {}) as Record<string, string>;
  if (Object.keys(variants).length > 0) {
    entries.push({
      label: "Variants",
      // Plain-language variant names ("Order of liquidity" not
      // "OrderOfLiquidity"); the statement code (SOFP) is the operator's own
      // shorthand and stays (D2).
      value: Object.entries(variants)
        .map(([k, v]) => `${k}: ${variantLabel(v)}`)
        .join(", "),
    });
  }
  entries.push({
    label: "Filing level",
    value: (config.filing_level === "group" ? "Group" : "Company"),
  });
  entries.push({
    label: "Denomination",
    value: denominationLabel(config.denomination as string | undefined),
  });
  // Notes — only surface when the run actually selected any. Empty lists
  // would render as "Notes: —" for every face-only run, which is noise.
  const notesToRun = Array.isArray(config.notes_to_run)
    ? (config.notes_to_run as string[])
    : [];
  if (notesToRun.length > 0) {
    entries.push({
      label: "Notes",
      value: notesToRun.map((n) => notesTabLabel(n)).join(", "),
    });
  }
  return (
    <dl style={styles.dl}>
      {entries.map((e) => (
        <div key={e.label} style={styles.dlRow}>
          <dt style={styles.dt}>{e.label}</dt>
          <dd style={styles.dd}>{e.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Convert the wire-shape cross_checks into the shape ValidatorTab expects.
 *  They already match structurally — this is a type assertion cast kept
 *  explicit so a future divergence in either shape shows up as a compile
 *  error. */
function crossChecksForValidator(
  rows: RunDetailJson["cross_checks"],
): CrossCheckResult[] {
  return rows.map((r) => ({
    name: r.name,
    status: r.status,
    expected: r.expected,
    actual: r.actual,
    diff: r.diff,
    tolerance: r.tolerance,
    message: r.message,
    // Step 8 — carry the click-to-cell target through so a targeted check
    // is actually clickable on the History detail surface. Dropping these
    // (the original bug) left every row non-clickable despite backend support.
    target_sheet: r.target_sheet,
    target_row: r.target_row,
    comparands: r.comparands,
  }));
}

/** Format a millisecond span as a terse "1m 02s" / "850 ms" string. */
function formatDurationMs(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const secs = Math.round(ms / 1000);
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

function formatRunDuration(startedAt: string | null, endedAt: string | null): string {
  if (!startedAt || !endedAt) return "—";
  const elapsed = new Date(endedAt).getTime() - new Date(startedAt).getTime();
  if (!Number.isFinite(elapsed) || elapsed < 0) return "—";
  return formatDurationMs(elapsed);
}

/** Per-agent duration for the Activity tab.
 *
 * The stored `started_at`/`ended_at` on a face or notes agent are batch
 * stamps — every row is pre-created before extraction and finalized after
 * it, so a naive `ended - started` collapses to the WHOLE-RUN wall clock and
 * every agent shows the same figure (run-168 QA finding: eight agents all
 * reading "5m 58s"). The honest per-agent number is the sum of its recorded
 * per-turn compute times, which the detail payload already carries. We prefer
 * that; for rows with no turn telemetry (the Sheet-12 fan-out parent, older
 * runs) we fall back to the timestamp window — which is already a real window
 * for the agents that are stamped individually (scout, AI review). */
function formatAgentDuration(agent: RunAgentJson): string {
  const turnMs = (agent.turns ?? []).reduce(
    (sum, t) => sum + (t.duration_ms ?? 0),
    0,
  );
  if (turnMs > 0) return formatDurationMs(turnMs);
  const { started_at: started, ended_at: ended } = agent;
  if (!started || !ended) return "—";
  const ms = new Date(ended).getTime() - new Date(started).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  return formatDurationMs(ms);
}

// Phase 9: one card per agent. Header shows statement, status, model,
// total tokens; body is an AgentTimeline fed by the persisted events so
// past runs replay the same ToolCallCard rows as a live run.
// Activity-tab ordering (UX-QA #14): scout first (-1), then face statements in
// reading order (0-5), then everything else (notes, AI review) at 99 — a stable
// sort keeps that tail in its original arrival order.
function agentActivityOrder(agent: RunAgentJson): number {
  const t = agent.statement_type;
  if (t === "SCOUT") return -1;
  return statementCodeOrder(t);
}

function agentDisplayName(agent: RunAgentJson): string {
  const pseudoLabel = pseudoAgentLabel(agent.statement_type);
  if (pseudoLabel) return pseudoLabel;
  if (agent.statement_type.startsWith("NOTES_")) return notesTabLabel(agent.statement_type);
  return agent.statement_type;
}

function agentSemanticUpdates(agent: RunAgentJson): string[] {
  const updates: string[] = [];
  const unfinished = agent.status === "failed" || agent.status === "cancelled" || agent.status === "aborted";
  const operatorMessage = (message: string) => unfinished && /^[a-z][a-z0-9_]+$/.test(message)
    ? errorGuidance(message).label
    : message;
  for (let index = agent.events.length - 1; index >= 0 && updates.length < 3; index -= 1) {
    const event = agent.events[index];
    if (event.event === "status" && event.data.message) {
      if (unfinished && /:\s*complete\.?$/i.test(event.data.message)) continue;
      updates.push(operatorMessage(event.data.message));
    } else if (event.event === "error" && event.data.message) {
      updates.push(operatorMessage(event.data.message));
    } else if (event.event === "complete" && event.data.success && !unfinished) {
      updates.push("Finished its assigned work");
    } else if (event.event === "complete" && !event.data.success && event.data.error) {
      updates.push(operatorMessage(event.data.error));
    }
  }
  if (agent.status === "failed") {
    const reason = updates.find((update) => /\b(failed|incomplete|unverified|without writing|could not|unable)\b/i.test(update));
    if (reason) return [reason, ...updates.filter((update) => update !== reason && update !== "Note references need verification")];
    return updates[0] === "Note references need verification" ? updates : ["Workstream failed", ...updates];
  }
  return updates;
}

function agentSourceReference(agent: RunAgentJson): string | null {
  let first = Number.POSITIVE_INFINITY;
  let last = Number.NEGATIVE_INFINITY;
  for (const event of agent.events) {
    const pages = parseEvidencePages(JSON.stringify(event.data));
    for (const page of pages) {
      if (page < first) first = page;
      if (page > last) last = page;
    }
  }
  if (!Number.isFinite(first) || !Number.isFinite(last)) return null;
  return first === last ? `Source page ${first}` : `Source pages ${first}–${last}`;
}

interface AgentSummary {
  updates: string[];
  sourceReference: string | null;
}

function AgentCard({ agent, summary, filingStandard }: { agent: RunAgentJson; summary: AgentSummary; filingStandard?: unknown }) {
  // Sheet-12 sub-tab selection — mirrors the live ExtractPage path so
  // replay looks identical to live once the operator picks a sub. null =
  // "All" (every sub-agent merged, same as pre-sub-tab behaviour).
  const [notes12SubId, setNotes12SubId] = useState<string | null>(null);
  const [technicalOpen, setTechnicalOpen] = useState(false);
  // Notes agents are persisted with statement_type = "NOTES_<TEMPLATE>"
  // — render with the same friendly chip the live UI uses so history
  // doesn't fall back to the raw DB enum value (peer-review MEDIUM).
  // Pseudo-agents (CORRECTION / NOTES_VALIDATOR / VALIDATOR) resolve
  // through the central vocabulary so this row wears the same name as
  // the tab describing the same work ("AI review" / "Notes review") —
  // three drifting local maps were the run-168 QA finding.
  const displayName = agentDisplayName(agent);
  const { updates, sourceReference } = summary;

  // Notes-12 branch: derive the sub-agent list from the persisted events
  // (live path gets this for free from the reducer). Only render the
  // sub-tab bar when at least one sub-agent was recorded. Memoised so
  // the O(N) walk doesn't re-run on unrelated parent rerenders (e.g.
  // paging through the history list with this card still mounted).
  const isNotes12 = isNotes12StatementType(agent.statement_type);
  const subAgents = useMemo(
    () => (isNotes12 ? deriveSubAgentRangesFromEvents(agent.events) : []),
    [isNotes12, agent.events],
  );
  const showSubTabs = subAgents.length > 0;

  // Filter + rebuild when a specific sub is selected, otherwise use the
  // full event list. Memoised on the same keys as the filter so switching
  // subs is the only trigger that rebuilds the timeline.
  const { events, toolTimeline, reasoningBlocks } = useMemo(() => {
    if (!technicalOpen) return { events: [], toolTimeline: [], reasoningBlocks: [] };
    if (showSubTabs && notes12SubId !== null) {
      const filtered = filterEventsBySubAgent(agent.events, notes12SubId);
      return {
        events: filtered,
        toolTimeline: buildToolTimeline(filtered),
        reasoningBlocks: buildReasoningTimeline(filtered),
      };
    }
    return {
      events: agent.events,
      toolTimeline: buildToolTimeline(agent.events),
      reasoningBlocks: buildReasoningTimeline(agent.events),
    };
  }, [agent.events, notes12SubId, showSubTabs, technicalOpen]);

  return (
    <article data-testid="run-detail-agent" className="pwc-view-enter" style={styles.agentDetail}>
      <div style={styles.agentHeaderButton}>
        <div style={styles.agentTitleRow}>
          <span style={styles.agentStatement}>{displayName}</span>
          {/* Plain-English gloss for face-statement codes (UX-QA #12/legend) —
              "SOFP" alone assumes the reader speaks MBRS shorthand. */}
          {statementCodeSubtitle(agent.statement_type, filingStandard) && (
            <span style={styles.agentSubtitle}>
              {statementCodeSubtitle(agent.statement_type, filingStandard)}
            </span>
          )}
          {agent.variant && (
            <span style={styles.agentVariant}>({agent.variant})</span>
          )}
          {statusBadge(agentStatusDisplay(agent.status))}
          {agent.error_type && (
            // v17 (item 9): machine-readable failure class — lets an
            // operator see WHY a row failed without opening the trace.
            <span
              data-testid="agent-error-type"
              style={styles.agentErrorType}
              title={errorGuidance(agent.error_type).action}
            >
              {errorGuidance(agent.error_type).label}
            </span>
          )}
        </div>
        <div style={styles.agentMetaRow}>
          <span>{formatAgentDuration(agent)}</span>
        </div>
      </div>
      <div style={styles.agentSummary}>
        <strong>{agent.status === "cancelled" || agent.status === "aborted" ? "Workstream stopped" : updates[0] ?? agentStatusDisplay(agent.status).label}</strong>
        <span>{sourceReference ?? "No source page was recorded for this activity."}</span>
      </div>
      {agent.error_message && (
        <div data-testid="agent-error-message" style={styles.agentErrorMessage}>
          <strong>Terminal detail</strong>
          <span>{agent.error_message}</span>
        </div>
      )}
      {updates.length > 1 && (
        <div style={styles.agentRecentUpdates}>
          <h4 style={styles.agentDetailLabel}>Recent updates</h4>
          {updates.slice(1).map((update, index) => (
            <div key={`${update}:${index}`} style={styles.agentRecentUpdate}>
              <span aria-hidden="true" style={styles.agentUpdateDot} />
              <span>{update}</span>
            </div>
          ))}
        </div>
      )}
      <details
        style={styles.agentTechnicalDetails}
        open={technicalOpen}
      >
        <summary
          style={styles.perfSummary}
          onClick={(event) => {
            event.preventDefault();
            setTechnicalOpen((open) => !open);
          }}
        >
          Technical activity
        </summary>
        {technicalOpen ? <div style={styles.agentBody}>
          <div style={styles.agentMetaRow}>
            <span>{displayModelId(agent.model)}</span>
            {agent.token_breakdown && (
              <span>{agent.token_breakdown.turn_count} turns · {agent.token_breakdown.tool_call_count} tool calls</span>
            )}
            <span style={styles.agentTokens}>
              {agent.total_tokens != null ? `${agent.total_tokens.toLocaleString()} tokens` : "— tokens"}
              {agent.total_cost != null ? ` · ${formatCost(agent.total_cost)}` : ""}
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
            reasoningBlocks={reasoningBlocks}
            isRunning={false}
          />
        </div> : null}
      </details>
    </article>
  );
}

function HistoricalAgentWorkspace({ agents, filingStandard }: { agents: RunAgentJson[]; filingStandard?: unknown }) {
  const orderedAgents = useMemo(
    () => [...agents].sort((a, b) => agentActivityOrder(a) - agentActivityOrder(b)),
    [agents],
  );
  const [filter, setFilter] = useState<"all" | "current" | "finished">("all");
  const [selectedId, setSelectedId] = useState<number | null>(orderedAgents[0]?.id ?? null);
  const visibleAgents = orderedAgents.filter((agent) => {
    if (filter === "all") return true;
    const finished = [
      "succeeded",
      "complete",
      "completed_with_errors",
      "failed",
      "cancelled",
      "skipped",
    ].includes(agent.status);
    return filter === "finished" ? finished : !finished;
  });
  const visibleKey = visibleAgents.map((agent) => agent.id).join(":");
  const selectedAgent = visibleAgents.find((agent) => agent.id === selectedId) ?? visibleAgents[0] ?? null;
  const summaries = useMemo(() => {
    const next = new Map<number, AgentSummary>();
    for (const agent of orderedAgents) {
      next.set(agent.id, {
        updates: agentSemanticUpdates(agent),
        sourceReference: agentSourceReference(agent),
      });
    }
    return next;
  }, [orderedAgents]);

  useEffect(() => {
    if (selectedAgent && selectedAgent.id !== selectedId) setSelectedId(selectedAgent.id);
  }, [selectedAgent, selectedId, visibleKey]);

  return (
    <div className="historical-agent-workspace" style={styles.historicalAgentWorkspace}>
      <section style={styles.historicalAgentRoster} aria-label="Recorded agents">
        <div style={styles.historicalAgentRosterHeader}>
          <div>
            <h3 style={styles.sectionHeading}>Agents</h3>
          </div>
          <div role="group" aria-label="Filter recorded agents" style={styles.agentFilters}>
            {(["current", "all", "finished"] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
                style={{ ...styles.agentFilterButton, ...(filter === value ? styles.agentFilterButtonActive : {}) }}
              >
                {value.charAt(0).toUpperCase() + value.slice(1)}
              </button>
            ))}
          </div>
        </div>
        <div style={styles.historicalAgentList} data-testid="run-detail-agent-list">
          {visibleAgents.map((agent) => {
            const selected = agent.id === selectedAgent?.id;
            const summary = summaries.get(agent.id);
            const status = agentStatusDisplay(agent.status);
            const update = summary?.updates[0] ?? status.label;
            const hasDistinctUpdate = update !== status.label && update !== "Finished its assigned work";
            const subtitle = statementCodeSubtitle(agent.statement_type, filingStandard)
              ?? (agent.statement_type === "SCOUT" ? "Document preparation" : null);
            return (
              <button
                key={agent.id}
                type="button"
                data-testid="run-detail-agent-row"
                aria-pressed={selected}
                onClick={() => setSelectedId(agent.id)}
                className="historical-agent-row"
                style={{ ...styles.historicalAgentRow, ...(selected ? styles.historicalAgentRowActive : {}) }}
              >
                <StatusIcon symbol={status.symbol} />
                <span style={styles.historicalAgentIdentity}>
                  <strong>{agentDisplayName(agent)}</strong>
                  {subtitle && <span>{subtitle}</span>}
                </span>
                <span style={styles.historicalAgentTask}>
                  <strong>{hasDistinctUpdate ? update : summary?.sourceReference ?? formatAgentDuration(agent)}</strong>
                  {hasDistinctUpdate && <span>{summary?.sourceReference ?? formatAgentDuration(agent)}</span>}
                </span>
                <span style={styles.historicalAgentState}>{status.label}</span>
              </button>
            );
          })}
          {visibleAgents.length === 0 && <p style={styles.dim}>No agents match this filter.</p>}
        </div>
      </section>
      <aside style={styles.historicalAgentDetailPane}>
        <span role="status" aria-live="polite" aria-atomic="true" style={styles.visuallyHidden}>
          {selectedAgent ? `Selected ${agentDisplayName(selectedAgent)} activity.` : "No agent selected."}
        </span>
        {selectedAgent && (
          <AgentCard
            key={selectedAgent.id}
            agent={selectedAgent}
            filingStandard={filingStandard}
            summary={summaries.get(selectedAgent.id) ?? { updates: [], sourceReference: null }}
          />
        )}
      </aside>
    </div>
  );
}

// Tab identity for the run-detail surface. Review + Values are gated on
// canonical mode (the reviewer diff + concept tree only exist there).
export function RunDetailView({
  detail, onDelete, onResumeDraft, onForceAbort, onRestart,
  canonicalEnabled = false, initialTab = "overview",
}: RunDetailViewProps) {
  // Which tab is showing. Lazy content (Notes editor, Concepts workspace,
  // PDF panes) only mounts when its tab is active, so opening a run doesn't
  // spin up a dozen TipTap editors or fetch concept trees up front.
  // Priority: a `?tab=` deep link wins, then the `initialTab` prop (the
  // /concepts/{id} alias opens straight on Values), then Overview.
  const [tab, setTab] = useState<RunTabKey>(
    () => readRunTabFromUrl() ?? initialTab,
  );
  // Switching tabs mirrors the choice into `?tab=` so reload / share / back
  // land on the same tab (R3). Kept separate from the App-level pathname sync.
  const [notesPreparationBlocked, setNotesPreparationBlocked] = useState(false);
  const selectTab = useCallback((key: RunTabKey) => {
    if (notesPreparationBlocked) return;
    setTab(key);
    writeRunTabToUrl(key);
  }, [notesPreparationBlocked]);
  // Back/forward across tabs: re-read the query so the visible tab follows.
  useEffect(() => {
    const restoreVisibleTab = () => {
      const url = new URL(window.location.href);
      url.searchParams.set("tab", tab);
      window.history.replaceState(window.history.state, "", url);
    };
    const onPop = () => {
      if (notesPreparationBlocked) {
        restoreVisibleTab();
        return;
      }
      const fromUrl = readRunTabFromUrl();
      setTab(fromUrl ?? initialTab);
    };
    const onTabChange = (event: Event) => {
      const key = (event as CustomEvent<RunTabKey>).detail;
      if (notesPreparationBlocked && key !== tab) {
        event.stopImmediatePropagation();
        restoreVisibleTab();
        writeRunTabToUrl(tab);
      } else if (key) setTab(key);
    };
    window.addEventListener("popstate", onPop, true);
    window.addEventListener(RUN_TAB_CHANGE_EVENT, onTabChange, true);
    return () => {
      window.removeEventListener("popstate", onPop, true);
      window.removeEventListener(RUN_TAB_CHANGE_EVENT, onTabChange, true);
    };
  }, [initialTab, notesPreparationBlocked, tab]);
  // mTool fill modal (button, NOT a tab — gotcha #7).
  const [mtoolOpen, setMtoolOpen] = useState(false);
  // Human-filled mTool file attached for comparison (one per run).
  const [humanFile, setHumanFile] = useState<HumanFileRecord | null>(null);
  const [humanDialogOpen, setHumanDialogOpen] = useState(false);
  const canCompareHuman = detail.status === "completed" || detail.status === "completed_with_errors";
  useEffect(() => {
    setHumanFile(null);
    if (!canCompareHuman) return;
    let cancelled = false;
    getHumanFile(detail.id)
      .then((payload) => { if (!cancelled) setHumanFile(payload.file); })
      .catch(() => { if (!cancelled) setHumanFile(null); });
    return () => { cancelled = true; };
  }, [detail.id, canCompareHuman]);
  // Delete confirmation — the shared ConfirmDialog replaces window.confirm so
  // every destructive action in the app confirms the same, plain-English way.
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Abort confirmation for a wedged `running` run (UX-QA #2).
  const [confirmAbort, setConfirmAbort] = useState(false);
  // A flagged workbook remains available to expert users for investigation,
  // but the action is explicitly a draft download and confirms the filing
  // risk at action time. This is not persistent review sign-off.
  const [confirmDraftDownload, setConfirmDraftDownload] = useState(false);
  const [restartPending, setRestartPending] = useState(false);
  const [notesAuditOpen, setNotesAuditOpen] = useState(false);
  const [crossChecks, setCrossChecks] = useState<RunDetailJson["cross_checks"]>(
    detail.cross_checks ?? [],
  );
  const [recheck, setRecheck] = useState({ running: false, summary: "" });
  const recheckAbortRef = useRef<AbortController | null>(null);

  // Step 8/12 — clicking a failed cross-check drives the source-PDF pane to
  // the cited page(s) of the cell it targets. We resolve (target_sheet,
  // target_row) → the concept's evidence string → page numbers via a
  // per-run concept map fetched from /concepts. We store the *selected
  // target* (not the resolved pages) so the pane recomputes once the map
  // arrives — a fast click before the fetch lands isn't lost.
  const [selectedTarget, setSelectedTarget] = useState<{ sheet: string; row: number } | null>(
    null
  );
  const [evidenceByCell, setEvidenceByCell] = useState<Map<string, string | null>>(
    new Map()
  );
  // Keyed on detail.id so switching runs (RunDetailView is NOT remounted per
  // run — no key in HistoryPage) refetches and clears stale state, instead of
  // resolving run B's targets against run A's concept map.
  useEffect(() => {
    setNotesAuditOpen(false);
    setRecheck({ running: false, summary: "" });
    recheckAbortRef.current?.abort();
  }, [detail.id]);

  // History can refresh a still-running record without changing its id. Keep
  // the local recheck state in sync with those authoritative snapshots so a
  // run that finishes while this page is open does not leave the first,
  // usually-empty cross-check list frozen on screen.
  useEffect(() => {
    setCrossChecks(detail.cross_checks ?? []);
  }, [detail.id, detail.cross_checks]);

  useEffect(() => () => recheckAbortRef.current?.abort(), []);

  useEffect(() => {
    let cancelled = false;
    setEvidenceByCell(new Map());
    setSelectedTarget(null);
    fetch(`/api/runs/${detail.id}/concepts`)
      .then((r) => (r.ok ? r.json() : { concepts: [] }))
      .then((data) => {
        if (cancelled) return;
        const map = new Map<string, string | null>();
        for (const c of (data.concepts || []) as ConceptRow[]) {
          map.set(`${c.render_sheet}:${c.render_row}`, c.evidence);
        }
        setEvidenceByCell(map);
      })
      .catch(() => {
        if (!cancelled) setEvidenceByCell(new Map());
      });
    return () => {
      cancelled = true;
    };
  }, [detail.id]);

  // Derived: the pages for the currently selected target. Recomputes when the
  // concept map finishes loading, so an early click resolves correctly.
  const pdfPages = useMemo(() => {
    if (!selectedTarget) return [];
    const evidence = evidenceByCell.get(`${selectedTarget.sheet}:${selectedTarget.row}`) ?? null;
    return parseEvidencePages(evidence);
  }, [selectedTarget, evidenceByCell]);

  const handleSelectTarget = (sheet: string, row: number) => {
    setSelectedTarget({ sheet, row });
  };

  const isRunning = detail.status === "running";
  const isDraft = detail.status === "draft";
  const isFailed = detail.status === "failed";
  const isAborted = detail.status === "aborted";
  // A finished-but-flagged run (UX-QA #1): it completed AND offers a download,
  // but a consistency check failed — so the page must NOT look like a clean run.
  // `failed` is excluded (it has no workbook to download and already reads as a
  // failure); this targets the states that quietly pair an amber badge with a
  // big Download button.
  const isErrorOutcome =
    detail.status === "completed_with_errors" ||
    detail.status === "correction_exhausted";
  const isInvestigationOutcome = isErrorOutcome || isFailed || isAborted;
  const failingChecks = crossChecks
    .filter((c) => c.status === "failed" || c.status === "blocked");
  const failingCheckSummaries = failingChecks.map((c) => {
    const values = [
      c.expected != null ? `expected ${formatAccounting(c.expected)}` : null,
      c.actual != null ? `actual ${formatAccounting(c.actual)}` : null,
      c.diff != null ? `difference ${formatAccounting(c.diff)}` : null,
    ].filter(Boolean);
    return `${crossCheckFailureLabel(c.name)}${c.status === "blocked" ? " — waiting for required statement data" : values.length ? ` — ${values.join(", ")}` : ""}`;
  });
  const advisoryCheckSummaries = crossChecks
    .filter((check) => String(check.status) === "warning")
    .map((check) =>
      `${crossCheckFailureLabel(check.name)}${check.message ? ` — ${check.message}` : ""}`,
    );
  const issueTab: RunTabKey =
    failingChecks.length > 0 || advisoryCheckSummaries.length > 0
      ? "checks"
      : "agents";
  // Run-84 finding (2026-08-05): a single statement can stop early — the step
  // cap, a timeout, a cancel — while the RUN still reports `completed`. Its
  // partial figures reach the merged workbook anyway (extraction saves facts as
  // it goes, and the merge collects every per-statement file on disk), so none
  // of the run-level banners above fire and the download looks finished. Mirror
  // of `statement_types.SETTLED_AGENT_STATUSES`; keep the two in step.
  const settledAgentStatuses = ["succeeded", "completed_with_errors", "skipped"];
  const incompleteStatements = (isRunning || isDraft ? [] : detail.agents ?? []).filter(
    (a) =>
      (Object.keys(STATEMENT_LABELS) as string[]).includes(a.statement_type) &&
      !settledAgentStatuses.includes(a.status),
  );
  // Wait for extraction to finish or stop it first (facts must be final) — same gate the
  // backend enforces (api/mtool.py _FILLABLE_STATUSES).
  const canFillMtool =
    ["completed", "completed_with_errors", "failed", "aborted"].includes(detail.status);
  // Legacy detection: rows created before the v2 schema never captured a
  // run_config, merged_workbook_path, or per-agent token counts. Rather
  // than leaving several sections mysteriously empty, tag the run so the
  // user knows the gaps are expected and not a data-loss bug.
  const isLegacy = detail.config == null;
  // Peer-review [CRITICAL] guard: block deletion of runs that are still
  // executing. Deleting mid-run cascades through run_agents, agent_events,
  // and cross_checks while the coordinator is still writing new rows —
  // creating orphan children or FK-violation crashes. The backend also
  // returns 409 for this case, but disabling the button in the UI means
  // the bad click is impossible in the normal flow.
  const canDelete = detail.status !== "running";
  const handleDelete = () => {
    // Open the shared confirm dialog; the actual delete fires on confirm.
    setConfirmDelete(true);
  };
  const handleRestart = async () => {
    if (!onRestart || restartPending) return;
    setRestartPending(true);
    try {
      await onRestart(detail.id);
    } finally {
      setRestartPending(false);
    }
  };

  // One persistent navigation, ordered by the human review journey.
  const tabs: { key: RunTabKey; label: string }[] = [
    { key: "overview", label: "Overview" },
    ...(canonicalEnabled ? [{ key: "values" as RunTabKey, label: "Figures" }] : []),
    { key: "notes", label: "Notes" },
    { key: "checks", label: "Cross-checks" },
    { key: "agents", label: "Activity" },
    ...(canonicalEnabled ? [{ key: "review" as RunTabKey, label: "AI review" }] : []),
  ];
  const availableTabs = isDraft
    ? tabs.filter((item) => item.key === "overview")
    : tabs;

  // Clamp to a renderable tab. `initialTab="values"` (the /concepts/{id}
  // alias) can point at a tab that isn't available when canonical mode is off
  // or still loading — without this, no tab is active and no panel renders,
  // leaving a blank page below the tab bar (peer-review [6]).
  const activeTab: RunTabKey = availableTabs.some((t) => t.key === tab) ? tab : "overview";
  const reviewWorkspaceActive = activeTab === "values" || activeTab === "notes";
  const rollup = detail.telemetry_rollup;
  const sidecarNotice = detail.pdf_sidecar ? describePdfSidecar(detail.pdf_sidecar) : null;
  const sidecarIssue = detail.pdf_sidecar?.partial
    ? `Source transcript has failed pages: ${detail.pdf_sidecar.failed_pages?.join(", ") || "unknown"}`
    : sidecarNotice && !sidecarNotice.built ? sidecarNotice.title : null;
  const runDuration = formatRunDuration(detail.started_at, detail.ended_at);
  const liveElapsed = useLiveElapsed(isRunning ? detail.started_at : null);
  const nonBlockingItems = [
    ...advisoryCheckSummaries,
    ...(isErrorOutcome && failingCheckSummaries.length === 0
      ? ["Extraction or review finished with issues."]
      : []),
    ...(detail.incidents ?? [])
      .filter((incident) => incident.severity !== "fatal")
      .map((incident) => incident.user_message),
  ];
  const reviewItemCount = nonBlockingItems.length + (sidecarIssue ? 1 : 0);
  const reviewItemMessage = reviewItemCount === 1
    ? nonBlockingItems[0] ?? sidecarIssue
    : `${reviewItemCount} items need review`;
  const hasFigureStatements = !Array.isArray(detail.config?.statements) || detail.config.statements.length > 0;
  const preparationState = isDraft
    ? "Setup not complete"
    : isRunning
      ? "Not ready yet"
      : isFailed || isAborted
        ? detail.merged_workbook_path
          ? "Partial results available"
          : "Not ready"
        : incompleteStatements.length > 0
          ? "Partial results available"
          : failingChecks.length > 0
            ? "Checks need attention"
          : isErrorOutcome
            ? "Ready, with items to review"
            : "Ready to prepare";

  // Roving keyboard navigation for the tab bar (WAI-ARIA tabs pattern):
  // Arrow keys move between tabs, Home/End jump to ends, and focus follows
  // selection. Inline styles can't express this, so it lives here.
  const tabBarRef = useRef<HTMLDivElement>(null);
  const tabAnchorRef = useRef<HTMLDivElement>(null);
  const previousTabRef = useRef(activeTab);
  useLayoutEffect(() => {
    if (previousTabRef.current === activeTab) return;
    previousTabRef.current = activeTab;
    const anchor = tabAnchorRef.current;
    if (!anchor) return;
    const panelTop = Math.max(0,
      window.scrollY + anchor.getBoundingClientRect().top - Number(ui.appTopbar.height));
    if (window.scrollY > panelTop) window.scrollTo({ top: panelTop, behavior: "auto" });
  }, [activeTab]);
  const onTabKeyDown = (e: React.KeyboardEvent, index: number) => {
    let next = index;
    if (e.key === "ArrowRight") next = (index + 1) % availableTabs.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + availableTabs.length) % availableTabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = availableTabs.length - 1;
    else return;
    e.preventDefault();
    selectTab(availableTabs[next].key);
    const btns =
      tabBarRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    btns?.[next]?.focus();
  };

  const handleRecheck = useCallback(async () => {
    recheckAbortRef.current?.abort();
    const controller = new AbortController();
    recheckAbortRef.current = controller;
    setRecheck({ running: true, summary: "" });
    try {
      const response = await fetch(`/api/runs/${detail.id}/recheck`, {
        signal: controller.signal,
      });
      if (!response.ok) {
        setRecheck({ running: false, summary: "Checks could not be rerun." });
        return;
      }
      const payload = (await response.json()) as {
        results?: RunDetailJson["cross_checks"];
      };
      const results = payload.results ?? [];
      const passed = results.filter((row) => row.status === "passed").length;
      const failed = results.filter((row) => row.status === "failed").length;
      const blocked = results.filter((row) => row.status === "blocked").length;
      const warnings = results.filter((row) => String(row.status) === "warning").length;
      setCrossChecks(results);
      setRecheck({
        running: false,
        summary: `${passed} passed · ${failed} failed · ${blocked} blocked · ${warnings} warnings`,
      });
    } catch (error) {
      if ((error as { name?: string })?.name === "AbortError") return;
      setRecheck({ running: false, summary: "Checks could not be rerun." });
    }
  }, [detail.id]);

  return (
    <div style={styles.container}>
      <header style={reviewWorkspaceActive ? styles.reviewContextHeader : styles.header}>
        <div style={styles.headerText}>
          <h1 style={styles.filename}>
            {detail.pdf_filename}
          </h1>
          <div style={styles.metaRow}>
            {statusBadge(runStatusDisplay(detail.status))}
            {!reviewWorkspaceActive && isLegacy && (
              <span style={styles.legacyBadge}
                title="Some configuration and performance details were not recorded for this older run.">
                Limited historical details
              </span>
            )}
          </div>
        </div>
        <div style={styles.actions}>
          {isDraft && onResumeDraft ? (
            <button
              type="button"
              onClick={() => onResumeDraft(detail.id)}
              className={uiClass.btnPrimary}
              style={ui.buttonPrimary}
            >
              Resume setup
            </button>
          ) : null}
          {!isDraft && <button
            type="button"
            onClick={() => isInvestigationOutcome ? setConfirmDraftDownload(true) : setMtoolOpen(true)}
            disabled={!canFillMtool || notesPreparationBlocked}
            className={isInvestigationOutcome ? uiClass.btnSecondary : uiClass.btnPrimary}
            style={isInvestigationOutcome ? ui.buttonSecondary : ui.buttonPrimary}
            title={
              notesPreparationBlocked
                ? "Wait for notes to finish saving or formatting before preparing mTool"
                : canFillMtool
                ? isInvestigationOutcome
                  ? "Prepare an investigation draft using your mTool template"
                  : "Prepare a draft using your mTool template"
                : "Wait for extraction to finish or stop it first"
            }
          >
            {isFailed || isAborted ? "Prepare investigation draft" : "Prepare mTool draft"}
          </button>}
          {canCompareHuman && !humanFile && (
            <button
              type="button"
              data-testid="compare-human-file"
              onClick={() => setHumanDialogOpen(true)}
              className={uiClass.btnSecondary}
              style={ui.buttonSecondary}
              title="Compare this run with the mTool file a person filled for the same document"
            >
              Compare with human file
            </button>
          )}
          {notesPreparationBlocked && <span role="status" style={styles.dim}>Notes have unsaved changes or active formatting. Resolve any save errors in Notes before preparing.</span>}
          {/* The "Figures" tab is the single door to reviewing values — the
              old duplicate "Review values" button was removed (Phase 2). */}
          {/* Keep routine preparation in the header. Run management sits at
              the end of Overview, beside the run details it affects. */}
          {isRunning && onForceAbort && (
            <button
              type="button"
              onClick={() => setConfirmAbort(true)}
              className={uiClass.btnDanger}
              style={ui.buttonDanger}
              title="Stop this run — use this if a run has been stuck for a long time."
            >
              Stop run
            </button>
          )}
        </div>
      </header>

      {/* Run-84: fires on its own condition, NOT on run status — the case it
          exists for is a run that reports `completed` with one statement
          unfinished, which every banner around it misses. */}
      {!reviewWorkspaceActive && incompleteStatements.length > 0 && (
        <div style={styles.errorBanner} role="alert">
          <div style={styles.errorBannerBody}>
            <strong style={styles.errorBannerTitle}>
              {incompleteStatements.length === 1
                ? "One statement did not finish extracting."
                : `${incompleteStatements.length} statements did not finish extracting.`}
            </strong>
            <span style={styles.errorBannerText}>
              {incompleteStatements
                .map((a) => STATEMENT_LABELS[a.statement_type as keyof typeof STATEMENT_LABELS] ?? a.statement_type)
                .join(", ")}
              . The figures each one got as far as writing are still in this run
              and will be identified as incomplete in the template preparation report.
              Re-run the statement before relying on it. This run cannot
              be filed until it is resolved.
            </span>
          </div>
          <button
            type="button"
            onClick={() => selectTab("agents")}
            className={uiClass.btnSecondary}
            style={ui.buttonSecondary}
          >
            View activity
          </button>
        </div>
      )}

      {!reviewWorkspaceActive && !isRunning && failingChecks.length > 0 && (
        <div style={styles.errorBanner} role="alert" data-testid="failed-check-warning">
          <div style={styles.errorBannerBody}>
            <strong style={styles.errorBannerTitle}>
              This run finished, but a consistency check didn’t pass.
            </strong>
            <span style={styles.errorBannerText}>
              {failingCheckSummaries.join("; ")}. Resolve the failed check before filing.
            </span>
          </div>
          <button
            type="button"
            onClick={() => selectTab("checks")}
            className={uiClass.btnSecondary}
            style={ui.buttonSecondary}
          >
            Review checks
          </button>
        </div>
      )}

      {!reviewWorkspaceActive && (isFailed || isAborted) && (
        <div style={styles.errorBanner} role="alert">
          <div style={styles.errorBannerBody}>
            <strong style={styles.errorBannerTitle}>
              {isFailed ? "This extraction did not finish." : "This extraction was stopped."}
            </strong>
            <span style={styles.errorBannerText}>
              Saved figures may be incomplete. Review Activity before using them to prepare
              an investigation draft with your mTool template.
            </span>
          </div>
          <button
            type="button"
            onClick={() => selectTab("agents")}
            className={uiClass.btnSecondary}
            style={ui.buttonSecondary}
          >
            View activity
          </button>
        </div>
      )}

      {reviewWorkspaceActive && incompleteStatements.length > 0 && (
        <div style={styles.reviewWarningStrip} role="alert" data-testid="review-incomplete-warning">
          <span>
            {incompleteStatements.length === 1
              ? "One statement did not finish extracting."
              : `${incompleteStatements.length} statements did not finish extracting.`}
            {" "}This run cannot be filed until the incomplete extraction is resolved.
          </span>
          <button
            type="button"
            onClick={() => selectTab("agents")}
            className={uiClass.btnGhost}
            style={{ ...ui.buttonGhost, ...ui.buttonSm }}
          >
            View activity
          </button>
        </div>
      )}

      {reviewWorkspaceActive && (isFailed || isAborted) && (
        <div style={styles.reviewWarningStrip} role="alert" data-testid="review-stopped-warning">
          <span>
            {isFailed ? "This extraction did not finish." : "This extraction was stopped."}
            {" "}Treat any preserved figures as an investigation draft.
          </span>
          <button
            type="button"
            onClick={() => selectTab("agents")}
            className={uiClass.btnGhost}
            style={{ ...ui.buttonGhost, ...ui.buttonSm }}
          >
            View activity
          </button>
        </div>
      )}


      <MtoolFillModal runId={detail.id} open={mtoolOpen} onClose={() => setMtoolOpen(false)} />
      <HumanFileDialog
        runId={detail.id}
        open={humanDialogOpen}
        defaultUnit={(detail.denomination ?? (detail.config?.denomination as Denomination | undefined) ?? "thousands")}
        existing={humanFile}
        onClose={() => setHumanDialogOpen(false)}
        onAttached={(record) => {
          setHumanFile(record);
          if (activeTab !== "values" && activeTab !== "notes") selectTab(canonicalEnabled ? "values" : "notes");
        }}
      />

      <ConfirmDialog
        isOpen={confirmDraftDownload}
        title={isFailed || isAborted ? "Prepare investigation draft?" : "Prepare mTool draft for review?"}
        message={
          <>
            {isFailed || isAborted ? (
              <>This run did not finish normally. Next, choose an mTool template to fill with the saved figures. The resulting draft is for investigation only; it is not ready to file.</>
            ) : (
              failingChecks.length > 0
                ? <>This run has <strong>{failingChecks.length} unresolved check{failingChecks.length === 1 ? "" : "s"}</strong>. Next, choose an mTool template to prepare a draft for review; it is not ready to file.</>
                : <>This run finished with items that need review. Next, choose an mTool template to prepare a draft for review; it is not ready to file.</>
            )}
          </>
        }
        confirmLabel="Choose mTool template"
        danger={false}
        onConfirm={() => {
          setConfirmDraftDownload(false);
          setMtoolOpen(true);
        }}
        onCancel={() => setConfirmDraftDownload(false)}
      />

      <ConfirmDialog
        isOpen={confirmAbort}
        title="Stop this run?"
        message={
          <>
            This marks <strong>{detail.pdf_filename}</strong> as stopped so you
            can delete or re-run it. Use this only if the run has clearly
            stopped — any work still in progress will be lost.
          </>
        }
        confirmLabel="Stop run"
        onConfirm={() => {
          setConfirmAbort(false);
          onForceAbort?.(detail.id);
        }}
        onCancel={() => setConfirmAbort(false)}
      />

      <ConfirmDialog
        isOpen={confirmDelete}
        title="Delete this run?"
        message={
          <>
            This removes <strong>{detail.pdf_filename}</strong> from your history.
            The extracted figures and any downloads for this run go with it. The
            original PDF and workbook files on disk are kept.
          </>
        }
        confirmLabel="Delete run"
        onConfirm={() => {
          setConfirmDelete(false);
          onDelete(detail.id);
        }}
        onCancel={() => setConfirmDelete(false)}
      />

      {/* Keep the same navigation visible on every run section. Overview is
          the primary landing surface; the remaining tabs are review tools. */}
        <div ref={tabAnchorRef} aria-hidden="true" data-testid="run-tab-anchor"
          style={{ height: 0, marginBottom: -pwc.space.lg }} />
        <div
          ref={tabBarRef}
          style={styles.tabBar}
          role="tablist"
          aria-label="Run detail sections"
        >
          {availableTabs.map((t, i) => {
            const active = t.key === activeTab;
            return (
              <span key={t.key} role="presentation" style={styles.tabGroup}>
                <button
                  type="button"
                  role="tab"
                  aria-selected={active}
                  tabIndex={active ? 0 : -1}
                  className="pwc-tab"
                  disabled={notesPreparationBlocked && !active}
                  onClick={() => selectTab(t.key)}
                  onKeyDown={(e) => onTabKeyDown(e, i)}
                  style={active ? styles.tabActive : styles.tab}
                >
                  {t.label}
                </button>
              </span>
            );
          })}
        </div>

      {/* One fade wrapper keyed on the active tab: switching tabs remounts it,
          replaying the shared fade-in so the new panel arrives instead of
          hard-swapping. The child <section> keeps role="tabpanel". */}
      <TabPanelFade tabKey={activeTab}>
      {activeTab === "overview" && (
        <section style={styles.section} role="tabpanel">
          {/* Lead with the operator's two immediate questions: can the filing
              workbook be prepared, and how long did extraction take? */}
          <div style={styles.metricStrip}>
            <MetricTile
              label="Workbook"
              value={preparationState}
              tone={
                !isRunning &&
                !isDraft &&
                !isInvestigationOutcome &&
                failingChecks.length === 0 &&
                incompleteStatements.length === 0
                  ? "success"
                  : failingChecks.length > 0
                    ? "warning"
                    : "neutral"
              }
            />
            <MetricTile
              label="Elapsed time"
              value={isRunning ? liveElapsed : runDuration}
              secondary
            />
          </div>
          {isRunning && (
            <LiveRunSummary agents={detail.agents ?? []} onViewActivity={() => selectTab("agents")} />
          )}
          {(detail.status === "completed" || detail.status === "completed_with_errors" || detail.status === "correction_exhausted") && canonicalEnabled &&
            hasFigureStatements && (
            <div style={styles.verificationPrompt}>
              <span>Verify extracted figures against the source PDF before filing.</span>
              <button type="button" onClick={() => selectTab("values")} className={uiClass.btnSecondary} style={{ ...ui.buttonSecondary, ...ui.buttonSm }}>
                Review figures
              </button>
            </div>
          )}
          {reviewItemCount > 0 && (
            <div style={styles.itemsToCheck} data-testid="items-to-check">
              <span style={styles.itemToCheck}>
                {reviewItemMessage}
              </span>
              <button
                type="button"
                onClick={() => selectTab(issueTab)}
                className={uiClass.btnSecondary}
                style={{ ...ui.buttonSecondary, ...ui.buttonSm }}
              >
                {issueTab === "checks" ? "View cross-checks" : "View activity"}
              </button>
            </div>
          )}
          <details>
            <summary style={styles.perfSummary}>Run configuration</summary>
            <ConfigBlock config={detail.config} />
          </details>
          {!isDraft && (
            <section aria-label="Run actions" style={styles.runActions}>
              <span style={ui.fieldLabel}>Run actions</span>
              <div style={styles.runActionButtons}>
                {onRestart && !isRunning && <button
                  type="button"
                  onClick={handleRestart}
                  disabled={restartPending}
                  className={uiClass.btnQuiet}
                  style={ui.buttonQuiet}
                  title="Create a new editable run with the same document and settings"
                >
                  {restartPending ? "Creating redo…" : "Redo run"}
                </button>}
                <button
                  type="button"
                  onClick={handleDelete}
                  disabled={!canDelete}
                  className={uiClass.btnDanger}
                  style={ui.buttonDanger}
                  title="Delete run from history (on-disk files are kept)"
                >
                  Delete run
                </button>
              </div>
            </section>
          )}
        </section>
      )}

      {activeTab === "agents" && (
        <section style={styles.section} role="tabpanel" data-testid="run-detail-agents">
          {detail.agents.length === 0 ? (
            <p style={styles.dim}>Nothing was recorded for this run yet.</p>
          ) : (
            <HistoricalAgentWorkspace agents={detail.agents} filingStandard={detail.filing_standard ?? detail.config?.filing_standard} />
          )}
          {/* Timing + AI-usage detail (the former Telemetry tab), tucked into a
              collapsed disclosure so the everyday view stays about what the AI
              did, not token/latency internals. */}
          <details style={styles.perfDetails} data-testid="run-detail-telemetry">
            <summary style={styles.perfSummary}>Performance details</summary>
            <div style={{ marginTop: pwc.space.md }}>
              {rollup && (
                <div style={styles.metricStrip}>
                  <MetricTile label="Total tokens" value={rollup.total_tokens.toLocaleString()} />
                  <MetricTile
                    label="Reasoning tokens"
                    value={(rollup.thinking_tokens ?? 0).toLocaleString()}
                  />
                  <MetricTile label="Est. cost" value={formatCost(rollup.total_cost)} />
                  <MetricTile
                    label="Usage coverage"
                    value={rollup.coverage === "complete"
                      ? "Complete"
                      : rollup.coverage === "partial"
                        ? `Partial (${rollup.calls_with_unavailable_usage ?? 0} missing)`
                        : "Unavailable"}
                  />
                  <MetricTile label="Model calls" value={String(rollup.call_count ?? 0)} />
                  <MetricTile label="Turns" value={String(rollup.turn_count)} />
                  <MetricTile label="Tool calls" value={String(rollup.tool_call_count)} />
                  <MetricTile label="Agents" value={String(detail.agents.length)} />
                </div>
              )}
              <AgentTelemetryPanel detail={detail} />
            </div>
          </details>
        </section>
      )}

      {activeTab === "notes" && (
        <section style={styles.sectionFull} role="tabpanel" data-testid="run-detail-notes-review">
          <ConceptsPage
            key={`${detail.id}:notes`}
            runId={detail.id}
            initialView="notes"
            initialCrossChecks={crossChecksForValidator(crossChecks)}
            onPreparationBlocked={setNotesPreparationBlocked}
            humanFile={humanFile}
            onReplaceHumanFile={() => setHumanDialogOpen(true)}
            onHumanFileRemoved={() => setHumanFile(null)}
          />
          <details
            style={styles.perfDetails}
            data-testid="run-detail-notes-audit"
            onToggle={(event) => setNotesAuditOpen(event.currentTarget.open)}
          >
            <summary style={styles.perfSummary}>Notes audit details</summary>
            {notesAuditOpen && (
              <div style={{ marginTop: pwc.space.md, display: "grid", gap: pwc.space.lg }}>
                <NotesIntegrityPanel runId={detail.id} />
                <NotesTablesPanel runId={detail.id} />
                <NotesReviewerPanel runId={detail.id} />
              </div>
            )}
          </details>
        </section>
      )}

      {activeTab === "checks" && (
        <section style={styles.section} role="tabpanel">
          <div style={styles.recheckBar}>
            <span data-testid="recheck-summary" role="status" aria-live="polite" style={styles.recheckSummary}>
              {recheck.summary}
            </span>
            <button
              type="button"
              data-testid="recheck-btn"
              onClick={() => void handleRecheck()}
              disabled={recheck.running}
              className={uiClass.btnSecondary}
              style={ui.buttonSecondary}
            >
              {recheck.running ? "Checking…" : "Rerun checks"}
            </button>
          </div>
          <div style={styles.crossCheckScroller}>
            <ValidatorTab
              crossChecks={crossChecksForValidator(crossChecks)}
              onSelectTarget={handleSelectTarget}
            />
          </div>
          {/* Source-PDF verification for the selected check's target cell.
              Rendered only once a target is selected. */}
          {selectedTarget && (
            <div style={{ marginTop: pwc.space.md }}>
              <PdfSourcePane runId={detail.id} pages={pdfPages} />
            </div>
          )}
        </section>
      )}

      {activeTab === "review" && canonicalEnabled && (
        <section style={styles.section} role="tabpanel" data-testid="run-detail-review">
          <ReviewTab runId={detail.id} onSelectTarget={handleSelectTarget} />
          {selectedTarget && (
            <div style={{ marginTop: pwc.space.md }}>
              <PdfSourcePane runId={detail.id} pages={pdfPages} />
            </div>
          )}
        </section>
      )}

      {activeTab === "values" && canonicalEnabled && (
        <section style={styles.sectionFull} role="tabpanel" data-testid="run-detail-values">
          {/* Notes render inline in the workspace (next to the Source PDF)
              rather than bouncing to the Notes tab — the review-workspace
              revamp (docs/PLAN-review-workspace.md Phase 1). The run's stored
              cross-checks seed the outcome strip's "Checks passing" (Phase 3). */}
          <ConceptsPage
            key={detail.id}
            runId={detail.id}
            initialView="figures"
            initialCrossChecks={crossChecksForValidator(crossChecks)}
            onPreparationBlocked={setNotesPreparationBlocked}
            humanFile={humanFile}
            onReplaceHumanFile={() => setHumanDialogOpen(true)}
            onHumanFileRemoved={() => setHumanFile(null)}
          />
        </section>
      )}
      </TabPanelFade>
    </div>
  );
}

/** A single labelled metric in the Overview strip. */
/** Ticking elapsed time for a working run; "—" without a start time. */
function useLiveElapsed(startedAt: string | null): string {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!startedAt) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [startedAt]);
  if (!startedAt) return "—";
  const elapsed = now - new Date(startedAt).getTime();
  return Number.isFinite(elapsed) && elapsed >= 0 ? formatDurationMs(elapsed) : "—";
}

const COMPLETE_AGENT_STATUSES = new Set(["succeeded", "completed"]);

/** Live progress for a working run (design system: one current-stage label,
 *  a plain-language count, secondary elapsed time; no silent waiting). The
 *  run detail refreshes while the run is working, so this stays current. */
function LiveRunSummary({ agents, onViewActivity }: { agents: RunAgentJson[]; onViewActivity: () => void }) {
  const complete = agents.filter((a) => COMPLETE_AGENT_STATUSES.has(a.status)).length;
  const working = agents
    .filter((a) => a.status === "running")
    .map((a) => STATEMENT_LABELS[a.statement_type as keyof typeof STATEMENT_LABELS] ?? agentDisplayName(a));
  const workingText = working.length === 0
    ? "Starting workstreams"
    : working.length <= 2
      ? `Working on ${working.join(" and ")}`
      : `Working on ${working.slice(0, 2).join(", ")} and ${working.length - 2} more`;
  return (
    <div style={styles.liveSummary} data-testid="live-run-summary">
      <div style={styles.liveSummaryText}>
        <span role="status" aria-live="polite" style={styles.liveSummaryStage}>
          <StatusIcon symbol={STATUS_SYMBOLS.inProgress} />
          {workingText}
        </span>
        {agents.length > 0 && (
          <span style={styles.liveSummaryCount}>
            {complete} of {agents.length} workstreams complete
          </span>
        )}
      </div>
      <button type="button" onClick={onViewActivity} className={uiClass.btnSecondary} style={{ ...ui.buttonSecondary, ...ui.buttonSm }}>
        View activity
      </button>
    </div>
  );
}

function MetricTile({
  label,
  value,
  tone = "neutral",
  secondary = false,
}: {
  label: string;
  value: string;
  tone?: "neutral" | "success" | "warning";
  secondary?: boolean;
}) {
  const accent =
    tone === "success" ? pwc.successText : tone === "warning" ? pwc.warningText : undefined;
  return (
    <div style={styles.metricTile}>
      <div style={{ ...styles.metricValue, ...(secondary ? styles.metricValueSecondary : {}), ...(accent ? { color: accent } : {}) }}>
        {value}
      </div>
      <div style={styles.metricLabel}>{label}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = {
  // No border/shadow here — the parent (RunDetailPage) provides the
  // top-level layout chrome. Keeping an outer card inside a page would
  // produce a nested-card look that doubles the visual noise.
  container: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
  } as React.CSSProperties,
  crossCheckScroller: {
    overflowX: "auto" as const,
    maxWidth: "100%",
  } as React.CSSProperties,
  recheckBar: {
    display: "flex",
    justifyContent: "flex-end",
    alignItems: "center",
    gap: pwc.space.md,
    minHeight: 40,
  } as React.CSSProperties,
  recheckSummary: {
    color: pwc.grey700,
    fontSize: 12,
  } as React.CSSProperties,
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-end",
    gap: pwc.space.lg,
    flexWrap: "wrap" as const,
    paddingBottom: pwc.space.lg,
    borderBottom: "none",
  } as React.CSSProperties,
  reviewContextHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: pwc.space.lg,
    flexWrap: "wrap" as const,
    minHeight: 44,
  } as React.CSSProperties,
  headerText: {
    minWidth: 0,
  } as React.CSSProperties,
  kicker: {
    fontFamily: pwc.fontHeading,
    fontSize: 12,
    fontWeight: pwc.weight.medium,
    color: pwc.orange500,
    marginBottom: pwc.space.xs,
  } as React.CSSProperties,
  filename: {
    ...ui.pageTitle,
    margin: 0,
  } as React.CSSProperties,
  reviewContextProfile: {
    display: "inline",
    marginLeft: pwc.space.sm,
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey700,
  } as React.CSSProperties,
  metaRow: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
  filingProfile: {
    ...ui.metadata,
    color: pwc.grey700,
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
  dim: {
    color: pwc.grey700,
    fontSize: 14,
    fontFamily: pwc.fontBody,
  } as React.CSSProperties,
  aiDisclaimer: {
    margin: `${pwc.space.sm}px 0 0`,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey700,
  } as React.CSSProperties,
  actions: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  // Finished-but-flagged summary. The tint and explicit copy carry meaning;
  // Direction A forbids accent edge rules.
  errorBanner: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    flexWrap: "wrap" as const,
    gap: pwc.space.md,
    padding: pwc.space.md,
    background: pwc.orange50,
    border: "none",
    borderRadius: pwc.radius.md,
  } as React.CSSProperties,
  errorBannerBody: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
    minWidth: 240,
    flex: "1 1 320px",
  } as React.CSSProperties,
  errorBannerTitle: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey800,
  } as React.CSSProperties,
  errorBannerText: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey700,
  } as React.CSSProperties,
  reviewWarningStrip: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
    padding: `${pwc.space.sm}px 0`,
    borderTop: `1px solid ${pwc.grey200}`,
    borderBottom: `1px solid ${pwc.grey200}`,
    color: pwc.grey900,
    fontFamily: pwc.fontBody,
    fontSize: 14,
  } as React.CSSProperties,
  runActions: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap" as const,
    gap: pwc.space.md,
    marginTop: pwc.space.xl,
    paddingTop: pwc.space.md,
    borderTop: `1px solid ${pwc.grey100}`,
  } as React.CSSProperties,
  runActionButtons: {
    display: "flex",
    gap: pwc.space.sm,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  // Data-dense surface tabs: compact selected fill, no accent edge line.
  tabBar: {
    position: "sticky",
    top: ui.appTopbar.height,
    scrollMarginTop: ui.appTopbar.height,
    zIndex: 15,
    background: tokens.surface.canvas,
    paddingBlock: pwc.space.xs,
    display: "flex",
    gap: pwc.space.xs,
    flexWrap: "nowrap" as const,
    overflowX: "auto" as const,
  } as React.CSSProperties,
  tabGroup: {
    display: "inline-flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  tabGroupLabel: {
    ...ui.microLabel,
    marginLeft: pwc.space.md,
  } as React.CSSProperties,
  tab: {
    ...ui.tab,
    fontSize: 14,
  } as React.CSSProperties,
  tabActive: {
    ...ui.tab,
    ...ui.tabActive,
    fontSize: 14,
  } as React.CSSProperties,
  // Full-bleed panel for the Values (Concepts) tab — its 3-column workspace
  // wants the whole width, unlike the prose-width Overview/Agents panels.
  sectionFull: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  metricStrip: {
    display: "flex",
    gap: pwc.space.md,
    flexWrap: "wrap" as const,
    marginBottom: pwc.space.md,
  } as React.CSSProperties,
  metricTile: {
    ...ui.statTile,
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  } as React.CSSProperties,
  metricValue: {
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
    fontVariantNumeric: "tabular-nums" as const,
  } as React.CSSProperties,
  metricValueSecondary: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    fontWeight: pwc.weight.regular,
    color: pwc.grey700,
  } as React.CSSProperties,
  metricLabel: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey700,
  } as React.CSSProperties,
  section: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  sectionHeading: {
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    fontWeight: pwc.weight.semibold,
    color: pwc.black,
    margin: 0,
  } as React.CSSProperties,
  // Button-flavoured variant of sectionHeading used for collapsible
  // sections. Strips all default button chrome so it visually matches an
  // <h4> while staying semantically a control with aria-expanded.
  collapsibleSectionHeading: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 680,
    color: pwc.grey700,
    margin: 0,
    background: "transparent",
    border: "none",
    padding: 0,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
    textAlign: "left" as const,
    width: "fit-content",
  } as React.CSSProperties,
  dl: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    margin: 0,
  } as React.CSSProperties,
  dlRow: {
    display: "flex",
    gap: pwc.space.sm,
    fontFamily: pwc.fontBody,
    fontSize: 14,
  } as React.CSSProperties,
  dt: {
    fontWeight: 680,
    color: pwc.grey700,
    minWidth: 140,
  } as React.CSSProperties,
  dd: {
    margin: 0,
    color: pwc.grey900,
  } as React.CSSProperties,
  historicalAgentWorkspace: {
    display: "grid",
    gridTemplateColumns: "minmax(520px, 1.35fr) minmax(300px, 0.72fr)",
    gap: pwc.space.xxl,
    alignItems: "start",
    minWidth: 0,
  } as React.CSSProperties,
  historicalAgentRoster: {
    minWidth: 0,
  } as React.CSSProperties,
  historicalAgentRosterHeader: {
    minHeight: 42,
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: pwc.space.md,
  } as React.CSSProperties,
  agentRosterHint: {
    margin: "3px 0 0",
    color: pwc.grey700,
    fontSize: 12,
  } as React.CSSProperties,
  agentFilters: {
    display: "flex",
    gap: 3,
  } as React.CSSProperties,
  agentFilterButton: {
    minHeight: 30,
    padding: "0 9px",
    border: 0,
    borderRadius: pwc.radius.sm,
    background: "transparent",
    color: pwc.grey700,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    cursor: "pointer",
  } as React.CSSProperties,
  agentFilterButtonActive: {
    background: pwc.grey50,
    color: pwc.black,
    fontWeight: pwc.weight.medium,
  } as React.CSSProperties,
  historicalAgentList: {
    display: "grid",
    gap: 2,
    marginTop: 7,
  } as React.CSSProperties,
  historicalAgentRow: {
    width: "100%",
    minHeight: 58,
    padding: "8px 9px",
    border: 0,
    borderRadius: pwc.radius.md,
    background: "transparent",
    display: "grid",
    gridTemplateColumns: "20px minmax(180px, 36%) minmax(0, 1fr) 100px",
    gap: 11,
    alignItems: "center",
    textAlign: "left",
    cursor: "pointer",
    color: pwc.black,
    fontFamily: pwc.fontBody,
    fontSize: 14,
  } as React.CSSProperties,
  historicalAgentRowActive: {
    background: pwc.grey50,
  } as React.CSSProperties,
  historicalAgentIdentity: {
    minWidth: 0,
    display: "grid",
    gap: 2,
  } as React.CSSProperties,
  historicalAgentTask: {
    minWidth: 0,
    display: "grid",
    gap: 2,
  } as React.CSSProperties,
  historicalAgentState: {
    color: pwc.grey700,
    fontSize: 12,
    textAlign: "right" as const,
    whiteSpace: "nowrap",
  } as React.CSSProperties,
  historicalAgentDetailPane: {
    position: "sticky",
    top: 86,
    minWidth: 0,
    paddingLeft: pwc.space.xxl,
    borderLeft: `1px solid ${pwc.grey100}`,
  } as React.CSSProperties,
  visuallyHidden: {
    position: "absolute",
    width: 1,
    height: 1,
    padding: 0,
    margin: -1,
    overflow: "hidden",
    clip: "rect(0, 0, 0, 0)",
    whiteSpace: "nowrap",
    border: 0,
  } as React.CSSProperties,
  perfDetails: {
    marginTop: pwc.space.lg,
    borderTop: `1px solid ${pwc.grey200}`,
    paddingTop: pwc.space.md,
  } as React.CSSProperties,
  itemsToCheck: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
    borderTop: `1px solid ${pwc.grey100}`,
    borderBottom: `1px solid ${pwc.grey100}`,
    padding: `${pwc.space.md}px 0`,
  } as React.CSSProperties,
  itemsToCheckBody: {
    display: "grid",
    gap: pwc.space.sm,
    marginTop: pwc.space.md,
  } as React.CSSProperties,
  itemToCheck: {
    margin: 0,
    color: pwc.grey700,
    fontFamily: pwc.fontBody,
    fontSize: 14,
  } as React.CSSProperties,
  perfSummary: {
    cursor: "pointer",
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.medium,
    color: pwc.grey700,
  } as React.CSSProperties,
  agentDetail: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
    background: pwc.white,
  } as React.CSSProperties,
  agentHeaderButton: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
    padding: 0,
    background: "transparent",
    border: "none",
    width: "100%",
    textAlign: "left" as const,
    fontFamily: "inherit",
    font: "inherit",
    color: "inherit",
  } as React.CSSProperties,
  agentBody: {
    marginTop: pwc.space.md,
  } as React.CSSProperties,
  agentSummary: {
    display: "grid",
    gap: 4,
    color: pwc.grey700,
    fontSize: 14,
    lineHeight: 1.55,
  } as React.CSSProperties,
  agentErrorMessage: {
    display: "grid",
    gap: 4,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    color: pwc.grey900,
    background: pwc.grey50,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    fontSize: 14,
    lineHeight: 1.55,
    whiteSpace: "pre-wrap" as const,
    overflowWrap: "anywhere" as const,
  } as React.CSSProperties,
  agentRecentUpdates: {
    display: "grid",
    gap: 9,
  } as React.CSSProperties,
  agentDetailLabel: {
    margin: 0,
    color: pwc.grey700,
    fontSize: 12,
    fontWeight: pwc.weight.semibold,
  } as React.CSSProperties,
  agentRecentUpdate: {
    display: "grid",
    gridTemplateColumns: "8px minmax(0, 1fr)",
    gap: 8,
    alignItems: "start",
    color: pwc.black,
    fontSize: 14,
  } as React.CSSProperties,
  agentUpdateDot: {
    width: 5,
    height: 5,
    marginTop: 6,
    borderRadius: "50%",
    background: pwc.grey400,
  } as React.CSSProperties,
  agentTechnicalDetails: {
    borderTop: `1px solid ${pwc.grey100}`,
    paddingTop: pwc.space.md,
  } as React.CSSProperties,
  agentTitleRow: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  agentStatement: {
    fontFamily: pwc.fontHeading,
    fontWeight: 680,
    fontSize: 14,
    color: pwc.grey900,
  } as React.CSSProperties,
  agentVariant: {
    color: pwc.grey500,
    fontSize: 14,
  } as React.CSSProperties,
  // Plain-English gloss next to the statement code (UX-QA #12).
  agentSubtitle: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey700,
  } as React.CSSProperties,
  // v17 (item 9): muted mono chip naming the failure class on failed rows.
  agentErrorType: {
    color: pwc.grey500,
    fontFamily: pwc.fontMono,
    fontSize: 12,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.sm,
    padding: "1px 6px",
  } as React.CSSProperties,
  // Proportional, not monospace (UX-QA #12): the model · turns · duration meta
  // read as a debug log in mono. Numbers here are incidental, not a table to
  // align, so the body font is friendlier for the accountant/PM audience.
  agentMetaRow: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey700,
  } as React.CSSProperties,
  liveSummary: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
    flexWrap: "wrap" as const,
    marginTop: pwc.space.lg,
    padding: `${pwc.space.md}px 0`,
    borderTop: `1px solid ${tokens.color.border.subtle}`,
    borderBottom: `1px solid ${tokens.color.border.subtle}`,
  } as React.CSSProperties,
  liveSummaryText: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    minWidth: 0,
  } as React.CSSProperties,
  liveSummaryStage: {
    display: "inline-flex",
    alignItems: "center",
    gap: pwc.space.sm,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    fontWeight: pwc.weight.medium,
    color: tokens.color.text.primary,
  } as React.CSSProperties,
  liveSummaryCount: {
    ...ui.metadata,
    paddingLeft: 24,
    fontVariantNumeric: "tabular-nums",
  } as React.CSSProperties,
  verificationPrompt: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: pwc.space.md,
    flexWrap: "wrap" as const,
    marginTop: pwc.space.lg,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey700,
  } as React.CSSProperties,
  agentTokens: {
    marginLeft: "auto",
  } as React.CSSProperties,
  legacyBadge: {
    ...ui.metadata,
    fontSize: 12,
  } as React.CSSProperties,
} as const;
