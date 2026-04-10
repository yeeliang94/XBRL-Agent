import { describe, test, expect } from "vitest";
import { appReducer, agentReducer, initialState } from "../App";
import type { SSEEvent } from "../lib/types";
import { createAgentState } from "../lib/types";

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

  // --- Phase 10: Per-agent event routing ---

  test("Events with agent_id route to per-agent state", () => {
    let state = runningState();

    // Status event with agent_id creates agent slot and routes
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "status",
        data: { phase: "reading_template", message: "Start", agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 1,
      } as SSEEvent,
    });

    expect(state.agents).toHaveProperty("sofp_0");
    expect(state.agents.sofp_0.status).toBe("running");
    expect(state.agents.sofp_0.currentPhase).toBe("reading_template");
    expect(state.agentTabOrder).toContain("sofp_0");
    // First tab auto-selected
    expect(state.activeTab).toBe("sofp_0");
  });

  test("Events from multiple agents populate separate state slices", () => {
    let state = runningState();

    // SOFP agent event
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "thinking_delta",
        data: { content: "Analyzing SOFP", thinking_id: "t1", agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 1,
      } as SSEEvent,
    });

    // SOPL agent event
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "thinking_delta",
        data: { content: "Analyzing SOPL", thinking_id: "t2", agent_id: "sopl_0", agent_role: "SOPL" },
        timestamp: 2,
      } as SSEEvent,
    });

    expect(state.agents.sofp_0.thinkingBuffer).toBe("Analyzing SOFP");
    expect(state.agents.sopl_0.thinkingBuffer).toBe("Analyzing SOPL");
    expect(state.agentTabOrder).toEqual(["sofp_0", "sopl_0"]);
  });

  test("Per-agent complete event marks agent as complete/failed", () => {
    let state = runningState();

    // Create agent
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "status",
        data: { phase: "reading_template", message: "Start", agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 1,
      } as SSEEvent,
    });

    // Agent completes successfully
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "complete",
        data: { success: true, agent_id: "sofp_0", agent_role: "SOFP", workbook_path: "/out/sofp.xlsx", error: null },
        timestamp: 2,
      } as SSEEvent,
    });

    expect(state.agents.sofp_0.status).toBe("complete");
    expect(state.agents.sofp_0.workbookPath).toBe("/out/sofp.xlsx");
    // Run should NOT be marked complete (that's run_complete's job)
    expect(state.isComplete).toBe(false);
  });

  test("run_complete stores cross-checks and creates validator tab", () => {
    let state = runningState();

    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: true,
          merged_workbook: "/out/filled.xlsx",
          merge_errors: [],
          cross_checks: [
            { name: "sofp_balance", status: "passed", expected: 100, actual: 100, diff: 0, tolerance: 1, message: "OK" },
            { name: "sopl_to_socie_profit", status: "pending", expected: null, actual: null, diff: null, tolerance: 1, message: "SOPL not run" },
          ],
          statements_completed: ["SOFP"],
          statements_failed: [],
        },
        timestamp: 3,
      } as SSEEvent,
    });

    expect(state.isComplete).toBe(true);
    expect(state.crossChecks).toHaveLength(2);
    expect(state.crossChecks[0].name).toBe("sofp_balance");
    expect(state.crossChecks[0].status).toBe("passed");
    expect(state.agents).toHaveProperty("validator");
    expect(state.agentTabOrder).toContain("validator");
  });

  test("SET_ACTIVE_TAB switches the active tab", () => {
    let state = runningState();

    // Create two agents
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "status",
        data: { phase: "reading_template", message: "Start", agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 1,
      } as SSEEvent,
    });
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "status",
        data: { phase: "reading_template", message: "Start", agent_id: "sopl_0", agent_role: "SOPL" },
        timestamp: 2,
      } as SSEEvent,
    });

    expect(state.activeTab).toBe("sofp_0"); // first tab auto-selected

    state = appReducer(state, { type: "SET_ACTIVE_TAB", payload: "sopl_0" });
    expect(state.activeTab).toBe("sopl_0");
  });

  test("Global tokens aggregate across multiple agents", () => {
    let state = runningState();

    // Agent SOFP emits token update
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "token_update",
        data: {
          prompt_tokens: 1000,
          completion_tokens: 200,
          thinking_tokens: 0,
          cumulative: 1200,
          cost_estimate: 0.001,
          agent_id: "sofp_0",
          agent_role: "SOFP",
        },
        timestamp: 1,
      } as SSEEvent,
    });

    // Agent SOPL emits token update
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "token_update",
        data: {
          prompt_tokens: 800,
          completion_tokens: 150,
          thinking_tokens: 0,
          cumulative: 950,
          cost_estimate: 0.0008,
          agent_id: "sopl_0",
          agent_role: "SOPL",
        },
        timestamp: 2,
      } as SSEEvent,
    });

    // Global tokens should be the SUM of both agents, not just the last event
    expect(state.tokens).not.toBeNull();
    expect(state.tokens!.prompt_tokens).toBe(1800);      // 1000 + 800
    expect(state.tokens!.completion_tokens).toBe(350);    // 200 + 150
    expect(state.tokens!.cumulative).toBe(2150);          // 1200 + 950
    expect(state.tokens!.cost_estimate).toBeCloseTo(0.0018); // 0.001 + 0.0008

    // Per-agent tokens should still be individual
    expect(state.agents.sofp_0.tokens!.prompt_tokens).toBe(1000);
    expect(state.agents.sopl_0.tokens!.prompt_tokens).toBe(800);
  });

  test("Global tokens update correctly when one agent updates multiple times", () => {
    let state = runningState();

    // SOFP first update
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "token_update",
        data: {
          prompt_tokens: 500, completion_tokens: 100, thinking_tokens: 0,
          cumulative: 600, cost_estimate: 0.0005, agent_id: "sofp_0", agent_role: "SOFP",
        },
        timestamp: 1,
      } as SSEEvent,
    });

    // SOPL update
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "token_update",
        data: {
          prompt_tokens: 300, completion_tokens: 50, thinking_tokens: 0,
          cumulative: 350, cost_estimate: 0.0003, agent_id: "sopl_0", agent_role: "SOPL",
        },
        timestamp: 2,
      } as SSEEvent,
    });

    // SOFP second update (cumulative for that agent increases)
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "token_update",
        data: {
          prompt_tokens: 1000, completion_tokens: 200, thinking_tokens: 0,
          cumulative: 1200, cost_estimate: 0.001, agent_id: "sofp_0", agent_role: "SOFP",
        },
        timestamp: 3,
      } as SSEEvent,
    });

    // Global = SOFP(1000+200) + SOPL(300+50) = 1550
    expect(state.tokens!.prompt_tokens).toBe(1300);    // 1000 + 300
    expect(state.tokens!.completion_tokens).toBe(250);  // 200 + 50
    expect(state.tokens!.cumulative).toBe(1550);        // 1200 + 350
  });

  test("RUN_STARTED with statements records statementsInRun", () => {
    const withSession = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "test.pdf" },
    });
    const state = appReducer(withSession, {
      type: "RUN_STARTED",
      payload: { statements: ["SOFP", "SOPL"] },
    });
    expect(state.statementsInRun).toEqual(["SOFP", "SOPL"]);
  });

  // --- Phase: Text segmentation across model turns ---

  test("text_delta events from separate model turns produce separate textSegments", () => {
    let state = runningState();

    // Turn 1: agent says something, then calls a tool
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "text_delta",
        data: { content: "Let me read the template.", agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 1,
      } as SSEEvent,
    });

    // Tool call flushes turn-1 text into a segment
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "tool_call",
        data: { tool_name: "read_template", tool_call_id: "tc_1", args: {}, agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 2,
      } as SSEEvent,
    });

    // Tool result
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "tool_result",
        data: { tool_name: "read_template", tool_call_id: "tc_1", result_summary: "ok", duration_ms: 100, agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 3,
      } as SSEEvent,
    });

    // Turn 2: agent says something else
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "text_delta",
        data: { content: "Extraction complete.", agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 4,
      } as SSEEvent,
    });

    const agent = state.agents.sofp_0;
    // Turn-1 text should be flushed into textSegments
    expect(agent.textSegments).toHaveLength(1);
    expect(agent.textSegments[0].content).toBe("Let me read the template.");
    // Turn-2 text is still in streamingText (in-progress)
    expect(agent.streamingText).toBe("Extraction complete.");
  });

  test("flushed text segment timestamp is strictly before the tool startTime", () => {
    let state = runningState();

    // Text then tool call
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "text_delta",
        data: { content: "Analyzing...", agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 1,
      } as SSEEvent,
    });
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "tool_call",
        data: { tool_name: "read_template", tool_call_id: "tc_order", args: {}, agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 2,
      } as SSEEvent,
    });

    const agent = state.agents.sofp_0;
    // Segment must sort before the tool card — segment.timestamp < tool.startTime
    expect(agent.textSegments[0].timestamp).toBeLessThan(agent.toolTimeline[0].startTime);
  });

  test("agent complete event flushes remaining streamingText into textSegments", () => {
    let state = runningState();

    // Text from final turn
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "text_delta",
        data: { content: "Final summary.", agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 1,
      } as SSEEvent,
    });

    // Agent completes — should flush streamingText into textSegments
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "complete",
        data: { success: true, agent_id: "sofp_0", agent_role: "SOFP", workbook_path: "/out.xlsx", error: null },
        timestamp: 2,
      } as SSEEvent,
    });

    const agent = state.agents.sofp_0;
    expect(agent.textSegments).toHaveLength(1);
    expect(agent.textSegments[0].content).toBe("Final summary.");
    expect(agent.streamingText).toBe("");
  });
});

