import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { PdfSourcePane } from "./PdfSourcePane";
import { parseEvidencePages } from "../lib/evidencePages";
import { ConceptsPage } from "../pages/ConceptsPage";
import type { ConceptRow } from "../pages/ConceptsPage";
import { EvalTab } from "./EvalTab";
import { runStatusDisplay, agentStatusDisplay } from "../lib/runStatus";
import type { RunStatusDisplay } from "../lib/runStatus";
import type { RunDetailJson, RunAgentJson, CrossCheckResult } from "../lib/types";
import { AgentTelemetryPanel } from "./AgentTelemetryPanel";
import { ValidatorTab } from "./ValidatorTab";
import { ReviewTab } from "./ReviewTab";
import { MtoolFillModal } from "./MtoolFillModal";
import { ConfirmDialog } from "./ConfirmDialog";
import { AgentTimeline } from "./AgentTimeline";
import { NotesSubTabBar } from "./NotesSubTabBar";
import { TabPanelFade } from "./TabPanelFade";
import { NotesReviewTab } from "./NotesReviewTab";
import { NotesReviewerPanel } from "./NotesReviewerPanel";
import { NotesCoveragePanel } from "./NotesCoveragePanel";
import { ConsistencyPanel } from "./ConsistencyPanel";
import {
  buildToolTimeline,
  filterEventsBySubAgent,
  deriveSubAgentRangesFromEvents,
} from "../lib/buildToolTimeline";
import { displayModelId } from "../lib/modelId";
import { notesTabLabel } from "../lib/appReducer";
import { formatCost } from "../lib/numberFormat";
import { denominationLabel, pseudoAgentLabel, variantLabel, crossCheckLabel } from "../lib/vocabulary";
import { isNotes12StatementType } from "../lib/notes";
import { statementCodeSubtitle, statementCodeOrder } from "../lib/sheetLabels";

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
  onDownload: (runId: number) => void;
  onDelete: (runId: number) => void;
  /** Rescue a run wedged in `running` status (UX-QA #2). When provided and the
   *  run is `running`, an "Abort run" control replaces the disabled Delete so a
   *  dead run isn't a dead-end. Optional — absent for callers that can't act. */
  onForceAbort?: (runId: number) => void;
  /** Called when the user confirms "Regenerate notes" in the Notes
   *  Review section. The parent wires this to the existing rerun
   *  endpoint. Optional — legacy callers without Step 12 UX still
   *  render the detail view unchanged. */
  onRegenerateNotes?: (runId: number) => void;
  /** Gate the review link on canonical mode so legacy runs (which
   *  have no concept tree) don't link to an empty page — matches the TopNav
   *  / Results gating (peer-review F6). Defaults to false: hidden unless the
   *  parent explicitly enables it. */
  canonicalEnabled?: boolean;
  /** Which tab to open on first render. Used by the `/concepts/{id}` alias
   *  to land directly on the Values tab. Defaults to "overview". */
  initialTab?: RunTabKey;
}

/** Render a status badge from a precomputed display. Caller picks
 *  runStatusDisplay vs agentStatusDisplay so the right vocabulary is used
 *  in each context (run-level vs per-agent enums differ slightly). */
