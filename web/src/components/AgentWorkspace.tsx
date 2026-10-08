import type { ReactNode } from "react";
import { AgentTabs, type AgentTabsProps } from "./AgentTabs";
import { activityWorkspaceStyle } from "../lib/uiStyles";

/** One grouped activity composition for live streams and saved run records. */
export function AgentWorkspace({ children, ...navigation }: AgentTabsProps & { children: ReactNode }) {
  return (
    <div className="multi-agent-workspace" style={activityWorkspaceStyle}>
      <AgentTabs {...navigation} />
      {children}
    </div>
  );
}
