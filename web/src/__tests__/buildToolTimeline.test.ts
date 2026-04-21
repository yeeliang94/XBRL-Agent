// Phase 4 tests for buildToolTimeline — the pure function that turns an SSE
// event stream into a ToolTimelineEntry[]. Used by history replay (and from
// Phase 5.4, also by the live reducer) so there's one merge implementation.
import { describe, test, expect } from "vitest";
import {
  buildToolTimeline,
  filterEventsBySubAgent,
  isScoutTimelineEvent,
} from "../lib/buildToolTimeline";
import type { SSEEvent } from "../lib/types";

function call(id: string, name: string, args: Record<string, unknown> = {}, ts = 0): SSEEvent {
  return {
    event: "tool_call",
    data: { tool_call_id: id, tool_name: name, args },
    timestamp: ts,
  };
}

function result(id: string, name: string, summary: string, durationMs = 100, ts = 0): SSEEvent {
  return {
    event: "tool_result",
    data: { tool_call_id: id, tool_name: name, result_summary: summary, duration_ms: durationMs },
    timestamp: ts,
  };
}

describe("buildToolTimeline", () => {
  test("empty events → empty array", () => {
    expect(buildToolTimeline([])).toEqual([]);
  });

  test("one tool_call followed by matching tool_result → one fully populated entry", () => {
    const events: SSEEvent[] = [
      call("tc1", "read_template", { path: "/x.xlsx" }, 1),
      result("tc1", "read_template", "ok — 45 fields", 320, 2),
    ];
    const timeline = buildToolTimeline(events);
    expect(timeline).toHaveLength(1);
    const entry = timeline[0];
    expect(entry.tool_call_id).toBe("tc1");
    expect(entry.tool_name).toBe("read_template");
    expect(entry.args).toEqual({ path: "/x.xlsx" });
    expect(entry.result_summary).toBe("ok — 45 fields");
    expect(entry.duration_ms).toBe(320);
    expect(entry.endTime).not.toBeNull();
    expect(entry.startTime).toBeGreaterThan(0);
  });

  test("tool_call without matching result → one active entry", () => {
    const events: SSEEvent[] = [
      call("tc1", "view_pdf_pages", { pages: [5] }, 1),
    ];
    const timeline = buildToolTimeline(events);
    expect(timeline).toHaveLength(1);
    expect(timeline[0].result_summary).toBeNull();
    expect(timeline[0].endTime).toBeNull();
    expect(timeline[0].duration_ms).toBeNull();
  });

  test("multiple interleaved tool calls — each result attaches to its matching tool_call_id", () => {
    const events: SSEEvent[] = [
      call("a", "read_template", {}, 1),
      call("b", "view_pdf_pages", { pages: [1] }, 2),
      call("c", "verify_totals", {}, 3),
      // Results arrive out of order.
      result("b", "view_pdf_pages", "rendered 1 page", 80, 4),
      result("a", "read_template", "45 fields", 200, 5),
      // c is still pending.
    ];
    const timeline = buildToolTimeline(events);
    expect(timeline).toHaveLength(3);

    const byId = Object.fromEntries(timeline.map((e) => [e.tool_call_id, e]));
    expect(byId.a.result_summary).toBe("45 fields");
    expect(byId.a.duration_ms).toBe(200);
    expect(byId.b.result_summary).toBe("rendered 1 page");
    expect(byId.b.duration_ms).toBe(80);
    expect(byId.c.result_summary).toBeNull();
    expect(byId.c.endTime).toBeNull();
  });

  test("events without tool_call_id are ignored", () => {
    // Cast through unknown so we can mix partial-shape fixtures — the reducer
    // only looks at `event` and `tool_call_id`, so the extra fields don't
    // matter here.
    const events = [
      { event: "status", data: { phase: "starting" }, timestamp: 1 },
      call("a", "read_template", {}, 2),
      { event: "thinking_delta", data: { content: "..." }, timestamp: 3 },
      { event: "text_delta", data: { content: "..." }, timestamp: 4 },
      result("a", "read_template", "ok", 50, 5),
      { event: "complete", data: { success: true }, timestamp: 6 },
    ] as unknown as SSEEvent[];
    const timeline = buildToolTimeline(events);
    expect(timeline).toHaveLength(1);
    expect(timeline[0].tool_call_id).toBe("a");
  });

  test("preserves call order — first call first", () => {
    const events: SSEEvent[] = [
      call("z", "fill_workbook", {}, 1),
      call("a", "read_template", {}, 2),
      call("m", "view_pdf_pages", { pages: [1] }, 3),
    ];
    const timeline = buildToolTimeline(events);
    expect(timeline.map((e) => e.tool_call_id)).toEqual(["z", "a", "m"]);
  });

  test("phase is carried forward from the most recent status event", () => {
    // Peer-review fix: live reducer tags each tool_call with state.currentPhase;
    // replay must do the same so live and history render the same phase.
    const events = [
      { event: "status", data: { phase: "reading_template", message: "" }, timestamp: 1 },
      call("a", "read_template", {}, 2),
      { event: "status", data: { phase: "viewing_pdf", message: "" }, timestamp: 3 },
      call("b", "view_pdf_pages", { pages: [5] }, 4),
      result("a", "read_template", "ok", 100, 5),
      result("b", "view_pdf_pages", "ok", 200, 6),
    ] as unknown as SSEEvent[];
    const timeline = buildToolTimeline(events);
    expect(timeline.map((e) => [e.tool_call_id, e.phase])).toEqual([
      ["a", "reading_template"],
      ["b", "viewing_pdf"],
    ]);
  });

  test("a tool_result without a preceding tool_call is ignored (defensive)", () => {
    const events: SSEEvent[] = [
      result("ghost", "read_template", "orphan", 10, 1),
      call("a", "read_template", {}, 2),
      result("a", "read_template", "ok", 50, 3),
    ];
    const timeline = buildToolTimeline(events);
    expect(timeline).toHaveLength(1);
    expect(timeline[0].tool_call_id).toBe("a");
  });
});

