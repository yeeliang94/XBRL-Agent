import type { AgentTabStatus, SSEEvent } from "./types";
import { notesSheetDisplayName } from "./sheetLabels";

/** One notes-formatter sub-agent: a whole notes sheet or one group of its tables. */
export interface FormatterSubAgent {
  subAgentId: string;
  label: string;
  status: AgentTabStatus;
}

/** Sub-agent ids equal the formatter's run_agents variant: the sheet name, or
 *  `notes_format_<sheet>_part<n>` when a large sheet is split into groups. */
export function formatterSubAgentLabel(subAgentId: string): string {
  const part = /^notes_format_(.+)_part(\d+)$/.exec(subAgentId);
  const sheet = part?.[1] ?? subAgentId.replace(/^notes_format_/, "");
  const name = notesSheetDisplayName(sheet);
  return part ? `${name} · part ${part[2]}` : name;
}

const PHASE_STATUS: Record<string, AgentTabStatus> = {
  started: "running",
  working: "running",
  completed: "complete",
  failed: "failed",
  cancelled: "cancelled",
};

/** Formatter sub-agents in first-seen order, each with its latest status. */
export function deriveFormatterSubAgents(events: SSEEvent[]): FormatterSubAgent[] {
  const byId = new Map<string, FormatterSubAgent>();
  for (const event of events) {
    const data = event.data as unknown as Record<string, unknown>;
    const subAgentId = typeof data.sub_agent_id === "string" ? data.sub_agent_id : "";
    if (!subAgentId) continue;
    const entry = byId.get(subAgentId)
      ?? { subAgentId, label: formatterSubAgentLabel(subAgentId), status: "running" as AgentTabStatus };
    if (event.event === "status" && typeof data.phase === "string" && data.phase in PHASE_STATUS) {
      entry.status = PHASE_STATUS[data.phase];
    }
    byId.set(subAgentId, entry);
  }
  return [...byId.values()];
}

/** The combined view names each sub-agent on its own status lines, so
 *  identical steps from parallel sub-agents stay distinguishable. */
export function labelFormatterEvents(events: SSEEvent[]): SSEEvent[] {
  return events.map((event) => {
    const data = event.data as unknown as Record<string, unknown>;
    if (event.event !== "status" || typeof data.sub_agent_id !== "string" || typeof data.message !== "string") {
      return event;
    }
    return {
      ...event,
      data: { ...event.data, message: `${formatterSubAgentLabel(data.sub_agent_id)}: ${data.message}` },
    } as SSEEvent;
  });
}
