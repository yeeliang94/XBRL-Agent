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
}

// ---------------------------------------------------------------------------
// Status badge — small indicator showing agent state
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: AgentTabStatus }) {
  if (status === "complete") {
    return (
      <span data-status="complete" style={badgeStyles.complete} aria-label="Complete">
        &#10003;
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
        &#10005;
      </span>
    );
  }
  if (status === "cancelled") {
    return (
      <span data-status="cancelled" style={badgeStyles.cancelled} aria-label="Cancelled">
        &#8856;
      </span>
    );
  }
  // pending
  return (
    <span data-status="pending" style={badgeStyles.pending} aria-label="Pending">
      &#8226;
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
}: AgentTabsProps) {
  return (
    <div role="tablist" className="tab-bar-scroll" style={styles.tabBar}>
      {/* Active agent tabs */}
      {tabOrder.map((agentId) => {
        const agent = agents[agentId];
        if (!agent) return null;
        const isActive = agentId === activeTab;
        const canAbort = agent.status === "running" && onAbortAgent;
        const canRerun = (agent.status === "failed" || agent.status === "cancelled") && onRerunAgent && !isRunning;
        return (
          <div key={agentId} style={styles.tabWrapper}>
            <button
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
            </button>
            {/* Per-agent action buttons */}
            {canAbort && (
              <button
                onClick={(e) => { e.stopPropagation(); onAbortAgent(agentId); }}
                style={styles.abortBtn}
                title={`Stop ${agent.label}`}
                aria-label={`Stop ${agent.label}`}
              >
                &#10005;
              </button>
            )}
            {canRerun && (
              <button
                onClick={(e) => { e.stopPropagation(); onRerunAgent!(agentId); }}
                style={styles.rerunBtn}
                title={`Rerun ${agent.label}`}
                aria-label={`Rerun ${agent.label}`}
              >
                &#8635;
              </button>
            )}
          </div>
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
          <span data-status="pending" style={badgeStyles.skeleton}>&#8226;</span>
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
    borderBottom: `2px solid ${pwc.grey200}`,
    background: pwc.white,
    borderRadius: `${pwc.radius.md}px ${pwc.radius.md}px 0 0`,
    border: `1px solid ${pwc.grey200}`,
    borderBottomWidth: 2,
    padding: `0 ${pwc.space.sm}px`,
    overflowX: "auto" as const,
  },
  tabWrapper: {
    display: "flex",
    alignItems: "center",
    position: "relative" as const,
  },
  abortBtn: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 18,
    height: 18,
    fontSize: 10,
    fontWeight: 700,
    color: pwc.error,
    background: "none",
    border: `1px solid ${pwc.error}`,
    borderRadius: "50%",
    cursor: "pointer",
    marginLeft: -4,
    marginRight: 4,
    lineHeight: 1,
  } as React.CSSProperties,
  rerunBtn: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 18,
    height: 18,
    fontSize: 13,
    color: pwc.orange500,
    background: "none",
    border: `1px solid ${pwc.orange500}`,
    borderRadius: "50%",
    cursor: "pointer",
    marginLeft: -4,
    marginRight: 4,
    lineHeight: 1,
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
    background: "none",
    border: "none",
    borderBottom: "2px solid transparent",
    marginBottom: -2,
    cursor: "pointer",
    whiteSpace: "nowrap" as const,
    transition: "color 0.15s, border-color 0.15s",
  },
  tabActive: {
    color: pwc.orange500,
    fontWeight: 600,
    borderBottomColor: pwc.orange500,
  },
  tabSkeleton: {
    color: pwc.grey300,
    cursor: "default",
    opacity: 0.5,
  },
} as const;

const badgeStyles = {
  complete: {
    color: pwc.success,
    fontSize: 12,
    fontWeight: 700,
    lineHeight: 1,
  } as React.CSSProperties,
  running: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 10,
    height: 10,
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
    color: pwc.error,
    fontSize: 12,
    fontWeight: 700,
    lineHeight: 1,
  } as React.CSSProperties,
  cancelled: {
    color: pwc.grey500,
    fontSize: 12,
    fontWeight: 700,
    lineHeight: 1,
  } as React.CSSProperties,
  aborting: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 10,
    height: 10,
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
    color: pwc.grey300,
    fontSize: 14,
    lineHeight: 1,
  } as React.CSSProperties,
  skeleton: {
    color: pwc.grey300,
    fontSize: 14,
    lineHeight: 1,
  } as React.CSSProperties,
} as const;
