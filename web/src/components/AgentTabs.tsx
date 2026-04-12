import { pwc } from "../lib/theme";
import type { AgentTabStatus } from "../lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type { AgentTabStatus };

export interface AgentTabState {
  agentId: string;
  label: string;
  status: AgentTabStatus;
  role: string;
}

export interface AgentTabsProps {
  agents: Record<string, AgentTabState>;
  tabOrder: string[];          // ordered agent IDs for active tabs
  activeTab: string;
  onTabClick: (agentId: string) => void;
  onAbortAgent?: (agentId: string) => void;
  onRerunAgent?: (agentId: string) => void;
  isRunning?: boolean;         // when true, rerun buttons are hidden (avoid concurrent writes)
  skeletonTabs?: string[];     // labels for statements not in this run (greyed-out)
  // Phase 8: gate statement tabs so pre-run state doesn't flash all 5
  // skeletons. Pass the statements the user actually picked for this run;
  // anything not in the list (and not a SPECIAL_TAB_IDS member) is hidden.
  statementsInRun?: string[];
}

// Tabs in this set follow their own lifecycle (scout is spun up before the
// run starts; validator is added on run_complete) and are therefore exempt
// from the statementsInRun gate. Kept as a top-level constant so the rule
// is discoverable without reading the render body.
const SPECIAL_TAB_IDS = new Set(["validator", "scout"]);

