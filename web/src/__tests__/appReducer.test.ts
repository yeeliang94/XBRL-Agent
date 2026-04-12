import { describe, test, expect } from "vitest";
import { appReducer, agentReducer, initialState } from "../App";
import type { SSEEvent } from "../lib/types";
import { createAgentState } from "../lib/types";
import { buildToolTimeline } from "../lib/buildToolTimeline";

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

  // Phase 6: the chat feed's thinking buffers were deleted along with the
  // ChatFeed path. thinking_delta and thinking_end events still land in
  // state.events[] for the audit trail, but no streaming buffer or
  // per-block reasoning object is synthesised from them any more.
  test("thinking_delta and thinking_end events accumulate in events[] without streaming buffers", () => {
    let state = runningState();

    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "thinking_delta",
        data: { content: "Reasoning about SOFP fields", thinking_id: "think_1" },
        timestamp: 1,
      } as SSEEvent,
    });
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "thinking_end",
        data: { thinking_id: "think_1", summary: "Reasoning about SOFP fields", full_length: 27 },
        timestamp: 2,
      } as SSEEvent,
    });

    expect(state.events).toHaveLength(2);
    // None of the old streaming fields should be on the state object.
    const shape = state as unknown as Record<string, unknown>;
    expect(shape.thinkingBuffer).toBeUndefined();
    expect(shape.activeThinkingId).toBeUndefined();
    expect(shape.thinkingBlocks).toBeUndefined();
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

  // Phase 6: streamingText / textSegments were removed with the chat feed.
  // text_delta events still land in events[] for the audit trail, but no
  // streaming buffer is maintained.
  test("text_delta events accumulate in events[] without streamingText", () => {
    let state = runningState();

    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "text_delta",
        data: { content: "I found " },
        timestamp: 1,
      } as SSEEvent,
    });
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "text_delta",
        data: { content: "the SOFP data." },
        timestamp: 2,
      } as SSEEvent,
    });
    expect(state.events).toHaveLength(2);
    const shape = state as unknown as Record<string, unknown>;
    expect(shape.streamingText).toBeUndefined();
    expect(shape.textSegments).toBeUndefined();
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

    // Two agents each get a status event. Phase 6: streaming buffers are
    // gone, so we assert events[] and agentTabOrder routing instead.
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "status",
        data: { phase: "reading_template", message: "", agent_id: "sofp_0", agent_role: "SOFP" },
        timestamp: 1,
      } as SSEEvent,
    });
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "status",
        data: { phase: "reading_template", message: "", agent_id: "sopl_0", agent_role: "SOPL" },
        timestamp: 2,
      } as SSEEvent,
    });

    expect(state.agents.sofp_0.events).toHaveLength(1);
    expect(state.agents.sopl_0.events).toHaveLength(1);
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

  // Phase 6: the text-segmentation tests that lived here covered the
  // streamingText / textSegments flush logic, which was deleted when the
  // chat feed was replaced by the tool-call timeline.

  // --- Phase 4: Top-nav view switching ---

  test("view state defaults to 'extract'", () => {
    expect(initialState.view).toBe("extract");
  });

  test("SET_VIEW switches between 'extract' and 'history'", () => {
    const toHistory = appReducer(initialState, { type: "SET_VIEW", payload: "history" });
    expect(toHistory.view).toBe("history");

    const backToExtract = appReducer(toHistory, { type: "SET_VIEW", payload: "extract" });
    expect(backToExtract.view).toBe("extract");
  });

  test("SET_VIEW preserves other AppState fields (does not reset extract state)", () => {
    // Upload and start a run — then switch to history — extract state must persist
    const uploaded = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "test.pdf" },
    });
    const running = appReducer(uploaded, { type: "RUN_STARTED" });
    const switched = appReducer(running, { type: "SET_VIEW", payload: "history" });

    expect(switched.sessionId).toBe("abc");
    expect(switched.filename).toBe("test.pdf");
    expect(switched.isRunning).toBe(true);
    expect(switched.view).toBe("history");
  });

  // --- Phase 9: Success toast ---

  test("run_complete with success:true sets a success toast", () => {
    const running = runningState();
    const withToast = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: true,
          merged_workbook: "/tmp/filled.xlsx",
          merge_errors: [],
          cross_checks: [],
          statements_completed: ["SOFP"],
          statements_failed: [],
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(withToast.toast).not.toBeNull();
    expect(withToast.toast!.tone).toBe("success");
    expect(withToast.toast!.message).toMatch(/complete/i);
  });

  test("run_complete with success:false does NOT set a success toast", () => {
    const running = runningState();
    const withToast = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: false,
          merged_workbook: null,
          merge_errors: ["boom"],
          cross_checks: [],
          statements_completed: [],
          statements_failed: ["SOFP"],
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(withToast.toast).toBeNull();
  });

  // Peer-review [HIGH] regression: starting a rerun used to leave stale
  // completion state visible in the UI — the prior ResultsView, the old
  // cross-checks in the validator tab, and the Phase 9 "Run completed
  // successfully" toast all stuck around until the new run_complete
  // eventually arrived. RERUN_STARTED must wipe all of them so the
  // intermediate window reflects "run in progress" cleanly.
  test("RERUN_STARTED clears completion state, errors, and toast", () => {
    // Start from a state that looks like a completed run: upload, run,
    // and receive run_complete. Then inject an agent slot so rerun has
    // a target.
    let state = runningState();
    state = {
      ...state,
      agents: {
        sofp_0: {
          ...createAgentState("sofp_0", "SOFP", "SOFP"),
          status: "failed",
        },
      },
      agentTabOrder: ["sofp_0"],
      isComplete: true,
      complete: {
        success: true,
        output_path: "",
        excel_path: "/tmp/filled.xlsx",
        trace_path: "",
        total_tokens: 1000,
        cost: 0.01,
      },
      crossChecks: [
        {
          name: "sofp_balance",
          status: "passed",
          expected: 100,
          actual: 100,
          diff: 0,
          tolerance: 1,
          message: "ok",
        },
      ],
      hasError: true,
      error: { message: "stale error", traceback: "" },
      toast: { message: "Run completed successfully", tone: "success" },
    };

    const rerun = appReducer(state, {
      type: "RERUN_STARTED",
      payload: { agentId: "sofp_0" },
    });

    // Primary contract: isRunning flips back on so ResultsView can hide.
    expect(rerun.isRunning).toBe(true);
    // Stale completion state is wiped.
    expect(rerun.isComplete).toBe(false);
    expect(rerun.complete).toBeNull();
    expect(rerun.crossChecks).toEqual([]);
    // Stale error state is wiped — the user is trying the failure again.
    expect(rerun.hasError).toBe(false);
    expect(rerun.error).toBeNull();
    // Stale success toast is dismissed so it can't look like it's
    // congratulating the NEW rerun before it's even started.
    expect(rerun.toast).toBeNull();
    // Sanity: the target agent is reset to pending.
    expect(rerun.agents.sofp_0.status).toBe("pending");
  });

  test("DISMISS_TOAST clears the toast", () => {
    const running = runningState();
    const withToast = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: true,
          merged_workbook: "/tmp/filled.xlsx",
          merge_errors: [],
          cross_checks: [],
          statements_completed: ["SOFP"],
          statements_failed: [],
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(withToast.toast).not.toBeNull();

    const cleared = appReducer(withToast, { type: "DISMISS_TOAST" });
    expect(cleared.toast).toBeNull();
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

  // Phase 6: thinking_delta no longer populates a thinkingBuffer. The event
  // still lands in events[] so the audit trail is preserved and the Phase 6
  // negative-shape tests below cover the "no streaming fields" invariant.

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

  // Phase 6 — dead streaming state is gone from AgentState/AppState. The
  // reducers must still accept thinking/text events (they're still streamed
  // by some agents) but they append to `events` only and do not populate
  // any streaming-buffer fields.
  test("thinking_delta event is recorded in events[] but no streaming buffer is written", () => {
    let agent = createAgentState("sofp_0", "SOFP", "SOFP");
    agent = agentReducer(agent, {
      event: "thinking_delta",
      data: { content: "thinking...", thinking_id: "t1" },
      timestamp: 1,
    } as SSEEvent);
    // The event lands in events[] for completeness.
    expect(agent.events).toHaveLength(1);
    // AgentState type no longer has thinkingBuffer / activeThinkingId etc.,
    // so we assert the fields are not present on the returned object.
    expect((agent as unknown as Record<string, unknown>).thinkingBuffer).toBeUndefined();
    expect((agent as unknown as Record<string, unknown>).activeThinkingId).toBeUndefined();
    expect((agent as unknown as Record<string, unknown>).thinkingBlocks).toBeUndefined();
  });

  test("text_delta event does not create a streamingText field", () => {
    let agent = createAgentState("sofp_0", "SOFP", "SOFP");
    agent = agentReducer(agent, {
      event: "text_delta",
      data: { content: "Hello" },
      timestamp: 1,
    } as SSEEvent);
    expect(agent.events).toHaveLength(1);
    expect((agent as unknown as Record<string, unknown>).streamingText).toBeUndefined();
    expect((agent as unknown as Record<string, unknown>).textSegments).toBeUndefined();
  });

  // Phase 5.4 — the live reducer must produce exactly the same timeline as
  // buildToolTimeline(events) so live and history replay cannot drift.
  test("agentReducer toolTimeline equals buildToolTimeline(events) for interleaved events", () => {
    let agent = createAgentState("sofp_0", "SOFP", "SOFP");
    const events: SSEEvent[] = [
      { event: "status", data: { phase: "reading_template", message: "" }, timestamp: 1 } as SSEEvent,
      { event: "tool_call", data: { tool_name: "read_template", tool_call_id: "a", args: {} }, timestamp: 2 } as SSEEvent,
      { event: "status", data: { phase: "viewing_pdf", message: "" }, timestamp: 3 } as SSEEvent,
      { event: "tool_call", data: { tool_name: "view_pdf_pages", tool_call_id: "b", args: { pages: [5] } }, timestamp: 4 } as SSEEvent,
      // Results arrive out of order.
      { event: "tool_result", data: { tool_name: "view_pdf_pages", tool_call_id: "b", result_summary: "rendered", duration_ms: 80 }, timestamp: 5 } as SSEEvent,
      { event: "tool_result", data: { tool_name: "read_template", tool_call_id: "a", result_summary: "45 fields", duration_ms: 200 }, timestamp: 6 } as SSEEvent,
    ];
    for (const evt of events) {
      agent = agentReducer(agent, evt);
    }
    // Deep-equal the live timeline to the pure function's output.
    expect(agent.toolTimeline).toEqual(buildToolTimeline(agent.events));
    // Spot-check: both calls populated, in call order, with matching phases.
    expect(agent.toolTimeline.map((e) => [e.tool_call_id, e.phase])).toEqual([
      ["a", "reading_template"],
      ["b", "viewing_pdf"],
    ]);
    expect(agent.toolTimeline[0].result_summary).toBe("45 fields");
    expect(agent.toolTimeline[1].result_summary).toBe("rendered");
  });
});
