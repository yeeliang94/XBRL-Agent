import { describe, test, expect } from "vitest";
import { appReducer, initialState } from "../App";
import type { SSEEvent } from "../lib/types";

// Helper: get to a "running" state
function runningState() {
  return appReducer(
    appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "test.pdf" },
    }),
    { type: "RUN_STARTED" },
  );
}

describe("appReducer", () => {
  test("UPLOADED sets sessionId and filename", () => {
    const state = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "test.pdf" },
    });
    expect(state.sessionId).toBe("abc");
    expect(state.filename).toBe("test.pdf");
    expect(state.isRunning).toBe(false);
  });

  test("RUN_STARTED sets isRunning and runStartTime", () => {
    const before = Date.now();
    const withSession = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "test.pdf" },
    });
    const state = appReducer(withSession, { type: "RUN_STARTED" });
    expect(state.isRunning).toBe(true);
    expect(state.runStartTime).toBeGreaterThanOrEqual(before);
    expect(state.runStartTime).toBeLessThanOrEqual(Date.now());
  });

  test("EVENT accumulates events and updates derived state", () => {
    const running = runningState();

    const withStatus = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "status",
        data: { phase: "reading_template", message: "Start" },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(withStatus.currentPhase).toBe("reading_template");
    expect(withStatus.events).toHaveLength(1);

    const withTokens = appReducer(withStatus, {
      type: "EVENT",
      payload: {
        event: "token_update",
        data: {
          prompt_tokens: 100,
          completion_tokens: 50,
          thinking_tokens: 0,
          cumulative: 150,
          cost_estimate: 0.001,
        },
        timestamp: 2,
      } as SSEEvent,
    });
    expect(withTokens.tokens?.cumulative).toBe(150);

    const withComplete = appReducer(withTokens, {
      type: "EVENT",
      payload: {
        event: "complete",
        data: {
          success: true,
          output_path: "",
          excel_path: "",
          trace_path: "",
          total_tokens: 5000,
          cost: 0.003,
        },
        timestamp: 3,
      } as SSEEvent,
    });
    expect(withComplete.isComplete).toBe(true);
    expect(withComplete.isRunning).toBe(false);
  });

  test("EVENT with error sets hasError and stops running", () => {
    const running = runningState();
    const state = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "error",
        data: { message: "API key invalid", traceback: "" },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(state.hasError).toBe(true);
    expect(state.isRunning).toBe(false);
  });

  test("RESET clears all state", () => {
    const state = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "test.pdf" },
    });
    const reset = appReducer(state, { type: "RESET" });
    expect(reset.sessionId).toBeNull();
    expect(reset.events).toHaveLength(0);
  });

  // --- P0: New streaming event types ---

  test("THINKING_DELTA event appends to thinkingBuffer", () => {
    const running = runningState();

    const s1 = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "thinking_delta",
        data: { content: "Let me analyze ", thinking_id: "think_1" },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(s1.thinkingBuffer).toBe("Let me analyze ");
    expect(s1.activeThinkingId).toBe("think_1");

    const s2 = appReducer(s1, {
      type: "EVENT",
      payload: {
        event: "thinking_delta",
        data: { content: "this PDF...", thinking_id: "think_1" },
        timestamp: 2,
      } as SSEEvent,
    });
    expect(s2.thinkingBuffer).toBe("Let me analyze this PDF...");
  });

  test("THINKING_END event finalizes thinkingBuffer and clears it", () => {
    let state = runningState();

    // Accumulate some thinking
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "thinking_delta",
        data: { content: "Reasoning about SOFP fields", thinking_id: "think_1" },
        timestamp: 1,
      } as SSEEvent,
    });

    // End the thinking block
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "thinking_end",
        data: { thinking_id: "think_1", summary: "Reasoning about SOFP fields", full_length: 27 },
        timestamp: 2,
      } as SSEEvent,
    });

    expect(state.thinkingBuffer).toBe("");
    expect(state.activeThinkingId).toBeNull();
    expect(state.thinkingBlocks).toHaveLength(1);
    expect(state.thinkingBlocks[0].id).toBe("think_1");
    expect(state.thinkingBlocks[0].content).toBe("Reasoning about SOFP fields");
    expect(state.thinkingBlocks[0].summary).toBe("Reasoning about SOFP fields");
  });

  test("TOOL_CALL event adds entry to toolTimeline with start timestamp", () => {
    const running = runningState();

    const state = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "tool_call",
        data: {
          tool_name: "read_template",
          tool_call_id: "tc_1",
          args: { path: "template.xlsx" },
        },
        timestamp: 1000,
      } as SSEEvent,
    });

    expect(state.toolTimeline).toHaveLength(1);
    expect(state.toolTimeline[0].tool_call_id).toBe("tc_1");
    expect(state.toolTimeline[0].tool_name).toBe("read_template");
    expect(state.toolTimeline[0].args).toEqual({ path: "template.xlsx" });
    expect(state.toolTimeline[0].result_summary).toBeNull();
    expect(state.toolTimeline[0].duration_ms).toBeNull();
    expect(state.toolTimeline[0].startTime).toBeGreaterThan(0);
    expect(state.toolTimeline[0].endTime).toBeNull();
  });

  test("TOOL_RESULT event pairs with matching tool_call by tool_call_id", () => {
    let state = runningState();

    // tool_call first
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "tool_call",
        data: { tool_name: "read_template", tool_call_id: "tc_1", args: {} },
        timestamp: 1000,
      } as SSEEvent,
    });

    // tool_result pairs by tool_call_id
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "tool_result",
        data: {
          tool_name: "read_template",
          tool_call_id: "tc_1",
          result_summary: "Read 45 fields",
          duration_ms: 320,
        },
        timestamp: 1001,
      } as SSEEvent,
    });

    expect(state.toolTimeline).toHaveLength(1);
    expect(state.toolTimeline[0].result_summary).toBe("Read 45 fields");
    expect(state.toolTimeline[0].duration_ms).toBe(320);
    expect(state.toolTimeline[0].endTime).toBeGreaterThan(0);
  });

  test("TEXT_DELTA event appends to streamingText", () => {
    let state = runningState();

    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "text_delta",
        data: { content: "I found " },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(state.streamingText).toBe("I found ");

    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "text_delta",
        data: { content: "the SOFP data." },
        timestamp: 2,
      } as SSEEvent,
    });
    expect(state.streamingText).toBe("I found the SOFP data.");
  });

  test("Events accumulate in order for full audit trail", () => {
    let state = runningState();

    const events: SSEEvent[] = [
      { event: "status", data: { phase: "reading_template", message: "Start" }, timestamp: 1 } as SSEEvent,
      { event: "thinking_delta", data: { content: "hmm", thinking_id: "t1" }, timestamp: 2 } as SSEEvent,
      { event: "tool_call", data: { tool_name: "read_template", tool_call_id: "tc_1", args: {} }, timestamp: 3 } as SSEEvent,
      { event: "tool_result", data: { tool_name: "read_template", tool_call_id: "tc_1", result_summary: "ok", duration_ms: 100 }, timestamp: 4 } as SSEEvent,
      { event: "text_delta", data: { content: "Done" }, timestamp: 5 } as SSEEvent,
    ];

    for (const evt of events) {
      state = appReducer(state, { type: "EVENT", payload: evt });
    }

    expect(state.events).toHaveLength(5);
    expect(state.events.map((e) => e.event)).toEqual([
      "status",
      "thinking_delta",
      "tool_call",
      "tool_result",
      "text_delta",
    ]);
  });
});
