import React from "react";
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
  // Ordering: statement tabs first, then notes tabs, then scout, then
  // validator — so users always find Validator at the far right after a
  // run completes, and notes slot into a predictable middle group.
  const gatedOrder = (() => {
    const statementIds: string[] = [];
    const notesIds: string[] = [];
    let scoutId: string | null = null;
    let validatorId: string | null = null;
    let notesValidatorId: string | null = null;
    for (const id of tabOrder) {
      const agent = agents[id];
      if (!agent) continue;
      if (SPECIAL_TAB_IDS.has(id)) {
        // scout and validator ride their own lifecycle — always shown.
        // NOTES_VALIDATOR joins them (peer-review F1) so its skip-emit
        // actually has a visible tab; bucketed with notes below since it
        // is thematically adjacent to the notes agents that feed it.
        if (id === "scout") scoutId = id;
        else if (id === "validator") validatorId = id;
        else if (id === "NOTES_VALIDATOR") notesValidatorId = id;
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
      ...(validatorId ? [validatorId] : []),
    ];
  })();

  // Split gatedOrder into its four buckets so skeletons can slot in
  // adjacent to their active counterparts. Rendering order:
  //   [statement active, statement skeletons, notes active, notes skeletons,
  //    scout, validator]
  // Previously skeletons landed in one trailing block after scout/validator,
  // which put notes skeletons at the far right of the bar instead of
  // inside the notes bucket (peer-review LOW).
  const statementActive: string[] = [];
  const notesActive: string[] = [];
  let scoutActive: string | null = null;
  let validatorActive: string | null = null;
  let notesValidatorActive: string | null = null;
  for (const id of gatedOrder) {
    if (id === "scout") scoutActive = id;
    else if (id === "validator") validatorActive = id;
    else if (id === "NOTES_VALIDATOR") notesValidatorActive = id;
    else if (id.startsWith(NOTES_TAB_PREFIX)) notesActive.push(id);
    else statementActive.push(id);
  }

  // Helper rendering one active tab. Closes over props so each bucket
  // below can call it without threading them as arguments. Per-tab
  // abort/rerun controls live in the activity-header toolbar below
  // (ActiveTabPanel) so the tab strip stays a clean navigation row.
  const renderTab = (agentId: string) => {
    const agent = agents[agentId];
    if (!agent) return null;
    const isActive = agentId === activeTab;
    return (
      <button
        key={agentId}
        role="tab"
        aria-selected={isActive}
        onClick={() => onTabClick(agentId)}
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
      </button>
    );
  };

  return (
    // Bug 3 — swapped from a single horizontal-scroll row (which truncated
    // labels once the strip grew past 6 tabs) to a two-row flex-wrap layout
    // with semantic buckets. Statements on top, notes + scout/validator
    // below, so a user scanning 13 tabs sees the two mental groups at a
    // glance instead of hunting through a collapsed row.
    <div role="tablist" className="tab-bar-scroll" style={styles.tabBar}>
      <div data-bucket="statements" style={styles.tabRow}>
        {statementActive.map(renderTab)}
        {skeletonTabs?.map((label) => (
          <SkeletonTab key={`skeleton-${label}`} keyPrefix="skeleton" label={label} />
        ))}
      </div>
      <div data-bucket="notes-and-special" style={styles.tabRow}>
        {notesActive.map(renderTab)}
        {notesSkeletons?.map((label) => (
          <SkeletonTab key={`notes-skeleton-${label}`} keyPrefix="notes-skeleton" label={label} />
        ))}
        {notesValidatorActive && renderTab(notesValidatorActive)}
        {scoutActive && renderTab(scoutActive)}
        {validatorActive && renderTab(validatorActive)}
      </div>
    </div>
  );
}

/** Render a greyed-out skeleton tab for a not-yet-started agent. */
function SkeletonTab({ keyPrefix, label }: { keyPrefix: string; label: string }) {
  return (
    <button
      key={`${keyPrefix}-${label}`}
      role="tab"
      aria-selected={false}
      aria-disabled="true"
      disabled
      className="agent-tab"
      style={{ ...styles.tab, ...styles.tabSkeleton }}
    >
      <span data-status="pending" style={badgeStyles.skeleton} />
      <span style={styles.tabLabelText}>{label}</span>
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
    // Bug 3 — flex column of two rows, each row flex-wraps its own tabs.
    // The wrap contract lives on `tabRow.flexWrap: wrap` below; the outer
    // column only ever has two children (the two row divs), so an outer
    // flexWrap would never fire and was a misleading style-prop. Tests
    // now assert flexWrap on the row buckets directly (peer-review R-1).
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "stretch",
    background: pwc.white,
    borderRadius: `${pwc.radius.md}px ${pwc.radius.md}px 0 0`,
    border: `1px solid ${pwc.grey200}`,
    paddingLeft: pwc.space.sm,
    paddingRight: pwc.space.sm,
    // Deliberately no overflowX — the two row buckets wrap internally, so
    // an overflow scroll would only kick in on absurd narrow viewports,
    // and even there wrap is the right failure mode.
    minHeight: 44,
    rowGap: 2,
  },
  // One horizontal row of tabs inside tabBar. `flexWrap: wrap` on the row
  // lets an individual row soft-break onto a third visual line if a user
  // picks (say) all 5 statements plus an extra-long label; usually both
  // rows sit single-line.
  tabRow: {
    display: "flex",
    flexWrap: "wrap" as const,
    alignItems: "stretch",
    gap: 0,
  },
  // Underline-style tab button. Active state is signalled by an
  // inset bottom shadow (acts as an underline that doesn't shift
  // layout) plus a faint orange tint, matching the rest of the
  // card-based page language. Hover styling lives in index.css
  // (.agent-tab:hover) since inline styles can't address :hover.
  tab: {
    display: "inline-flex",
    alignItems: "center",
    gap: pwc.space.xs + 2,
    padding: `${pwc.space.sm + 2}px ${pwc.space.md + 2}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 500,
    color: pwc.grey700,
    background: "transparent",
    border: "none",
    borderRadius: 0,
    cursor: "pointer",
    whiteSpace: "nowrap" as const,
    transition: "color 0.15s, background 0.15s, box-shadow 0.15s",
    minWidth: 0,
    maxWidth: 220,
  },
  tabActive: {
    color: pwc.orange700,
    fontWeight: 600,
    background: pwc.orange50,
    boxShadow: `inset 0 -2px 0 ${pwc.orange500}`,
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
    display: "inline-flex",
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
    maxWidth: 180,
  },
  tabSubLabel: {
    fontSize: 11,
    fontWeight: 400,
    color: pwc.grey500,
    fontFamily: pwc.fontBody,
    overflow: "hidden",
    textOverflow: "ellipsis" as const,
    whiteSpace: "nowrap" as const,
    maxWidth: 180,
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
    background: pwc.successBg,
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
    background: pwc.errorBg,
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
    background: pwc.errorBg,
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
