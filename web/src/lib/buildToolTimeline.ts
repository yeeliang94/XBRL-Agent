// Pure reducer: SSEEvent[] → ToolTimelineEntry[]. Single merge
// implementation shared by history replay and (from Phase 5.4) the live
// appReducer path, so there's no drift between them.
import type {
  EventPhase,
  SSEEvent,
  ToolTimelineEntry,
} from "./types";

// Events whose shape AgentTimeline / buildToolTimeline / ToolCallCard
// actually consume. Anything outside this set (notably `scout_complete`,
// `scout_cancelled`, `complete`, `run_complete`) is routing metadata the
// parent UI handles directly and doesn't need in the timeline's event
// buffer. Extracted so handleAutoDetect stays short and so tests can
// assert the set without scraping the PreRunPanel switch.
const _TIMELINE_EVENT_TYPES: ReadonlySet<SSEEvent["event"]> = new Set<SSEEvent["event"]>([
  "status",
  "tool_call",
  "tool_result",
  "thinking_delta",
  "thinking_end",
  "text_delta",
  "error",
]);

/** True if the SSE event is one AgentTimeline renders; false for routing
 *  envelopes (scout_complete, run_complete, …). Safe to call on any event. */
export function isScoutTimelineEvent(evt: { event: string }): boolean {
  return _TIMELINE_EVENT_TYPES.has(evt.event as SSEEvent["event"]);
}

/**
 * Walk an event stream and pull out the Sheet-12 sub-agent batch metadata.
 *
 * The live reducer (agentReducer) populates `AgentState.subAgentBatchRanges`
 * from `status` events carrying phase="started" + batch_note_range +
 * batch_page_range + sub_agent_id. History replay doesn't use that reducer,
 * so this helper gives the RunDetailView the same list by inspecting the
 * persisted events directly. Keeping the derivation pure also means unit
 * tests can lock the live/replay equivalence contract in one place.
 *
 * Order = first-seen sub_agent_id (matches live behaviour). Retries that
 * re-emit the same sub_agent_id replace the prior entry so the ranges
 * reflect the final successful attempt.
 */
export interface DerivedSubAgentRange {
  subAgentId: string;
  notes: [number, number];
  pages: [number, number];
}

export function deriveSubAgentRangesFromEvents(
  events: SSEEvent[],
): DerivedSubAgentRange[] {
  const byId = new Map<string, DerivedSubAgentRange>();
  const order: string[] = [];
  for (const evt of events) {
    if (evt.event !== "status") continue;
    const d = evt.data as unknown as Record<string, unknown>;
    if (d.phase !== "started") continue;
    if (!Array.isArray(d.batch_note_range) || !Array.isArray(d.batch_page_range)) continue;
    const noteRange = d.batch_note_range as number[];
    const pageRange = d.batch_page_range as number[];
    if (noteRange.length !== 2 || pageRange.length !== 2) continue;
    const subId = typeof d.sub_agent_id === "string" ? d.sub_agent_id : "unknown";
    if (!byId.has(subId)) order.push(subId);
    byId.set(subId, {
      subAgentId: subId,
      notes: [noteRange[0], noteRange[1]],
      pages: [pageRange[0], pageRange[1]],
    });
  }
  return order.map((id) => byId.get(id)!).filter(Boolean);
}

/**
 * Filter an SSE event stream down to events originating from a single
 * Sheet-12 sub-agent (or the coordinator itself when sub_agent_id === null).
 *
 * Sheet 12 fans out to N sub-agents but emits every event under the parent
 * `agent_id="notes:LIST_OF_NOTES"`, with sub-agent provenance carried as:
 *   - `data.sub_agent_id` on all sub-agent events, and
 *   - `tool_call_id` prefixed with `"<sub_agent_id>:"` on tool_call /
 *     tool_result events (namespaced so parallel sub-agents never collide
 *     in the frontend timeline Map — see listofnotes_subcoordinator._emit).
 *
 * When `subAgentId === null` the helper is the identity (all events). When
 * set to a specific sub id, only events that carry that id in either the
 * payload field or the namespaced tool_call_id survive. Coordinator-level
 * events (no sub_agent_id, no namespaced id) are excluded from sub-id
 * views — the "All" bucket is how the operator sees those.
 */
export function filterEventsBySubAgent(
  events: SSEEvent[],
  subAgentId: string | null,
): SSEEvent[] {
  if (subAgentId === null) return events;
  return events.filter((evt) => {
    const d = evt.data as unknown as Record<string, unknown>;
    if (typeof d?.sub_agent_id === "string" && d.sub_agent_id === subAgentId) {
      return true;
    }
    // tool_call / tool_result may have been re-serialised without the
    // sub_agent_id routing field (e.g. legacy persisted runs); fall back
    // to the namespaced tool_call_id prefix which is always present.
    const tcid = d?.tool_call_id;
    if (typeof tcid === "string" && tcid.startsWith(`${subAgentId}:`)) {
      return true;
    }
    return false;
  });
}

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
      const data = evt.data;
      if (data && typeof data.phase === "string") {
        currentPhase = data.phase;
      }
      continue;
    }

    if (evt.event === "tool_call") {
      const data = evt.data;
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
      const data = evt.data;
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
