import { describe, expect, test } from "vitest";
import { buildActivitySentences } from "../lib/buildActivitySentences";
import type { SSEEvent, ToolTimelineEntry } from "../lib/types";

describe("buildActivitySentences", () => {
  test("keeps semantic milestones in newest-first order", () => {
    const events = [{ event: "status", data: { phase: "viewing_pdf", message: "Opening source PDF" }, timestamp: 1 }] as SSEEvent[];
    const tools: ToolTimelineEntry[] = [{
      tool_call_id: "toc", tool_name: "find_toc", args: {}, result_summary: "Found page 3",
      duration_ms: 100, startTime: 2000, endTime: 2100, phase: "viewing_pdf",
    }];
    const sentences = buildActivitySentences(events, tools);

    expect(sentences.map((item) => item.source)).toEqual(["tool", "status"]);
    expect(sentences[0].text).toBe("Locating the contents page.");
    expect(sentences[1].text).toBe("Opening source PDF.");
  });

  test("does not expose provider reasoning in the operator feed", () => {
    expect(buildActivitySentences([], [])).toEqual([]);
  });

  test("shows page, figure, note and checking activity in operator language", () => {
    const tools: ToolTimelineEntry[] = [
      { tool_call_id: "pages", tool_name: "view_pdf_pages", args: { pages: [3, 4] }, result_summary: null, duration_ms: null, startTime: 1000, endTime: null, phase: "viewing_pdf" },
      { tool_call_id: "text", tool_name: "read_page_text", args: { pages: [5] }, result_summary: null, duration_ms: null, startTime: 1500, endTime: null, phase: null },
      { tool_call_id: "figures", tool_name: "write_facts", args: {}, result_summary: "raw result", duration_ms: 1, startTime: 2000, endTime: 2001, phase: "filling_workbook" },
      { tool_call_id: "notes", tool_name: "write_notes", args: {}, result_summary: "raw result", duration_ms: 1, startTime: 3000, endTime: 3001, phase: "writing_notes" },
      { tool_call_id: "checks", tool_name: "verify_totals", args: {}, result_summary: "Balanced: True", duration_ms: 1, startTime: 4000, endTime: 4001, phase: "verifying" },
    ];

    expect(buildActivitySentences([], tools).map((item) => item.text)).toEqual([
      "Checking statement totals.",
      "Adding extracted notes.",
      "Adding extracted figures.",
      "Reading the text of source page 5.",
      "Reviewing source pages 3 and 4.",
    ]);
  });

  test("omits unknown raw tools and generated phase echoes", () => {
    const events = [{ event: "status", data: { phase: "viewing_pdf", message: "SOFP: Viewing Pdf" }, timestamp: 1 }] as SSEEvent[];
    const tools: ToolTimelineEntry[] = [{
      tool_call_id: "raw", tool_name: "internal_provider_operation", args: { secret: "detail" },
      result_summary: "raw result", duration_ms: 1, startTime: 2000, endTime: 2001, phase: "viewing_pdf",
    }];

    expect(buildActivitySentences(events, tools)).toEqual([]);
  });

  test("turns retries into concise milestones without exposing the provider error", () => {
    const events = [{
      event: "status",
      data: { phase: "reading_template", message: "SOFP: retrying (attempt 2) — last error: provider timeout" },
      timestamp: 1,
    }] as SSEEvent[];

    expect(buildActivitySentences(events, [])[0].text).toBe("Retrying SOFP.");
  });

  test("surfaces terminal failure reasons and completion events", () => {
    const events = [
      { event: "error", data: { message: "Provider timed out while reading page 18" }, timestamp: 1 },
      { event: "complete", data: { success: false, error: "Provider timed out while reading page 18" }, timestamp: 2 },
    ] as SSEEvent[];

    const sentences = buildActivitySentences(events, []);

    expect(sentences[0]).toMatchObject({ text: "Provider timed out while reading page 18.", active: false });
    expect(sentences[1].text).toBe("Provider timed out while reading page 18.");
  });

  test("keeps per-statement completion milestones", () => {
    const events = [{
      event: "status",
      data: { phase: "complete", message: "SOFP: Complete" },
      timestamp: 1,
    }] as SSEEvent[];

    expect(buildActivitySentences(events, [])[0].text).toBe("SOFP: Complete.");
  });

  test("falls back only for tools in the canonical operator vocabulary", () => {
    const tools: ToolTimelineEntry[] = [
      { tool_call_id: "save", tool_name: "save_result", args: {}, result_summary: "ok", duration_ms: 1, startTime: 2000, endTime: 2001, phase: "complete" },
      { tool_call_id: "raw", tool_name: "internal_provider_operation", args: {}, result_summary: "ok", duration_ms: 1, startTime: 1000, endTime: 1001, phase: "complete" },
    ];

    expect(buildActivitySentences([], tools).map((item) => item.text)).toEqual(["Saving result."]);
  });
});
