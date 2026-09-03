import { describe, expect, test } from "vitest";
import { buildActivitySentences } from "../lib/buildActivitySentences";
import type { ReasoningBlock, SSEEvent, ToolTimelineEntry } from "../lib/types";

describe("buildActivitySentences", () => {
  test("merges reasoning, statuses, and tools into newest-first sentences", () => {
    const events = [{
      event: "status",
      data: { phase: "viewing_pdf", message: "Opening source PDF" },
      timestamp: 1,
    }] as SSEEvent[];
    const tools: ToolTimelineEntry[] = [{
      tool_call_id: "toc",
      tool_name: "find_toc",
      args: {},
      result_summary: "Found page 3",
      duration_ms: 100,
      startTime: 2000,
      endTime: 2100,
      phase: "viewing_pdf",
    }];
    const reasoning: ReasoningBlock[] = [{
      thinking_id: "thought-1",
      content: "The contents page is page 3. I will inspect it next.",
      startedAt: 3000,
      endedAt: 3200,
      duration_ms: 200,
      isComplete: true,
    }];

    const sentences = buildActivitySentences(events, tools, reasoning);

    expect(sentences.map((item) => item.source)).toEqual([
      "reasoning",
      "reasoning",
      "tool",
      "status",
    ]);
    expect(sentences[0].text).toBe("I will inspect it next.");
    expect(sentences[1].text).toBe("The contents page is page 3.");
    expect(sentences[2].text).toMatch(/Locating table of contents/i);
    expect(sentences[3].text).toBe("Opening source PDF.");
  });

  test("keeps an unfinished reasoning sentence active without dropping text", () => {
    const reasoning: ReasoningBlock[] = [{
      thinking_id: "thought-1",
      content: "The statement appears to continue on the next page",
      startedAt: 1000,
      endedAt: null,
      duration_ms: null,
      isComplete: false,
    }];

    expect(buildActivitySentences([], [], reasoning)).toEqual([{
      id: "reasoning:thought-1:0",
      text: "The statement appears to continue on the next page",
      timestamp: 1000,
      source: "reasoning",
      active: true,
    }]);
  });

  test("keeps note references and decimals inside one sentence", () => {
    const reasoning: ReasoningBlock[] = [{
      thinking_id: "thought-1",
      content: "Note 2.14 covers deferred tax. The effective rate is 12.5%.",
      startedAt: 1000,
      endedAt: 1200,
      duration_ms: 200,
      isComplete: true,
    }];

    expect(buildActivitySentences([], [], reasoning).map((item) => item.text)).toEqual([
      "The effective rate is 12.5%.",
      "Note 2.14 covers deferred tax.",
    ]);
  });

  test("renders markdown-formatted reasoning as separate plain-text updates", () => {
    const reasoning: ReasoningBlock[] = [{
      thinking_id: "thought-1",
      content: "**Reviewing PPE amounts****Clarifying PPE mapping**",
      startedAt: 1000,
      endedAt: 1200,
      duration_ms: 200,
      isComplete: true,
    }];

    const sentences = buildActivitySentences([], [], reasoning);

    expect(sentences.map((item) => item.text)).toEqual([
      "Clarifying PPE mapping",
      "Reviewing PPE amounts",
    ]);
    expect(sentences.every((item) => !item.text.includes("*"))).toBe(true);
  });

  test("unwraps inline bold without splitting the surrounding sentence", () => {
    const reasoning: ReasoningBlock[] = [{
      thinking_id: "thought-1",
      content: "Reviewing **PPE amounts** against note 4.",
      startedAt: 1000,
      endedAt: 1200,
      duration_ms: 200,
      isComplete: true,
    }];

    expect(buildActivitySentences([], [], reasoning).map((item) => item.text)).toEqual([
      "Reviewing PPE amounts against note 4.",
    ]);
  });

  test("unwraps inline italics without exposing markdown asterisks", () => {
    const reasoning: ReasoningBlock[] = [{
      thinking_id: "thought-1",
      content: "Checking *depreciation* against the policy.",
      startedAt: 1000,
      endedAt: 1200,
      duration_ms: 200,
      isComplete: true,
    }];

    expect(buildActivitySentences([], [], reasoning).map((item) => item.text)).toEqual([
      "Checking depreciation against the policy.",
    ]);
  });

  test("surfaces terminal failure reasons and completion events", () => {
    const events = [
      {
        event: "error",
        data: { message: "Provider timed out while reading page 18" },
        timestamp: 1,
      },
      {
        event: "complete",
        data: { success: false, error: "Provider timed out while reading page 18" },
        timestamp: 2,
      },
    ] as SSEEvent[];

    const sentences = buildActivitySentences(events, [], []);

    expect(sentences[0]).toMatchObject({
      text: "Provider timed out while reading page 18.",
      active: false,
    });
    expect(sentences[1].text).toBe("Provider timed out while reading page 18.");
  });
});
