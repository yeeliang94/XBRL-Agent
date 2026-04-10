import { describe, test, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AgentFeed } from "../components/AgentFeed";
import type { SSEEvent, ThinkingBlock, ToolTimelineEntry, TextSegment, EventPhase } from "../lib/types";

const sampleEvents: SSEEvent[] = [
  { event: "status", data: { phase: "reading_template" as EventPhase, message: "Starting..." }, timestamp: 1 },
  { event: "thinking_delta", data: { content: "hmm", thinking_id: "t1" }, timestamp: 2 },
  { event: "thinking_end", data: { thinking_id: "t1", summary: "hmm", full_length: 3 }, timestamp: 3 },
  { event: "tool_call", data: { tool_name: "read_template", tool_call_id: "tc_1", args: {} }, timestamp: 4 },
  { event: "tool_result", data: { tool_name: "read_template", tool_call_id: "tc_1", result_summary: "ok", duration_ms: 100 }, timestamp: 5 },
  { event: "text_delta", data: { content: "Done" }, timestamp: 6 },
  { event: "token_update", data: { prompt_tokens: 100, completion_tokens: 50, thinking_tokens: 0, cumulative: 150, cost_estimate: 0.001 }, timestamp: 7 },
];

const sampleBlocks: ThinkingBlock[] = [{
  id: "t1",
  content: "hmm",
  summary: "hmm",
  timestamp: Date.now(),
  phase: "reading_template",
  durationMs: 100,
}];

const sampleTimeline: ToolTimelineEntry[] = [{
  tool_call_id: "tc_1",
  tool_name: "read_template",
  args: {},
  result_summary: "ok",
  duration_ms: 100,
  startTime: Date.now(),
  endTime: Date.now() + 100,
  phase: "reading_template",
}];

describe("AgentFeed", () => {
  test("renders thinking blocks, tool cards, and text in chronological order", () => {
    render(
      <AgentFeed
        events={sampleEvents}
        thinkingBlocks={sampleBlocks}
        toolTimeline={sampleTimeline}
        streamingText="Done"
        textSegments={[]}
        thinkingBuffer=""
        activeThinkingId={null}
        isRunning={false}
        currentPhase={null}
      />,
    );
    // Should render a thinking block summary
    expect(screen.getByText(/hmm/)).toBeInTheDocument();
    // Should render a tool card
    expect(screen.getByText("Reading template")).toBeInTheDocument();
    // Should render streaming text
    expect(screen.getByText("Done")).toBeInTheDocument();
  });

  test("'Timeline' view is default, 'Raw Log' is alternate", () => {
    render(
      <AgentFeed
        events={sampleEvents}
        thinkingBlocks={sampleBlocks}
        toolTimeline={sampleTimeline}
        streamingText=""
        textSegments={[]}
        thinkingBuffer=""
        activeThinkingId={null}
        isRunning={false}
        currentPhase={null}
      />,
    );
    const timelineBtn = screen.getByRole("button", { name: /timeline/i });
    const rawLogBtn = screen.getByRole("button", { name: /raw log/i });
    expect(timelineBtn).toBeInTheDocument();
    expect(rawLogBtn).toBeInTheDocument();
  });

  test("toggle switches to Raw Log view showing flat event list", () => {
    render(
      <AgentFeed
        events={sampleEvents}
        thinkingBlocks={sampleBlocks}
        toolTimeline={sampleTimeline}
        streamingText=""
        textSegments={[]}
        thinkingBuffer=""
        activeThinkingId={null}
        isRunning={false}
        currentPhase={null}
      />,
    );
    const rawLogBtn = screen.getByRole("button", { name: /raw log/i });
    fireEvent.click(rawLogBtn);
    // Raw log should show event type badges
    expect(screen.getByText("status")).toBeInTheDocument();
    expect(screen.getByText("tool_call")).toBeInTheDocument();
  });

  test("filters out token_update events from timeline view", () => {
    render(
      <AgentFeed
        events={sampleEvents}
        thinkingBlocks={sampleBlocks}
        toolTimeline={sampleTimeline}
        streamingText=""
        textSegments={[]}
        thinkingBuffer=""
        activeThinkingId={null}
        isRunning={false}
        currentPhase={null}
      />,
    );
    // token_update should not appear in timeline
    expect(screen.queryByText("token_update")).not.toBeInTheDocument();
  });

  test("shows phase markers as dividers with phase label", () => {
    render(
      <AgentFeed
        events={sampleEvents}
        thinkingBlocks={sampleBlocks}
        toolTimeline={sampleTimeline}
        streamingText=""
        textSegments={[]}
        thinkingBuffer=""
        activeThinkingId={null}
        isRunning={false}
        currentPhase={null}
      />,
    );
    // Phase label should appear as a divider
    expect(screen.getByText("reading_template")).toBeInTheDocument();
  });

  test("renders textSegments interleaved with tool cards in chronological order", () => {
    const now = Date.now();
    // Segment 1: text from turn 1 (before tool)
    const segments: TextSegment[] = [
      { content: "Let me read the template.", timestamp: now, phase: "reading_template" },
      { content: "Extraction complete.", timestamp: now + 300, phase: "filling_workbook" },
    ];
    // Tool card between the two segments
    const tools: ToolTimelineEntry[] = [{
      tool_call_id: "tc_1",
      tool_name: "read_template",
      args: {},
      result_summary: "ok",
      duration_ms: 100,
      startTime: now + 100,
      endTime: now + 200,
      phase: "reading_template",
    }];

    render(
      <AgentFeed
        events={[]}
        thinkingBlocks={[]}
        toolTimeline={tools}
        streamingText=""
        textSegments={segments}
        thinkingBuffer=""
        activeThinkingId={null}
        isRunning={false}
        currentPhase={null}
      />,
    );

    // Both text segments should render
    expect(screen.getByText("Let me read the template.")).toBeInTheDocument();
    expect(screen.getByText("Extraction complete.")).toBeInTheDocument();
    // Tool card should render between them
    expect(screen.getByText("Reading template")).toBeInTheDocument();

    // Verify chronological order: segment1 → tool → segment2
    const container = screen.getByText("Let me read the template.").closest(".agent-scroll")
      || document.body;
    const allText = container.textContent || "";
    const idx1 = allText.indexOf("Let me read the template.");
    const idxTool = allText.indexOf("Reading template");
    const idx2 = allText.indexOf("Extraction complete.");
    expect(idx1).toBeLessThan(idxTool);
    expect(idxTool).toBeLessThan(idx2);
  });
});
