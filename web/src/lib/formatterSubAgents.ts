import type { AgentTabStatus, RunAgentJson, SSEEvent } from "./types";
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

/** The notes sheet a sub-agent formats. */
export function formatterSubAgentSheet(subAgentId: string): string {
  const part = /^notes_format_(.+)_part\d+$/.exec(subAgentId);
  return part?.[1] ?? subAgentId.replace(/^notes_format_/, "");
}

const PHASE_STATUS: Record<string, AgentTabStatus> = {
  // Waiting for one of the shared model slots (notes/auto_format.py).
  queued: "pending",
  started: "running",
  working: "running",
  completed: "complete",
  failed: "failed",
  cancelled: "cancelled",
};

/** Durable call rows are the roster, even before a part has saved any steps.
 * Older completed calls without part metadata retain their legacy display. */
export function formatterEventsFromCalls(calls: RunAgentJson[]): SSEEvent[] {
  const hasRoster = calls.some((call) => call.status === "running"
    || call.events.some((event) => "sub_agent_id" in event.data));
  if (!hasRoster) return calls.flatMap((call) => call.events);
  return calls.flatMap((call) => {
    const sub_agent_id = call.variant ?? `call:${call.id}`;
    const phase = ["completed", "succeeded"].includes(call.status) ? "completed"
      : ["cancelled", "aborted"].includes(call.status) ? "cancelled"
      : call.status === "failed" ? "failed" : "started";
    const startTime = (Date.parse(call.started_at ?? "") || 0) / 1000;
    const start: SSEEvent = { event: "status", timestamp: startTime,
      data: { sub_agent_id, phase: "started", message: "Formatting started" } };
    const events = call.events.map((event) => ({ ...event,
      data: { ...event.data, sub_agent_id } }) as SSEEvent);
    // A terminal audit row remains authoritative when an event write failed.
    const lastStatus = [...events].reverse().find((event) => event.event === "status");
    const terminal: SSEEvent[] = phase !== "started" && lastStatus?.data.phase !== phase
      ? [{ event: "status", timestamp: Math.max((Date.parse(call.ended_at ?? "") || 0) / 1000,
        startTime, ...events.map((event) => event.timestamp ?? 0)),
        data: { sub_agent_id, phase, message: phase === "completed" ? "Formatting finished"
          : phase === "cancelled" ? "Formatting stopped" : "Formatting did not finish. Review it from Notes.",
          detail: call.error_message ?? "" } }] : [];
    return [...(events.some((event) => event.event === "status") ? [] : [start]), ...events, ...terminal];
  }).sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
}

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

/** One notes sheet and the parts it was split into. */
export interface FormatterSheet {
  sheet: string;
  label: string;
  subAgentIds: string[];
  status: AgentTabStatus;
  partsDone: number;
  partsTotal: number;
  /** Plain sentence for each failed part, in part order. */
  failures: string[];
}

/** A sheet is waiting until any part starts, working while any part is
 *  unfinished, and failed when a finished part failed. */
export function deriveFormatterSheets(events: SSEEvent[]): FormatterSheet[] {
  const failures = new Map<string, string>();
  for (const event of events) {
    const data = event.data as unknown as Record<string, unknown>;
    if (event.event === "status" && data.phase === "failed" && typeof data.sub_agent_id === "string") {
      failures.set(data.sub_agent_id, typeof data.message === "string" ? data.message : "");
    }
  }
  const bySheet = new Map<string, FormatterSubAgent[]>();
  for (const sub of deriveFormatterSubAgents(events)) {
    const sheet = formatterSubAgentSheet(sub.subAgentId);
    bySheet.set(sheet, [...(bySheet.get(sheet) ?? []), sub]);
  }
  return [...bySheet].map(([sheet, parts]) => {
    const statuses = parts.map((part) => part.status);
    const unfinished = statuses.some((status) => status === "running" || status === "pending");
    const status: AgentTabStatus = statuses.every((s) => s === "pending") ? "pending"
      : unfinished ? "running"
      : statuses.includes("failed") ? "failed"
      : statuses.includes("cancelled") ? "cancelled"
      : "complete";
    return {
      sheet,
      label: notesSheetDisplayName(sheet),
      subAgentIds: parts.map((part) => part.subAgentId),
      status,
      partsDone: statuses.filter((s) => ["complete", "failed", "cancelled"].includes(s)).length,
      partsTotal: parts.length,
      failures: parts.filter((part) => part.status === "failed")
        .map((part) => failures.get(part.subAgentId) ?? "").filter(Boolean),
    };
  });
}

/** The one-line progress shown under "Notes formatting" in the workstream list. */
export function formatterProgressSummary(sheets: FormatterSheet[]): string | null {
  if (sheets.length === 0) return null;
  const partsTotal = sheets.reduce((sum, sheet) => sum + sheet.partsTotal, 0);
  const partsDone = sheets.reduce((sum, sheet) => sum + sheet.partsDone, 0);
  if (sheets.some((sheet) => sheet.status === "running" || sheet.status === "pending")) {
    return `${partsDone} of ${partsTotal} parts done`;
  }
  // The row's status label already says Failed; the panel names the sheet.
  const formatted = sheets.filter((sheet) => sheet.status === "complete").length;
  return `${formatted} of ${sheets.length} sheets formatted`;
}

/** How many parts are working right now, for the list's second line. */
export function formatterRunningParts(events: SSEEvent[]): number {
  return deriveFormatterSubAgents(events).filter((sub) => sub.status === "running").length;
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
