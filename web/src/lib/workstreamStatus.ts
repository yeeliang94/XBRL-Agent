import type { AgentTabStatus } from "./types";

export function workstreamStatusLabel(status: AgentTabStatus, role?: string): string {
  // Formatting can save useful output while leaving appearance unverified.
  if (status === "failed" && (role === "NOTES_FORMATTING" || role === "notes-formatting")) return "Needs review";
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
