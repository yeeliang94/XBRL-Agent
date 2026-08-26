import React, { useRef } from "react";
import { pwc } from "../lib/theme";
import type { AgentTabStatus } from "../lib/types";
import { NON_AGENT_TAB_IDS } from "../lib/agentTabKinds";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type { AgentTabStatus };

export interface AgentTabState {
  agentId: string;
  label: string;
  status: AgentTabStatus;
  role: string;
  // Phase 5.2 / peer-review [M1]: when present, renders beneath the
  // main tab label as a secondary chip. Only Notes-12 populates this
  // today (via `agentSubAgentSummary`); other agents pass undefined.
  // Kept as a plain string so AgentTabs has no runtime dep on the
  // reducer module.
  subLabel?: string | null;
  // Honest-completion flag (peer-review F1): non-null when the agent
  // finalised with an acknowledged, audited gap (status stays "complete").
  // Mirrors AgentState.flag; renders the "needs review" ⚠ chip below.
  flag?: string | null;
}

export interface AgentTabsProps {
  agents: Record<string, AgentTabState>;
  tabOrder: string[];          // ordered agent IDs for active tabs
  activeTab: string;
  onTabClick: (agentId: string) => void;
  skeletonTabs?: string[];     // labels for face statements not yet started (greyed-out)
  // Phase 8: gate statement tabs so pre-run state doesn't flash all 5
  // skeletons. Pass the statements the user actually picked for this run;
  // anything not in the list (and not a SPECIAL_TAB_IDS member or notes
  // agent) is hidden.
  statementsInRun?: string[];
  // PLAN §4 Phase D.3: mirror of statementsInRun for notes templates. Any
  // notes agent whose role isn't in this list (and has no event activity
  // yet) is gated out. Notes tabs render AFTER statement tabs but BEFORE
  // scout/validator so the visual bucketing stays stable.
  notesInRun?: string[];
  // Skeleton rows for notes templates the user picked but that haven't
  // emitted their first event yet — same "greyed chip" treatment as the
  // face-statement skeletonTabs.
  notesSkeletons?: string[];
}

// Tabs in this set follow their own lifecycle (scout is spun up before the
// run starts; validator is added on run_complete) and are therefore exempt
// from the statementsInRun gate. The same set drives the per-agent
// stop/rerun toolbar gating in ActiveTabPanel — both callsites import
// from `lib/agentTabKinds` to keep the contract in lockstep.
const SPECIAL_TAB_IDS = NON_AGENT_TAB_IDS;

// Notes agent_ids carry a stable `notes:` prefix (notes/coordinator.py).
// Kept here rather than imported so AgentTabs has no runtime dep on the
// notes_types mirror — the prefix is the single contract between the
// coordinator SSE events and the frontend bucketer.
const NOTES_TAB_PREFIX = "notes:";