// Helpers for the sub-agent filter tests — tack a sub_agent_id onto the
// payload and (for tool events) the namespaced tool_call_id that
// listofnotes_subcoordinator._emit uses on the wire. Cast through `unknown`
// so we can add the sub_agent_id field that rides on the event but isn't
// declared on each data interface (matches the pattern other tests use
// for extra routing fields).
function subCall(subId: string, callId: string, name: string, ts = 0): SSEEvent {
  return {
    event: "tool_call",
    data: {
      tool_call_id: `${subId}:${callId}`,
      tool_name: name,
      args: {},
      sub_agent_id: subId,
    },
    timestamp: ts,
  } as unknown as SSEEvent;
}

function subResult(subId: string, callId: string, name: string, ts = 0): SSEEvent {
  return {
    event: "tool_result",
    data: {
      tool_call_id: `${subId}:${callId}`,
      tool_name: name,
      result_summary: "ok",
      duration_ms: 50,
      sub_agent_id: subId,
    },
    timestamp: ts,
  } as unknown as SSEEvent;
}

function subStatus(subId: string, phase: string, ts = 0): SSEEvent {
  return {
    event: "status",
    data: {
      phase,
      message: "",
      sub_agent_id: subId,
    },
    timestamp: ts,
  } as unknown as SSEEvent;
}

describe("isScoutTimelineEvent", () => {
  // Pins the set of events that flow into the scout log. Without this,
  // a PreRunPanel refactor could accidentally include scout_complete
  // in the log (noisy) or drop tool_call (broken) without warning.
  test.each([
    ["status", true],
    ["tool_call", true],
    ["tool_result", true],
    ["thinking_delta", true],
    ["thinking_end", true],
    ["text_delta", true],
    ["error", true],
    ["scout_complete", false],
    ["scout_cancelled", false],
    ["complete", false],
    ["run_complete", false],
    ["token_update", false],
  ] as [string, boolean][])("%s -> %s", (eventName, expected) => {
    expect(isScoutTimelineEvent({ event: eventName })).toBe(expected);
  });
});

describe("filterEventsBySubAgent", () => {
  // Locks the sheet-12 sub-tab contract: events routed by sub_agent_id or
  // by namespaced tool_call_id survive the filter for their own sub, and
  // coordinator-level events (no sub_agent_id, no namespaced id) drop out
  // of any specific sub-view. The nested-Notes-12 UI depends on this exact
  // shape — see ExtractPage + RunDetailView sub-tab wiring.
  test("null subAgentId is identity (All tab)", () => {
    const events: SSEEvent[] = [
      subCall("sub0", "x1", "find_toc", 1),
      subCall("sub1", "y1", "view_pages", 2),
    ];
    expect(filterEventsBySubAgent(events, null)).toEqual(events);
  });

  test("sub id selects only events carrying sub_agent_id=<id>", () => {
    const events: SSEEvent[] = [
      subStatus("sub0", "started", 1),
      subCall("sub0", "x1", "find_toc", 2),
      subResult("sub0", "x1", "find_toc", 3),
      subCall("sub1", "y1", "view_pages", 4),
      subResult("sub1", "y1", "view_pages", 5),
    ];
    const sub0 = filterEventsBySubAgent(events, "sub0");
    expect(sub0).toHaveLength(3);
    expect(sub0.every((e) =>
      (e.data as unknown as Record<string, unknown>).sub_agent_id === "sub0"
    )).toBe(true);
  });

  test("namespaced tool_call_id prefix matches even when sub_agent_id is absent", () => {
    // Legacy / history replay path: persisted events may have been stripped
    // of their sub_agent_id routing field but keep the namespaced id.
    const events = [
      {
        event: "tool_call",
        data: { tool_call_id: "sub2:xyz", tool_name: "view_pages", args: {} },
        timestamp: 1,
      },
    ] as unknown as SSEEvent[];
    const filtered = filterEventsBySubAgent(events, "sub2");
    expect(filtered).toHaveLength(1);
  });

  test("coordinator-level events (no sub_agent_id, no namespaced id) excluded from sub views", () => {
    const events = [
      { event: "status", data: { phase: "starting", message: "" }, timestamp: 1 },
      subCall("sub0", "x1", "find_toc", 2),
    ] as unknown as SSEEvent[];
    expect(filterEventsBySubAgent(events, "sub0")).toHaveLength(1);
    // And the All view keeps everything.
    expect(filterEventsBySubAgent(events, null)).toHaveLength(2);
  });
});
