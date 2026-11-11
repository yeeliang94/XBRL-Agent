import type { NotesSubAgentRange } from "../components/NotesSubTabBar";
import type { SSEEvent } from "./types";

// AI review on a clean run makes a short first check, then hands any grounded
// anomaly to a focused investigation. Both passes are one agent working in
// turn, recorded on one stream; the handoff status marks where the second
// pass starts so the activity view can show each pass on its own.

export const REVIEWER_FIRST_PASS = "review:first";
export const REVIEWER_FOCUSED_PASS = "review:focused";

function phaseOf(event: SSEEvent): unknown {
  return event.event === "status" ? (event.data as unknown as Record<string, unknown>).phase : undefined;
}

/** Where the latest review attempt starts and where its focused
 *  investigation starts, or null when that attempt ran as a single pass.
 *  A retry on the live page appends a new attempt to the same record, so
 *  only the latest attempt is split. */
function passBounds(events: SSEEvent[]): { start: number; focused: number } | null {
  let start = 0;
  events.forEach((event, index) => {
    const phase = phaseOf(event);
    if (phase === "triage_started" || phase === "started") start = index;
  });
  const latest = events.slice(start);
  const handoff = latest.findIndex((event) => phaseOf(event) === "triage_handoff");
  if (handoff >= 0) return { start, focused: start + handoff + 1 };
  const started = latest.findIndex((event) => phaseOf(event) === "investigation_started");
  return started >= 0 ? { start, focused: start + started } : null;
}

export function reviewerPassRanges(events: SSEEvent[]): NotesSubAgentRange[] {
  if (passBounds(events) === null) return [];
  return [
    { subAgentId: REVIEWER_FIRST_PASS, notes: null, pages: null, label: "First check" },
    { subAgentId: REVIEWER_FOCUSED_PASS, notes: null, pages: null, label: "Focused investigation" },
  ];
}

export function filterReviewerPass(events: SSEEvent[], passId: string | null): SSEEvent[] {
  const bounds = passBounds(events);
  if (bounds === null) return events;
  if (passId === REVIEWER_FIRST_PASS) return events.slice(bounds.start, bounds.focused);
  if (passId === REVIEWER_FOCUSED_PASS) return events.slice(bounds.focused);
  return events;
}
