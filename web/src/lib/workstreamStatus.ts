import type { AgentTabStatus } from "./types";

export function workstreamStatusLabel(status: AgentTabStatus): string {
  switch (status) {
    case "running": return "Working";
    case "aborting": return "Stopping";
    case "complete": return "Complete";
    case "failed": return "Failed";
    case "cancelled": return "Stopped";
    case "skipped": return "Skipped";
    default: return "Waiting";
  }
}

export function isCompletedWorkstream(status: AgentTabStatus): boolean {
  return status === "complete";
}
