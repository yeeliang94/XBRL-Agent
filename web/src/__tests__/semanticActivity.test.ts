import { describe, expect, test } from "vitest";
import { semanticActivities } from "../lib/semanticActivity";
import type { SSEEvent, ToolTimelineEntry } from "../lib/types";

describe("semanticActivities", () => {
  test("uses friendly tool wording and omits technical durations", () => {
    const timeline: ToolTimelineEntry[] = [{
      tool_call_id: "call-1",
      tool_name: "view_pdf_pages",
      args: { pages: [3, 4] },
      result_summary: "pages rendered",
      duration_ms: 30,
      startTime: 1_700_000_000_000,
      endTime: 1_700_000_000_030,
      phase: "viewing_pdf",
    }];

    const activity = semanticActivities([], timeline)[0];
    expect(activity.title).toBe("Checking PDF pages");
    expect(activity.detail).toBe("pages 3 and 4");
    expect(JSON.stringify(activity)).not.toContain("30");
    expect(JSON.stringify(activity)).not.toContain("view_pdf_pages");
  });

  test("turns a raw calling status into plain language when no tool event exists", () => {
    const events = [{
      event: "status",
      data: { message: "Calling discover_notes..." },
      timestamp: 10,
    }] as SSEEvent[];

    expect(semanticActivities(events, [])[0].title).toBe("Discovering notes");
  });

  test("keeps unfamiliar reviewer tool identifiers out of the live roster", () => {
    const timeline: ToolTimelineEntry[] = [{
      tool_call_id: "review-1", tool_name: "internal_review_operation", args: {},
      result_summary: null, duration_ms: null, startTime: 1, endTime: null, phase: null,
    }];
    expect(semanticActivities([], timeline)[0].title).toBe("Reviewing document");
  });

  test("names the notes-review workstream in operator language", () => {
    const timeline: ToolTimelineEntry[] = [{
      tool_call_id: "review-2", tool_name: "relink_note_cell", args: {},
      result_summary: null, duration_ms: null, startTime: 2, endTime: null, phase: null,
    }];
    expect(semanticActivities([], timeline)[0].title).toBe("Checking note references");
  });
});
