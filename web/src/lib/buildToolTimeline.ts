// Pure reducer: SSEEvent[] → ToolTimelineEntry[]. Single merge
// implementation shared by history replay and (from Phase 5.4) the live
// appReducer path, so there's no drift between them.
import type {
  EventPhase,
  SSEEvent,
  StatusData,
  ToolCallData,
  ToolResultData,
  ToolTimelineEntry,
} from "./types";

/**
 * Rebuild a tool timeline from an SSE event list. Events without a
 * tool_call_id (status, thinking_delta, complete, ...) are ignored.
 * A tool_result without a preceding tool_call is dropped defensively —
 * the backend should never emit one, but if a replay buffer is corrupt
 * we'd rather show a partial timeline than throw.
 *
 * Single pass with a Map<tool_call_id, entry> for O(N) merge.
 */
export function buildToolTimeline(events: SSEEvent[]): ToolTimelineEntry[] {
  const byId = new Map<string, ToolTimelineEntry>();
  const order: string[] = [];
  // Track the most recent status phase so tool_call entries inherit the
  // same phase value the live reducer would assign. Without this, live and
  // replay timelines drift on the `phase` field even though the rest of
  // the entry shape matches.
  let currentPhase: EventPhase | null = null;

  for (const evt of events) {
    if (evt.event === "status") {
      const data = evt.data as StatusData;
      if (data && typeof data.phase === "string") {
        currentPhase = data.phase as EventPhase;
      }
      continue;
    }

    if (evt.event === "tool_call") {
      const data = evt.data as ToolCallData;
      if (!data || !data.tool_call_id) continue;
      // Use the event's timestamp (seconds) as startTime in ms, or Date.now()
      // as a safe fallback when the server didn't stamp one.
      const startTime = evt.timestamp ? evt.timestamp * 1000 : Date.now();
      const entry: ToolTimelineEntry = {
        tool_call_id: data.tool_call_id,
        tool_name: data.tool_name,
        args: data.args ?? {},
        result_summary: null,
        duration_ms: null,
        startTime,
        endTime: null,
        phase: currentPhase,
      };
      if (!byId.has(data.tool_call_id)) {
        order.push(data.tool_call_id);
      }
      byId.set(data.tool_call_id, entry);
      continue;
    }

    if (evt.event === "tool_result") {
      const data = evt.data as ToolResultData;
      if (!data || !data.tool_call_id) continue;
      const existing = byId.get(data.tool_call_id);
      // Drop orphan results: without a matching call we have no args, no
      // tool_name on the entry, and no start time to pair with.
      if (!existing) continue;
      const endTime = evt.timestamp ? evt.timestamp * 1000 : Date.now();
      byId.set(data.tool_call_id, {
        ...existing,
        result_summary: data.result_summary,
        duration_ms: data.duration_ms,
        endTime,
      });
      continue;
    }

    // Anything else (status, thinking_delta, text_delta, token_update,
    // error, complete) is not timeline material.
  }

  // Preserve call order — the Map iterator would also work since insertion
  // order is preserved, but the explicit `order` array documents intent.
  return order.map((id) => byId.get(id)!).filter(Boolean);
}