// ---------------------------------------------------------------------------
// Status badge — small indicator showing agent state
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: AgentTabStatus }) {
  if (status === "complete") {
    return (
      <span data-status="complete" style={badgeStyles.complete} aria-label="Complete">
        <span style={badgeStyles.completeDot} />
      </span>
    );
  }
  if (status === "running") {
    return (
      <span data-status="running" style={badgeStyles.running} aria-label="Running">
        <span style={badgeStyles.runningDot} />
      </span>
    );
  }
  if (status === "aborting") {
    return (
      <span data-status="aborting" style={badgeStyles.aborting} aria-label="Aborting">
        <span style={badgeStyles.abortingDot} />
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span data-status="failed" style={badgeStyles.failed} aria-label="Failed">
        <span style={badgeStyles.failedDot} />
      </span>
    );
  }
  if (status === "cancelled") {
    return (
      <span data-status="cancelled" style={badgeStyles.cancelled} aria-label="Cancelled">
        <span style={badgeStyles.cancelledDot} />
      </span>
    );
  }
  // pending
  return (
    <span data-status="pending" style={badgeStyles.pending} aria-label="Pending">
      <span style={badgeStyles.pendingDot} />
    </span>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AgentTabs({
  agents,
  tabOrder,
  activeTab,
  onTabClick,
  onAbortAgent,
  onRerunAgent,
  isRunning,
  skeletonTabs,
  statementsInRun,
}: AgentTabsProps) {
  // Phase 8 gating. The rule is:
  //   Render a tab if ANY of the following is true:
  //     1. The tab is a SPECIAL_TAB_IDS member (scout/validator) AND the
  //        agent exists in state — these follow their own lifecycle.
  //     2. `statementsInRun` was not passed at all (legacy callers / history
  //        detail views) — we treat that as "no gate, show everything".
  //     3. The agent's role is in statementsInRun — i.e. the user actually
  //        picked this statement for the current run.
  //
  // Ordering: statement tabs render first in their original tabOrder, then
  // scout (if present), then validator (if present) — so users always find
  // the aggregate Validator pane at the far right after a run completes.
  const gatedOrder = (() => {
    const statementIds: string[] = [];
    let scoutId: string | null = null;
    let validatorId: string | null = null;
    for (const id of tabOrder) {
      const agent = agents[id];
      if (!agent) continue;
      if (SPECIAL_TAB_IDS.has(id)) {
        if (id === "scout") scoutId = id;
        else if (id === "validator") validatorId = id;
        continue;
      }
      // Statement tabs — gated by statementsInRun unless prop is undefined.
      if (statementsInRun === undefined) {
        statementIds.push(id);
      } else if (statementsInRun.includes(agent.role)) {
        statementIds.push(id);
      }
    }
    return [
      ...statementIds,
      ...(scoutId ? [scoutId] : []),
      ...(validatorId ? [validatorId] : []),
    ];
  })();

  return (
    <div role="tablist" className="tab-bar-scroll" style={styles.tabBar}>
      {/* Active agent tabs (gated + reordered so special tabs sit last) */}
      {gatedOrder.map((agentId) => {
        const agent = agents[agentId];
        if (!agent) return null;
        const isActive = agentId === activeTab;
        const canAbort = agent.status === "running" && onAbortAgent;
        const canRerun = (agent.status === "failed" || agent.status === "cancelled") && onRerunAgent && !isRunning;
        return (
          <button
            key={agentId}
            role="tab"
            aria-selected={isActive}
            onClick={() => onTabClick(agentId)}
            style={{
              ...styles.tab,
              ...(isActive ? styles.tabActive : {}),
            }}
          >
            <StatusBadge status={agent.status} />
            <span>{agent.label}</span>
            {canAbort && (
              <span
                role="button"
                onClick={(e) => { e.stopPropagation(); onAbortAgent(agentId); }}
                style={styles.abortBtn}
                title={`Stop ${agent.label}`}
                aria-label={`Stop ${agent.label}`}
              >
                &#10005;
              </span>
            )}
            {canRerun && (
              <span
                role="button"
                onClick={(e) => { e.stopPropagation(); onRerunAgent!(agentId); }}
                style={styles.rerunBtn}
                title={`Rerun ${agent.label}`}
                aria-label={`Rerun ${agent.label}`}
              >
                &#8635;
              </span>
            )}
          </button>
        );
      })}

      {/* Skeleton tabs for unselected statements */}
      {skeletonTabs?.map((label) => (
        <button
          key={`skeleton-${label}`}
          role="tab"
          aria-selected={false}
          aria-disabled="true"
          disabled
          style={{ ...styles.tab, ...styles.tabSkeleton }}
        >
          <span data-status="pending" style={badgeStyles.skeleton} />
          <span>{label}</span>
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = {
  tabBar: {
    display: "flex",
    gap: pwc.space.xs,
    alignItems: "center",
    background: pwc.white,
    borderRadius: `${pwc.radius.md}px ${pwc.radius.md}px 0 0`,
    border: `1px solid ${pwc.grey200}`,
    padding: `${pwc.space.sm}px`,
    overflowX: "auto" as const,
  },
  abortBtn: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 16,
    height: 16,
    fontSize: 9,
    fontWeight: 700,
    color: pwc.grey500,
    background: "none",
    border: "none",
    borderRadius: "50%",
    cursor: "pointer",
    lineHeight: 1,
    marginLeft: 2,
  } as React.CSSProperties,
  rerunBtn: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 16,
    height: 16,
    fontSize: 12,
    color: pwc.orange500,
    background: "none",
    border: "none",
    borderRadius: "50%",
    cursor: "pointer",
    lineHeight: 1,
    marginLeft: 2,
  } as React.CSSProperties,
  tab: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 13,
    fontWeight: 500,
    color: pwc.grey700,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: 999,
    cursor: "pointer",
    whiteSpace: "nowrap" as const,
    transition: "color 0.15s, background 0.15s, border-color 0.15s, box-shadow 0.15s",
  },
  tabActive: {
    color: pwc.orange700,
    fontWeight: 600,
    background: pwc.orange50,
    borderColor: pwc.orange400,
  },
  tabSkeleton: {
    color: pwc.grey300,
    cursor: "default",
    opacity: 0.5,
    background: pwc.grey50,
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
    background: "#F0FDF4",
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
    background: "#FEF2F2",
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
    background: "#FEF2F2",
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