// ---------------------------------------------------------------------------
// Status badge — small indicator showing agent state
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: AgentTabStatus }) {
  const spec = STATUS_BADGES[status];
  return (
    <span data-status={status} style={spec.wrapper} aria-label={spec.label}>
      <span style={spec.dot} />
    </span>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

function AgentTabsImpl({
  agents,
  tabOrder,
  activeTab,
  onTabClick,
  skeletonTabs,
  statementsInRun,
  notesInRun,
  notesSkeletons,
}: AgentTabsProps) {
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  // Phase 8 + D.3 gating. The rule is:
  //   Render a tab if ANY of the following is true:
  //     1. The tab is a SPECIAL_TAB_IDS member (scout/validator) AND the
  //        agent exists in state — these follow their own lifecycle.
  //     2. Notes agent (agent_id starts "notes:") — gated by notesInRun
  //        unless that prop is undefined (legacy callers / history views).
  //     3. `statementsInRun` was not passed at all (legacy callers / history
  //        detail views) — we treat that as "no gate, show everything".
  //     4. The agent's role is in statementsInRun — i.e. the user actually
  //        picked this statement for the current run.
  //
  // Ordering: statement workstreams first, then notes, then run checks.
  // The visible navigator groups those buckets vertically.
  const gatedOrder = (() => {
    const statementIds: string[] = [];
    const notesIds: string[] = [];
    let scoutId: string | null = null;
    let validatorId: string | null = null;
    let notesValidatorId: string | null = null;
    let correctionId: string | null = null;
    for (const id of tabOrder) {
      const agent = agents[id];
      if (!agent) continue;
      if (SPECIAL_TAB_IDS.has(id)) {
        // scout and validator ride their own lifecycle — always shown.
        // NOTES_VALIDATOR joins them (peer-review F1) so its skip-emit
        // actually has a visible selector in the run-checks group.
        // CORRECTION (the reviewer pass) is the same shape — it must be
        // bucketed explicitly here, else it'd hit this branch, match no
        // `id === …` case, and fall through `continue` into nowhere (the
        // very disappearance we're fixing).
        if (id === "scout") scoutId = id;
        else if (id === "validator") validatorId = id;
        else if (id === "NOTES_VALIDATOR") notesValidatorId = id;
        else if (id === "CORRECTION") correctionId = id;
        continue;
      }
      if (id.startsWith(NOTES_TAB_PREFIX)) {
        // Notes tabs — gated by notesInRun unless prop is undefined.
        if (notesInRun === undefined || notesInRun.includes(agent.role)) {
          notesIds.push(id);
        }
        continue;
      }
      // Statement tabs — gated by statementsInRun unless prop is undefined.
      if (statementsInRun === undefined || statementsInRun.includes(agent.role)) {
        statementIds.push(id);
      }
    }
    return [
      ...statementIds,
      ...notesIds,
      ...(notesValidatorId ? [notesValidatorId] : []),
      ...(scoutId ? [scoutId] : []),
      // Reviewer sits just before Cross-checks (validator) — it runs right
      // after the cross-check pass, so this mirrors the run timeline.
      ...(correctionId ? [correctionId] : []),
      ...(validatorId ? [validatorId] : []),
    ];
  })();

  // Split gatedOrder into purpose-led buckets so queued workstreams stay
  // adjacent to active work of the same type.
  const statementActive: string[] = [];
  const notesActive: string[] = [];
  let scoutActive: string | null = null;
  let validatorActive: string | null = null;
  let notesValidatorActive: string | null = null;
  let correctionActive: string | null = null;
  for (const id of gatedOrder) {
    if (id === "scout") scoutActive = id;
    else if (id === "validator") validatorActive = id;
    else if (id === "NOTES_VALIDATOR") notesValidatorActive = id;
    else if (id === "CORRECTION") correctionActive = id;
    else if (id.startsWith(NOTES_TAB_PREFIX)) notesActive.push(id);
    else statementActive.push(id);
  }
  const navigationOrder = [
    ...statementActive,
    ...notesActive,
    ...(scoutActive ? [scoutActive] : []),
    ...(notesValidatorActive ? [notesValidatorActive] : []),
    ...(correctionActive ? [correctionActive] : []),
    ...(validatorActive ? [validatorActive] : []),
  ];

  // Helper rendering one active workstream selector. Per-agent abort/rerun
  // controls live in the focused activity pane.
  const renderTab = (agentId: string) => {
    const agent = agents[agentId];
    if (!agent) return null;
    const isActive = agentId === activeTab;
    return (
      <button
        key={agentId}
        ref={(node) => { tabRefs.current[agentId] = node; }}
        data-agent-label={agent.label}
        role="tab"
        aria-selected={isActive}
        tabIndex={isActive ? 0 : -1}
        onClick={() => onTabClick(agentId)}
        onKeyDown={(event) => {
          if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const currentIndex = navigationOrder.indexOf(agentId);
          const nextIndex = event.key === "Home"
            ? 0
            : event.key === "End"
              ? navigationOrder.length - 1
              : event.key === "ArrowDown"
                ? (currentIndex + 1) % navigationOrder.length
                : (currentIndex - 1 + navigationOrder.length) % navigationOrder.length;
          const nextId = navigationOrder[nextIndex];
          if (!nextId) return;
          onTabClick(nextId);
          tabRefs.current[nextId]?.focus();
        }}
        title={agent.label}
        className="agent-tab"
        style={{ ...styles.tab, ...(isActive ? styles.tabActive : {}) }}
      >
        <StatusBadge status={agent.status} />
        <span style={styles.tabLabelStack}>
          <span style={styles.tabLabelText}>{agent.label}</span>
          {agent.subLabel && (
            <span style={styles.tabSubLabel}>{agent.subLabel}</span>
          )}
        </span>
        {agent.flag && (
          // Honest-completion flag (peer-review F1): finalised but the agent
          // accepted a known imbalance / unfilled-mandatory — needs a human
          // look. The plain chip reads "Needs review"; the model's free-text
          // detail stays in the tooltip (warningText token, not off-palette).
          <span
            aria-label={`Needs your review: ${agent.flag}`}
            title={agent.flag}
            style={{
              marginLeft: 4,
              color: pwc.warningText,
              fontSize: 11,
              fontWeight: pwc.weight.medium,
              whiteSpace: "nowrap",
            }}
          >
            ⚠ Needs review
          </span>
        )}
        {!agent.flag && (
          <span style={{ ...styles.tabStatus, ...(agent.status === "running" ? styles.tabStatusRunning : {}) }}>
            {STATUS_BADGES[agent.status].label}
          </span>
        )}
      </button>
    );
  };

  const completedCount = gatedOrder.filter((id) => agents[id]?.status === "complete").length;

  return (
    <div
      role="tablist"
      aria-label="Run workstreams"
      aria-orientation="vertical"
      className="workstream-nav"
      style={styles.tabBar}
    >
      <div style={styles.navigatorHeader}>
        <div>
          <div style={styles.navigatorTitle}>Workstreams</div>
          <div style={styles.navigatorHint}>Select a workstream to inspect its activity.</div>
        </div>
        <span style={styles.navigatorCount}>{completedCount} complete</span>
      </div>

      {(statementActive.length > 0 || (skeletonTabs?.length ?? 0) > 0) && (
        <div data-bucket="statements" style={styles.tabGroup}>
          <div style={styles.groupLabel}>Financial statements</div>
          {statementActive.map(renderTab)}
          {skeletonTabs?.map((label) => (
            <SkeletonTab key={`skeleton-${label}`} keyPrefix="skeleton" label={label} />
          ))}
        </div>
      )}

      {(notesActive.length > 0 || (notesSkeletons?.length ?? 0) > 0) && (
        <div data-bucket="notes" style={styles.tabGroup}>
          <div style={styles.groupLabel}>Notes</div>
          {notesActive.map(renderTab)}
          {notesSkeletons?.map((label) => (
            <SkeletonTab key={`notes-skeleton-${label}`} keyPrefix="notes-skeleton" label={label} />
          ))}
        </div>
      )}

      {(notesValidatorActive || scoutActive || correctionActive || validatorActive) && (
        <div data-bucket="run-checks" style={styles.tabGroup}>
          <div style={styles.groupLabel}>Run checks</div>
          {scoutActive && renderTab(scoutActive)}
          {notesValidatorActive && renderTab(notesValidatorActive)}
          {correctionActive && renderTab(correctionActive)}
          {validatorActive && renderTab(validatorActive)}
        </div>
      )}
    </div>
  );
}

/** Render a greyed-out skeleton tab for a not-yet-started agent. */
function SkeletonTab({ keyPrefix, label }: { keyPrefix: string; label: string }) {
  return (
    <button
      key={`${keyPrefix}-${label}`}
      data-agent-label={label}
      role="tab"
      aria-selected={false}
      aria-disabled="true"
      disabled
      className="agent-tab"
      style={{ ...styles.tab, ...styles.tabSkeleton }}
    >
      <span data-status="pending" style={badgeStyles.skeleton} />
      <span style={styles.tabLabelText}>{label}</span>
      <span style={styles.tabStatus}>Queued</span>
    </button>
  );
}

// Equal-by-content for the fields the tab bar actually reads. The parent
// (`ExtractView`) rebuilds `agentTabsAgents` any time `state.agents`
// changes ref — which happens on every SSE event, including token_update.
// Shallow-ref equality would therefore re-render the tab bar on every
// token delta. Comparing the 4 tab-relevant fields per agent plus the
// array-shaped props by content keeps the tab bar stable across
// high-frequency non-tab events (peer-review finding #5).
// Exported for unit testing — do not depend on this export from app code.
export function areAgentTabsPropsEqual(
  prev: AgentTabsProps,
  next: AgentTabsProps,
): boolean {
  if (
    prev.activeTab !== next.activeTab ||
    prev.onTabClick !== next.onTabClick
  ) {
    return false;
  }
  if (!arrayShallowEqual(prev.tabOrder, next.tabOrder)) return false;
  if (!arrayShallowEqual(prev.statementsInRun ?? [], next.statementsInRun ?? [])) return false;
  if (!arrayShallowEqual(prev.notesInRun ?? [], next.notesInRun ?? [])) return false;
  if (!arrayShallowEqual(prev.skeletonTabs ?? [], next.skeletonTabs ?? [])) return false;
  if (!arrayShallowEqual(prev.notesSkeletons ?? [], next.notesSkeletons ?? [])) return false;
  const prevKeys = Object.keys(prev.agents);
  const nextKeys = Object.keys(next.agents);
  if (prevKeys.length !== nextKeys.length) return false;
  for (const key of prevKeys) {
    const a = prev.agents[key];
    const b = next.agents[key];
    if (!b) return false;
    if (
      a.agentId !== b.agentId ||
      a.label !== b.label ||
      a.status !== b.status ||
      a.role !== b.role ||
      // subLabel renders a secondary chip beneath the main label (Notes-12
      // fan-out progress). Missing this let progress chips go stale until
      // the parent status flipped (peer review [MEDIUM]). Normalise null
      // and undefined so "absent ↔ absent" stays equal.
      (a.subLabel ?? null) !== (b.subLabel ?? null)
    ) {
      return false;
    }
  }
  return true;
}

function arrayShallowEqual<T>(a: readonly T[], b: readonly T[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

export const AgentTabs = React.memo(AgentTabsImpl, areAgentTabsPropsEqual);

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = {
  tabBar: {
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "stretch" as const,
    background: pwc.white,
    minWidth: 0,
    overflowY: "auto" as const,
    maxHeight: 620,
  },
  navigatorHeader: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: pwc.space.md,
    padding: `${pwc.space.lg}px ${pwc.space.lg}px ${pwc.space.md}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
  },
  navigatorTitle: {
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    lineHeight: 1.3,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey900,
  },
  navigatorHint: {
    marginTop: 2,
    fontFamily: pwc.fontBody,
    fontSize: 12,
    lineHeight: 1.4,
    color: pwc.grey700,
  },
  navigatorCount: {
    flexShrink: 0,
    fontFamily: pwc.fontMono,
    fontSize: 11,
    color: pwc.grey700,
  },
  tabGroup: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
    padding: `${pwc.space.md}px ${pwc.space.sm}px`,
    borderBottom: `1px solid ${pwc.grey200}`,
  },
  groupLabel: {
    padding: `0 ${pwc.space.sm}px ${pwc.space.xs}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    fontWeight: pwc.weight.semibold,
    color: pwc.grey700,
    letterSpacing: "0.02em",
  },
  tab: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs + 2,
    width: "100%",
    minHeight: 42,
    padding: `${pwc.space.sm}px ${pwc.space.sm}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: pwc.weight.medium,
    color: pwc.grey700,
    background: "transparent",
    border: "none",
    borderLeft: "3px solid transparent",
    borderRadius: pwc.radius.md,
    cursor: "pointer",
    textAlign: "left" as const,
    transition: `color ${pwc.motion.duration.fast} ${pwc.motion.easing}, background ${pwc.motion.duration.fast} ${pwc.motion.easing}`,
    minWidth: 0,
  },
  tabActive: {
    color: pwc.grey900,
    fontWeight: pwc.weight.semibold,
    background: pwc.grey100,
    borderLeftColor: pwc.orange500,
  },
  tabSkeleton: {
    color: pwc.grey300,
    cursor: "default",
    opacity: 0.6,
    background: "transparent",
  },
  // Stack the main label and sub-label (when present) vertically inside
  // the tab. Most tabs have no subLabel so the render falls back to a
  // single-line appearance automatically. minWidth:0 lets the inner
  // text node shrink so ellipsis truncation kicks in instead of forcing
  // the parent flex item wider.
  tabLabelStack: {
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "flex-start" as const,
    lineHeight: 1.15,
    minWidth: 0,
    overflow: "hidden",
  },
  tabLabelText: {
    overflow: "hidden",
    textOverflow: "ellipsis" as const,
    whiteSpace: "nowrap" as const,
    maxWidth: 170,
  },
  tabSubLabel: {
    fontSize: 11,
    fontWeight: 400,
    color: pwc.grey500,
    fontFamily: pwc.fontBody,
    overflow: "hidden",
    textOverflow: "ellipsis" as const,
    whiteSpace: "nowrap" as const,
    maxWidth: 170,
  },
  tabStatus: {
    marginLeft: "auto",
    flexShrink: 0,
    fontFamily: pwc.fontBody,
    fontSize: 11,
    color: pwc.grey700,
  },
  tabStatusRunning: {
    color: pwc.orange700,
  },
} as const;

const badgeStyles = {
  complete: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 12,
    height: 12,
    borderRadius: "50%",
    background: "transparent",
  } as React.CSSProperties,
  completeDot: {
    width: 6,
    height: 6,
    borderRadius: "50%",
    background: pwc.success,
  } as React.CSSProperties,
  running: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 12,
    height: 12,
    borderRadius: "50%",
    background: pwc.orange50,
  } as React.CSSProperties,
  runningDot: {
    display: "inline-block",
    width: 8,
    height: 8,
    borderRadius: "50%",
    background: pwc.orange400,
    animation: "pulse 1.2s ease-in-out infinite",
  } as React.CSSProperties,
  failed: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 12,
    height: 12,
    borderRadius: "50%",
    background: "transparent",
  } as React.CSSProperties,
  failedDot: {
    width: 6,
    height: 6,
    borderRadius: "50%",
    background: pwc.error,
  } as React.CSSProperties,
  cancelled: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 12,
    height: 12,
    borderRadius: "50%",
    background: pwc.grey100,
  } as React.CSSProperties,
  cancelledDot: {
    width: 6,
    height: 6,
    borderRadius: "50%",
    background: pwc.grey500,
  } as React.CSSProperties,
  aborting: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 12,
    height: 12,
    borderRadius: "50%",
    background: "transparent",
  } as React.CSSProperties,
  abortingDot: {
    display: "inline-block",
    width: 8,
    height: 8,
    borderRadius: "50%",
    background: pwc.error,
    animation: "pulse 0.8s ease-in-out infinite",
  } as React.CSSProperties,
  pending: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 12,
    height: 12,
    borderRadius: "50%",
    background: pwc.white,
    border: `1.5px solid ${pwc.grey300}`,
  } as React.CSSProperties,
  pendingDot: {
    width: 4,
    height: 4,
    borderRadius: "50%",
    background: pwc.grey300,
  } as React.CSSProperties,
  skeleton: {
    display: "inline-block",
    width: 8,
    height: 8,
    borderRadius: "50%",
    background: pwc.grey300,
  } as React.CSSProperties,
} as const;

// Status → badge rendering spec. One row per AgentTabStatus; TS enforces
// completeness because the Record enforces exhaustive keys. Lives at the
// bottom of the file so it can reference the `badgeStyles` object above.
const STATUS_BADGES: Record<
  AgentTabStatus,
  { wrapper: React.CSSProperties; dot: React.CSSProperties; label: string }
> = {
  complete:  { wrapper: badgeStyles.complete,  dot: badgeStyles.completeDot,  label: "Complete" },
  running:   { wrapper: badgeStyles.running,   dot: badgeStyles.runningDot,   label: "Running" },
  aborting:  { wrapper: badgeStyles.aborting,  dot: badgeStyles.abortingDot,  label: "Aborting" },
  failed:    { wrapper: badgeStyles.failed,    dot: badgeStyles.failedDot,    label: "Failed" },
  cancelled: { wrapper: badgeStyles.cancelled, dot: badgeStyles.cancelledDot, label: "Cancelled" },
  pending:   { wrapper: badgeStyles.pending,   dot: badgeStyles.pendingDot,   label: "Pending" },
};