function statusBadge(display: RunStatusDisplay) {
  return (
    <span style={{ ...ui.badge, borderColor: display.accent }}>
      <span aria-hidden="true" style={ui.badgeDot(display.accent)} />
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
  const models = (config.models ?? {}) as Record<string, string>;
  if (Object.keys(models).length > 0) {
    entries.push({
      label: "Model overrides",
      value: Object.entries(models)
        .map(([k, v]) => `${k}: ${displayModelId(v)}`)
        .join(", "),
    });
  }
  entries.push({
    label: "Scout",
    value: config.use_scout ? "Enabled" : "Disabled",
  });
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

function AgentCard({ agent }: { agent: RunAgentJson }) {
  // Sheet-12 sub-tab selection — mirrors the live ExtractPage path so
  // replay looks identical to live once the operator picks a sub. null =
  // "All" (every sub-agent merged, same as pre-sub-tab behaviour).
  const [notes12SubId, setNotes12SubId] = useState<string | null>(null);
  // Collapsed by default: the detail page used to render every agent's
  // full tool timeline inline, producing a tall, hard-to-scan view when a
  // run had 5+ agents. Operators said they only occasionally need the
  // raw event stream, so we hide it until the header is clicked.
  const [expanded, setExpanded] = useState(false);

  // Notes agents are persisted with statement_type = "NOTES_<TEMPLATE>"
  // — render with the same friendly chip the live UI uses so history
  // doesn't fall back to the raw DB enum value (peer-review MEDIUM).
  // Pseudo-agents (CORRECTION / NOTES_VALIDATOR / VALIDATOR) resolve
  // through the central vocabulary so this row wears the same name as
  // the tab describing the same work ("AI review" / "Notes review") —
  // three drifting local maps were the run-168 QA finding.
  let displayName: string;
  const pseudoLabel = pseudoAgentLabel(agent.statement_type);
  if (pseudoLabel) {
    displayName = pseudoLabel;
  } else if (agent.statement_type.startsWith("NOTES_")) {
    displayName = notesTabLabel(agent.statement_type);
  } else {
    displayName = agent.statement_type;
  }

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
  const { events, toolTimeline } = useMemo(() => {
    if (showSubTabs && notes12SubId !== null) {
      const filtered = filterEventsBySubAgent(agent.events, notes12SubId);
      return { events: filtered, toolTimeline: buildToolTimeline(filtered) };
    }
    return { events: agent.events, toolTimeline: buildToolTimeline(agent.events) };
  }, [agent.events, notes12SubId, showSubTabs]);

  return (
    <article data-testid="run-detail-agent" style={styles.agentCard}>
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        style={styles.agentHeaderButton}
      >
        <div style={styles.agentTitleRow}>
          <span style={styles.agentChevron} aria-hidden="true">
            {expanded ? "▾" : "▸"}
          </span>
          <span style={styles.agentStatement}>{displayName}</span>
          {/* Plain-English gloss for face-statement codes (UX-QA #12/legend) —
              "SOFP" alone assumes the reader speaks MBRS shorthand. */}
          {statementCodeSubtitle(agent.statement_type) && (
            <span style={styles.agentSubtitle}>
              {statementCodeSubtitle(agent.statement_type)}
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
            >
              {agent.error_type.replace(/_/g, " ")}
            </span>
          )}
        </div>
        <div style={styles.agentMetaRow}>
          <span>{displayModelId(agent.model)}</span>
          {agent.token_breakdown && (
            <span>
              {agent.token_breakdown.turn_count} turns ·{" "}
              {agent.token_breakdown.tool_call_count} tool calls
            </span>
          )}
          <span>{formatAgentDuration(agent)}</span>
          <span style={styles.agentTokens}>
            {agent.total_tokens != null
              ? `${agent.total_tokens.toLocaleString()} tokens`
              : "— tokens"}
            {agent.total_cost != null ? ` · ${formatCost(agent.total_cost)}` : ""}
          </span>
        </div>
      </button>
      {expanded && (
        <div style={styles.agentBody}>
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
            isRunning={false}
          />
        </div>
      )}
    </article>
  );
}

// Tab identity for the run-detail surface. Review + Values are gated on
// canonical mode (the reviewer diff + concept tree only exist there).
export type RunTabKey = "overview" | "agents" | "notes" | "checks" | "telemetry" | "review" | "values" | "eval";

const RUN_TAB_KEYS: readonly RunTabKey[] = [
  "overview", "agents", "notes", "checks", "telemetry", "review", "values", "eval",
];

/** Read the active run-detail tab from the URL's `?tab=` param so a specific
 *  tab is bookmarkable / shareable / restored on reload (docs/
 *  PLAN-design-qa-fixes.md R3). Returns null when absent or unrecognised. */
function readRunTabFromUrl(): RunTabKey | null {
  if (typeof window === "undefined") return null;
  const raw = new URLSearchParams(window.location.search).get("tab");
  return raw && (RUN_TAB_KEYS as readonly string[]).includes(raw)
    ? (raw as RunTabKey)
    : null;
}

/** Write the active tab into the URL without adding history entries (a tab
 *  switch shouldn't need a Back press per tab). Only touches the `?tab=`
 *  query — the pathname (managed by App.tsx) is left untouched. */
function writeRunTabToUrl(key: RunTabKey): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  url.searchParams.set("tab", key);
  window.history.replaceState(window.history.state, "", url.toString());
}

export function RunDetailView({
  detail, onDownload, onDelete, onForceAbort, onRegenerateNotes,
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
  const selectTab = useCallback((key: RunTabKey) => {
    setTab(key);
    writeRunTabToUrl(key);
  }, []);
  // Back/forward across tabs: re-read the query so the visible tab follows.
  useEffect(() => {
    const onPop = () => {
      const fromUrl = readRunTabFromUrl();
      if (fromUrl) setTab(fromUrl);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  // mTool fill modal (button, NOT a tab — gotcha #7).
  const [mtoolOpen, setMtoolOpen] = useState(false);
  // Delete confirmation — the shared ConfirmDialog replaces window.confirm so
  // every destructive action in the app confirms the same, plain-English way.
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Abort confirmation for a wedged `running` run (UX-QA #2).
  const [confirmAbort, setConfirmAbort] = useState(false);
  // Whether the user has acknowledged the "completed with errors" warning
  // banner (UX-QA #1). Until they do, Download stays a secondary (not primary)
  // action so the tool doesn't invite filing failed-check data at a glance.
  const [errorAck, setErrorAck] = useState(false);

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

  const canDownload = !!detail.merged_workbook_path;
  const isRunning = detail.status === "running";
  // A finished-but-flagged run (UX-QA #1): it completed AND offers a download,
  // but a consistency check failed — so the page must NOT look like a clean run.
  // `failed` is excluded (it has no workbook to download and already reads as a
  // failure); this targets the states that quietly pair an amber badge with a
  // big Download button.
  const isErrorOutcome =
    detail.status === "completed_with_errors" ||
    detail.status === "correction_exhausted";
  const failingCheckNames = (detail.cross_checks ?? [])
    .filter((c) => c.status === "failed")
    .map((c) => crossCheckLabel(c.name));
  // mTool fill needs a completed run (facts must be final) — same gate the
  // backend enforces (api/mtool.py _FILLABLE_STATUSES).
  const canFillMtool =
    detail.status === "completed" || detail.status === "completed_with_errors";
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

  // Tab definitions. Values is hidden unless canonical mode is on (the
  // concept tree doesn't exist for legacy runs). Order encodes the ranked
  // emphasis: audit (overview) → debug (agents/telemetry) → review (values).
  const tabs: { key: RunTabKey; label: string }[] = [
    { key: "overview", label: "Overview" },
    // "Activity" (was "Agents") shows what the AI did, per statement, with a
    // collapsed "Performance details" section that used to be the Telemetry tab.
    { key: "agents", label: "Activity" },
    { key: "notes", label: "Notes" },
    { key: "checks", label: "Cross-checks" },
    ...(canonicalEnabled
      ? [
          { key: "review" as RunTabKey, label: "AI review" },
          { key: "values" as RunTabKey, label: "Figures" },
        ]
      : []),
    // Gold-standard eval (v16): the Eval scorecard tab only appears when this
    // run was graded against a benchmark. A normal run never shows it.
    ...(detail.benchmark_id != null
      ? [{ key: "eval" as RunTabKey, label: "Eval" }]
      : []),
  ];

  // Clamp to a renderable tab. `initialTab="values"` (the /concepts/{id}
  // alias) can point at a tab that isn't available when canonical mode is off
  // or still loading — without this, no tab is active and no panel renders,
  // leaving a blank page below the tab bar (peer-review [6]).
  const activeTab: RunTabKey = tabs.some((t) => t.key === tab) ? tab : "overview";

  const rollup = detail.telemetry_rollup;

  // Outcome summary for the Overview strip (E1). Cross-check status can carry
  // an advisory "warning" beyond the typed enum, so compare as strings.
  const outcomes = (() => {
    const checks = (detail.cross_checks ?? []) as { status: string }[];
    const passed = checks.filter((c) => c.status === "passed").length;
    const failed = checks.filter((c) => c.status === "failed").length;
    const advisories = checks.filter((c) => c.status === "warning").length;
    // A run's "statements" = the FACE statements the user chose (UX-QA #13b).
    // The old filter only dropped NOTES_LIST_OF_NOTES + the named pseudo-agents,
    // so it silently counted SCOUT and every per-template notes agent
    // (NOTES_ACC_POLICIES, …) as statements — a 5-statement run with notes +
    // scout reported "8-10 statements". Exclude scout and ALL notes agents so
    // the count matches what the user selected.
    const statements = detail.agents.filter(
      (a) => a.statement_type !== "SCOUT" &&
        !a.statement_type.startsWith("NOTES_") &&
        !isNotes12StatementType(a.statement_type) &&
        pseudoAgentLabel(a.statement_type) === null,
    ).length;
    return {
      passed,
      graded: passed + failed,
      advisories,
      // "Needs attention" counts only BLOCKING failures (UX-QA #13a). Advisory
      // warnings are non-blocking and get their own calmer "Advisory notes"
      // tile — folding them in here made a clean-but-advisory run show an amber
      // "Needs attention" next to "8/8 passing", which read as a contradiction.
      needsAttention: failed,
      statements,
    };
  })();

  // Roving keyboard navigation for the tab bar (WAI-ARIA tabs pattern):
  // Arrow keys move between tabs, Home/End jump to ends, and focus follows
  // selection. Inline styles can't express this, so it lives here.
  const tabBarRef = useRef<HTMLDivElement>(null);
  const onTabKeyDown = (e: React.KeyboardEvent, index: number) => {
    let next = index;
    if (e.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    else return;
    e.preventDefault();
    selectTab(tabs[next].key);
    const btns =
      tabBarRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    btns?.[next]?.focus();
  };

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <div style={styles.headerText}>
          <div style={styles.kicker}>Run {detail.id}</div>
          <h3 style={styles.filename}>{detail.pdf_filename}</h3>
          <div style={styles.metaRow}>
            {statusBadge(runStatusDisplay(detail.status))}
            {isLegacy && (
              <span
                style={styles.legacyBadge}
                title="This run was recorded before the v2 schema — run config, merged workbook path, and token counts were not captured."
              >
                Legacy run (pre-v2)
              </span>
            )}
            <span style={styles.dim}>
              {new Date(detail.created_at).toLocaleString()}
            </span>
          </div>
          {/* AI disclaimer — one persistent line on the run report (Phase 6). */}
          <p style={styles.aiDisclaimer} role="note">
            Figures were extracted by AI — verify against the source PDF before filing.
          </p>
        </div>
        <div style={styles.actions}>
          {(() => {
            // Demote Download to a secondary until the user acknowledges the
            // error banner, so a completed-with-errors run doesn't lead with a
            // filled primary button inviting a (possibly wrong) filing (#1).
            const demote = isErrorOutcome && !errorAck;
            return (
              <button
                type="button"
                onClick={() => onDownload(detail.id)}
                disabled={!canDownload}
                className={demote ? uiClass.btnSecondary : uiClass.btnPrimary}
                style={demote ? ui.buttonSecondary : ui.buttonPrimary}
                title={
                  canDownload
                    ? demote
                      ? "Review the failing checks above before downloading"
                      : "Download the completed Excel file"
                    : "The Excel file isn't ready (the run stopped before it was assembled)"
                }
              >
                Download filled Excel
              </button>
            );
          })()}
          {/* The "Figures" tab is the single door to reviewing values — the
              old duplicate "Review values" button was removed (Phase 2). */}
          <button
            type="button"
            onClick={() => setMtoolOpen(true)}
            disabled={!canFillMtool}
            // Outline secondary, NOT a ghost/text link: the header actions
            // should share one visual language (filled primary + outline
            // secondaries). A bare orange text label here read as a hyperlink
            // sitting between two buttons (run-168 design critique).
            className={uiClass.btnSecondary}
            style={ui.buttonSecondary}
            title={
              canFillMtool
                ? "Fill an mTool template from this run's figures"
                : "mTool fill needs a completed run"
            }
          >
            Fill mTool template
          </button>
          {/* Hairline divider so the destructive action doesn't sit shoulder
              to shoulder with the most-clicked buttons. */}
          <span aria-hidden="true" style={styles.actionsDivider} />
          {isRunning && onForceAbort ? (
            // A run wedged in `running` can never be deleted (Delete is disabled
            // and the API 409s). Give the user an escape hatch: abort it, which
            // flips a dead row to `aborted` so Delete/Download become usable
            // (UX-QA #2).
            <button
              type="button"
              onClick={() => setConfirmAbort(true)}
              className={uiClass.btnDanger}
              style={ui.buttonDanger}
              title="Stop this run and mark it aborted — use this if a run has been stuck for a long time."
            >
              Abort run
            </button>
          ) : (
            <button
              type="button"
              onClick={handleDelete}
              disabled={!canDelete}
              className={uiClass.btnDanger}
              style={ui.buttonDanger}
              title={
                canDelete
                  ? "Delete run from history (on-disk files are kept)"
                  : "Can't delete a run that's still in progress — wait for it to finish or abort it first."
              }
            >
              Delete run
            </button>
          )}
        </div>
      </header>

      {/* Finished-but-flagged warning (UX-QA #1): a completed_with_errors /
          needs-review run must not look like a clean run. Name the failing
          check(s), link straight to the Cross-checks tab, and keep Download
          demoted until acknowledged. */}
      {isErrorOutcome && (
        <div style={styles.errorBanner} role="alert">
          <div style={styles.errorBannerBody}>
            <strong style={styles.errorBannerTitle}>
              This run finished, but a consistency check didn’t pass.
            </strong>
            <span style={styles.errorBannerText}>
              {failingCheckNames.length > 0 ? (
                <>
                  Review before filing:{" "}
                  {failingCheckNames.join(", ")}.
                </>
              ) : (
                <>Review the cross-checks before downloading or filing this run.</>
              )}
            </span>
          </div>
          <div style={styles.errorBannerActions}>
            <button
              type="button"
              onClick={() => selectTab("checks")}
              className={uiClass.btnSecondary}
              style={ui.buttonSecondary}
            >
              View cross-checks
            </button>
            {!errorAck && (
              <button
                type="button"
                onClick={() => setErrorAck(true)}
                className={uiClass.btnGhost}
                style={ui.buttonGhost}
              >
                I’ve reviewed these
              </button>
            )}
          </div>
        </div>
      )}

      <MtoolFillModal runId={detail.id} open={mtoolOpen} onClose={() => setMtoolOpen(false)} />

      <ConfirmDialog
        isOpen={confirmAbort}
        title={`Abort run ${detail.id}?`}
        message={
          <>
            This marks <strong>{detail.pdf_filename}</strong> as aborted so you
            can delete or re-run it. Use this only if the run has clearly
            stopped — any work still in progress will be lost.
          </>
        }
        confirmLabel="Abort run"
        onConfirm={() => {
          setConfirmAbort(false);
          onForceAbort?.(detail.id);
        }}
        onCancel={() => setConfirmAbort(false)}
      />

      <ConfirmDialog
        isOpen={confirmDelete}
        title={`Delete run ${detail.id}?`}
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

      {/* Tab bar — one shared navigation for the whole run, replacing the
          old long scroll of stacked sections + the disjointed /concepts jump. */}
      <div
        ref={tabBarRef}
        style={styles.tabBar}
        role="tablist"
        aria-label="Run detail sections"
      >
        {tabs.map((t, i) => {
          const active = t.key === activeTab;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={active}
              // Roving tabindex: only the active tab is in the tab order;
              // arrow keys reach the others (WAI-ARIA tabs pattern).
              tabIndex={active ? 0 : -1}
              onClick={() => selectTab(t.key)}
              onKeyDown={(e) => onTabKeyDown(e, i)}
              style={active ? styles.tabActive : styles.tab}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {/* One fade wrapper keyed on the active tab: switching tabs remounts it,
          replaying the shared fade-in so the new panel arrives instead of
          hard-swapping. The child <section> keeps role="tabpanel". */}
      <TabPanelFade tabKey={activeTab}>
      {activeTab === "overview" && (
        <section style={styles.section} role="tabpanel">
          {/* Lead with OUTCOMES — the first question is "did it extract
              correctly / anything to fix", not how many tokens it used (E1).
              Cost/tokens are demoted below the configuration. */}
          <div style={styles.metricStrip}>
            <MetricTile
              label="Checks passing"
              value={
                outcomes.graded > 0
                  ? `${outcomes.passed}/${outcomes.graded}`
                  : "—"
              }
              tone={
                outcomes.graded === 0
                  ? "neutral"
                  : outcomes.passed === outcomes.graded
                  ? "success"
                  : "warning"
              }
            />
            <MetricTile
              label="Needs attention"
              value={String(outcomes.needsAttention)}
              tone={outcomes.needsAttention > 0 ? "warning" : "success"}
            />
            {outcomes.advisories > 0 && (
              <MetricTile label="Advisory notes" value={String(outcomes.advisories)} />
            )}
            <MetricTile label="Statements" value={String(outcomes.statements)} />
          </div>
          <h4 style={styles.sectionHeading}>Run configuration</h4>
          <ConfigBlock config={detail.config} />
          {rollup && (
            <>
              <h4 style={styles.sectionHeading}>Performance</h4>
              <div style={styles.metricStrip}>
                <MetricTile label="Total tokens" value={rollup.total_tokens.toLocaleString()} />
                <MetricTile label="Est. cost" value={formatCost(rollup.total_cost)} />
                <MetricTile label="Turns" value={String(rollup.turn_count)} />
                <MetricTile label="Tool calls" value={String(rollup.tool_call_count)} />
                <MetricTile label="Agents" value={String(detail.agents.length)} />
              </div>
            </>
          )}
          {detail.repeat_group_id != null && (
            <ConsistencyPanel groupId={detail.repeat_group_id} />
          )}
        </section>
      )}

      {activeTab === "agents" && (
        <section style={styles.section} role="tabpanel" data-testid="run-detail-agents">
          {detail.agents.length === 0 ? (
            <p style={styles.dim}>Nothing was recorded for this run yet.</p>
          ) : (
            <div style={styles.agentStack} data-testid="run-detail-agent-list">
              {/* Statement reading order (UX-QA #14), matching the Figures
                  sheet-nav so the app orders statements one way everywhere:
                  scout first, then face statements in reading order, then notes
                  / AI-review pseudo-agents (stable, keeping their arrival
                  order). Beats the backend's incidental alphabetical order. */}
              {[...detail.agents]
                .sort((a, b) => agentActivityOrder(a) - agentActivityOrder(b))
                .map((agent) => (
                  <AgentCard key={agent.id} agent={agent} />
                ))}
            </div>
          )}
          {/* Timing + AI-usage detail (the former Telemetry tab), tucked into a
              collapsed disclosure so the everyday view stays about what the AI
              did, not token/latency internals. */}
          <details style={styles.perfDetails} data-testid="run-detail-telemetry">
            <summary style={styles.perfSummary}>Performance details</summary>
            <div style={{ marginTop: pwc.space.md }}>
              <AgentTelemetryPanel detail={detail} />
            </div>
          </details>
        </section>
      )}

      {activeTab === "notes" && (
        <section style={styles.section} role="tabpanel" data-testid="run-detail-notes-review">
          <NotesCoveragePanel runId={detail.id} />
          <NotesReviewerPanel runId={detail.id} />
          <NotesReviewTab runId={detail.id} onRegenerate={onRegenerateNotes} />
        </section>
      )}

      {activeTab === "checks" && (
        <section style={styles.section} role="tabpanel">
          <div style={styles.crossCheckScroller}>
            <ValidatorTab
              crossChecks={crossChecksForValidator(detail.cross_checks)}
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
            runId={detail.id}
            initialCrossChecks={crossChecksForValidator(detail.cross_checks)}
            onRegenerateNotes={onRegenerateNotes}
          />
        </section>
      )}

      {/* Gold-standard eval (v16): the scorecard. Lazy-mounted (only rendered
          when this tab is active) and only present when the run was graded. */}
      {activeTab === "eval" && detail.benchmark_id != null && (
        <section style={styles.section} role="tabpanel" data-testid="run-detail-eval">
          <EvalTab runId={detail.id} initialScore={detail.eval_score ?? null} />
        </section>
      )}
      </TabPanelFade>
    </div>
  );
}

/** A single labelled metric in the Overview strip. */
function MetricTile({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "success" | "warning";
}) {
  const accent =
    tone === "success" ? pwc.success : tone === "warning" ? pwc.warning : undefined;
  return (
    <div style={styles.metricTile}>
      <div style={{ ...styles.metricValue, ...(accent ? { color: accent } : {}) }}>
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
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-end",
    gap: pwc.space.lg,
    flexWrap: "wrap" as const,
    // Page-header treatment: hairline rule + generous separation from the
    // detail body, matching the design system's page-head.
    paddingBottom: pwc.space.lg,
    borderBottom: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  headerText: {
    minWidth: 0,
  } as React.CSSProperties,
  kicker: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: pwc.weight.medium,
    textTransform: "uppercase" as const,
    letterSpacing: "2px",
    color: pwc.orange500,
    marginBottom: pwc.space.xs,
  } as React.CSSProperties,
  filename: {
    fontFamily: pwc.fontHeading,
    fontSize: 22,
    fontWeight: pwc.weight.semibold,
    letterSpacing: "-0.3px",
    color: pwc.grey900,
    margin: 0,
  } as React.CSSProperties,
  metaRow: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
  dim: {
    color: pwc.grey700,
    fontSize: 13,
    fontFamily: pwc.fontBody,
  } as React.CSSProperties,
  aiDisclaimer: {
    margin: `${pwc.space.sm}px 0 0`,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
  } as React.CSSProperties,
  actions: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  // Finished-but-flagged banner (#1): neutral surface with an amber left-rule,
  // matching the design-system alert treatment (no loud full-bleed fill).
  errorBanner: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    flexWrap: "wrap" as const,
    gap: pwc.space.md,
    padding: pwc.space.md,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderLeft: `3px solid ${pwc.warning}`,
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
    fontSize: 13,
    color: pwc.grey700,
  } as React.CSSProperties,
  errorBannerActions: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
  // Separates the destructive Delete from the everyday actions.
  actionsDivider: {
    alignSelf: "stretch",
    width: 1,
    background: pwc.grey200,
    margin: `0 ${pwc.space.xs}px`,
  } as React.CSSProperties,
  // Tab bar: a thin row of buttons with the active one underlined in the
  // brand orange. Data-dense chrome — keep it tight, not airy.
  tabBar: {
    display: "flex",
    gap: pwc.space.xs,
    borderBottom: `1px solid ${pwc.grey200}`,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  tab: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 600,
    color: pwc.grey500,
    background: "transparent",
    border: "none",
    borderBottom: "2px solid transparent",
    marginBottom: -1,
    cursor: "pointer",
    // Crossfade the active-tab indicator + label colour when switching
    // (Phase 7 motion tokens).
    transition: `color ${pwc.motion.duration.fast} ${pwc.motion.easing}, border-color ${pwc.motion.duration.fast} ${pwc.motion.easing}`,
  } as React.CSSProperties,
  tabActive: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 600,
    color: pwc.orange500,
    background: "transparent",
    border: "none",
    borderBottom: `2px solid ${pwc.orange500}`,
    marginBottom: -1,
    cursor: "pointer",
    transition: `color ${pwc.motion.duration.fast} ${pwc.motion.easing}, border-color ${pwc.motion.duration.fast} ${pwc.motion.easing}`,
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
    fontFamily: pwc.fontMono,
    fontSize: 20,
    fontWeight: pwc.weight.regular,
    color: pwc.grey900,
  } as React.CSSProperties,
  metricLabel: {
    fontFamily: pwc.fontBody,
    fontSize: 11,
    textTransform: "uppercase" as const,
    letterSpacing: 0.5,
    color: pwc.grey500,
  } as React.CSSProperties,
  section: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.sm,
  } as React.CSSProperties,
  sectionHeading: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey700,
    margin: 0,
    textTransform: "uppercase" as const,
    letterSpacing: 0.5,
  } as React.CSSProperties,
  // Button-flavoured variant of sectionHeading used for collapsible
  // sections. Strips all default button chrome so it visually matches an
  // <h4> while staying semantically a control with aria-expanded.
  collapsibleSectionHeading: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey700,
    margin: 0,
    textTransform: "uppercase" as const,
    letterSpacing: 0.5,
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
    fontWeight: 600,
    color: pwc.grey700,
    minWidth: 140,
  } as React.CSSProperties,
  dd: {
    margin: 0,
    color: pwc.grey900,
  } as React.CSSProperties,
  // Phase 9: per-agent card stack replaces the stats table. Each card
  // gets its own rounded border so the agent boundaries stay visible
  // when several timelines share the detail view.
  agentStack: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.md,
  } as React.CSSProperties,
  perfDetails: {
    marginTop: pwc.space.lg,
    borderTop: `1px solid ${pwc.grey200}`,
    paddingTop: pwc.space.md,
  } as React.CSSProperties,
  perfSummary: {
    cursor: "pointer",
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: pwc.weight.medium,
    color: pwc.grey700,
  } as React.CSSProperties,
  agentCard: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    background: pwc.white,
    // Fade-up as cards render. Keyed by agent.id upstream, so during a live
    // run only a newly-arrived agent animates; existing cards stay put.
    animation: `fade-in ${pwc.motion.duration.base} ${pwc.motion.easing}`,
  } as React.CSSProperties,
  // Clickable header serves as the collapse/expand toggle. Styled as a
  // plain block (no button chrome) so it reads as a card row, not a
  // standalone control — the chevron + aria-expanded carry the affordance.
  agentHeaderButton: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: "transparent",
    border: "none",
    width: "100%",
    textAlign: "left" as const,
    cursor: "pointer",
    fontFamily: "inherit",
    font: "inherit",
    color: "inherit",
  } as React.CSSProperties,
  agentBody: {
    borderTop: `1px solid ${pwc.grey100}`,
  } as React.CSSProperties,
  agentChevron: {
    color: pwc.grey500,
    fontSize: 12,
    width: 12,
    display: "inline-block",
  } as React.CSSProperties,
  agentTitleRow: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  agentStatement: {
    fontFamily: pwc.fontMono,
    fontWeight: 600,
    fontSize: 14,
    color: pwc.grey900,
  } as React.CSSProperties,
  agentVariant: {
    color: pwc.grey500,
    fontSize: 13,
  } as React.CSSProperties,
  // Plain-English gloss next to the statement code (UX-QA #12).
  agentSubtitle: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
  } as React.CSSProperties,
  // v17 (item 9): muted mono chip naming the failure class on failed rows.
  agentErrorType: {
    color: pwc.grey500,
    fontFamily: pwc.fontMono,
    fontSize: 11,
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
  agentTokens: {
    marginLeft: "auto",
  } as React.CSSProperties,
  legacyBadge: {
    ...ui.badgeNeutral,
    fontSize: 11,
    border: `1px solid ${pwc.grey200}`,
    textTransform: "uppercase" as const,
    letterSpacing: 0.3,
  } as React.CSSProperties,
} as const;