// --- agentReducer unit tests ---

describe("agentReducer", () => {
  test("status event sets currentPhase and marks running", () => {
    const agent = createAgentState("sofp_0", "SOFP", "SOFP");
    const result = agentReducer(agent, {
      event: "status",
      data: { phase: "viewing_pdf", message: "Viewing" },
      timestamp: 1,
    } as SSEEvent);
    expect(result.currentPhase).toBe("viewing_pdf");
    expect(result.status).toBe("running");
  });

  test("thinking_delta accumulates in agent buffer", () => {
    let agent = createAgentState("sofp_0", "SOFP", "SOFP");
    agent = agentReducer(agent, {
      event: "thinking_delta",
      data: { content: "Part 1 ", thinking_id: "t1" },
      timestamp: 1,
    } as SSEEvent);
    agent = agentReducer(agent, {
      event: "thinking_delta",
      data: { content: "Part 2", thinking_id: "t1" },
      timestamp: 2,
    } as SSEEvent);
    expect(agent.thinkingBuffer).toBe("Part 1 Part 2");
  });

  test("tool_call and tool_result pair correctly", () => {
    let agent = createAgentState("sofp_0", "SOFP", "SOFP");
    agent = agentReducer(agent, {
      event: "tool_call",
      data: { tool_name: "fill_workbook", tool_call_id: "tc_1", args: { row: 5 } },
      timestamp: 1,
    } as SSEEvent);
    expect(agent.toolTimeline).toHaveLength(1);
    expect(agent.toolTimeline[0].result_summary).toBeNull();

    agent = agentReducer(agent, {
      event: "tool_result",
      data: { tool_name: "fill_workbook", tool_call_id: "tc_1", result_summary: "Wrote row 5", duration_ms: 50 },
      timestamp: 2,
    } as SSEEvent);
    expect(agent.toolTimeline[0].result_summary).toBe("Wrote row 5");
    expect(agent.toolTimeline[0].duration_ms).toBe(50);
  });
});
