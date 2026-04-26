import { describe, test, expect, afterEach } from "vitest";
import { appReducer, agentReducer, agentSubAgentSummary, bootState, initialState, notesTabLabel, parseRouteFromPath } from "../lib/appReducer";
import type { SSEEvent } from "../lib/types";
import { createAgentState, type AgentState } from "../lib/types";
import { buildToolTimeline } from "../lib/buildToolTimeline";

// Pinned shape for the stripped chat-feed fields — the tests below assert
// they stay stripped. Typed as optional-unknown so `.toBeUndefined()` can
// probe without reintroducing `as unknown as Record<string, unknown>`.
type StrippedChatFields = {
  thinkingBuffer?: unknown;
  activeThinkingId?: unknown;
  thinkingBlocks?: unknown;
  streamingText?: unknown;
  textSegments?: unknown;
};

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

  // Peer-review fix (2026-04-27): coordinator-level error events with a
  // ``type`` discriminator are non-fatal — the backend still continues
  // through correction → notes validator → run_complete. The reducer
  // must surface the error message but leave ``isRunning=true`` so the
  // spinner stays on while the backend finishes its work. Without this,
  // a ``merge_failed`` event mid-run would silently make the UI look
  // done while the pipeline kept running for minutes.
  test("typed coordinator error events do NOT flip isRunning to false", () => {
    for (const type of ["merge_failed", "cross_check_exception", "correction_wallclock_exceeded"]) {
      const running = runningState();
      const state = appReducer(running, {
        type: "EVENT",
        payload: {
          event: "error",
          data: { type, message: "diagnostic", traceback: "" },
          timestamp: 1,
        } as SSEEvent,
      });
      expect(state.hasError).toBe(true);
      // Spinner stays spinning — backend will fire run_complete later.
      expect(state.isRunning).toBe(true);
      // Error message captured for the banner.
      expect(state.error?.message).toBe("diagnostic");
    }
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

  // ---------------------------------------------------------------------------
  // PLAN-stop-and-validation-visibility Phase 2.3 — partial_merge banner.
  // ---------------------------------------------------------------------------

  test("partial_merge event captures payload onto AppState", () => {
    const running = runningState();
    const state = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "partial_merge",
        data: {
          merged: true,
          merged_path: "/output/abc/filled.xlsx",
          statements_included: ["SOFP", "SOPL"],
          notes_included: [],
          statements_missing: ["SOCI"],
          notes_missing: [],
          error: null,
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(state.partialMerge).not.toBeNull();
    expect(state.partialMerge?.merged).toBe(true);
    expect(state.partialMerge?.statements_included).toEqual(["SOFP", "SOPL"]);
    expect(state.partialMerge?.statements_missing).toEqual(["SOCI"]);
    // partial_merge alone does NOT terminate the run — the cancel handler
    // emits a paired error event right after, which owns the
    // running→idle transition.
    expect(state.isRunning).toBe(true);
    expect(state.hasError).toBe(false);
  });

  // ---------------------------------------------------------------------------
  // PLAN-stop-and-validation-visibility Phase 5 — cross-check progress.
  // ---------------------------------------------------------------------------

  test("cross_check_start seeds crossCheckProgress for the active pass", () => {
    const running = runningState();
    const state = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "cross_check_start",
        data: { phase: "initial", total: 5 },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(state.crossCheckProgress.phase).toBe("initial");
    expect(state.crossCheckProgress.total).toBe(5);
    expect(state.crossCheckProgress.results).toEqual([]);
    expect(state.crossCheckProgress.isComplete).toBe(false);
  });

  test("cross_check_result events accumulate progressively", () => {
    let state = runningState();
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "cross_check_start",
        data: { phase: "initial", total: 2 },
        timestamp: 1,
      } as SSEEvent,
    });
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "cross_check_result",
        data: {
          phase: "initial", index: 0, total: 2,
          name: "check_a", status: "passed",
          expected: null, actual: null, diff: null, tolerance: null,
          message: "ok",
        },
        timestamp: 2,
      } as SSEEvent,
    });
    expect(state.crossCheckProgress.results).toHaveLength(1);
    expect(state.crossCheckProgress.results[0].name).toBe("check_a");

    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "cross_check_result",
        data: {
          phase: "initial", index: 1, total: 2,
          name: "check_b", status: "failed",
          expected: 100, actual: 90, diff: -10, tolerance: 1,
          message: "off by 10",
        },
        timestamp: 3,
      } as SSEEvent,
    });
    expect(state.crossCheckProgress.results).toHaveLength(2);
    expect(state.crossCheckProgress.results[1].status).toBe("failed");
  });

  test("post_correction phase replaces initial-pass results in-place", () => {
    // Initial pass arrives, then re-run after correction. The Validator
    // tab should show the post-correction state — initial-pass rows
    // must NOT linger.
    let state = runningState();
    const initialEvents: SSEEvent[] = [
      { event: "cross_check_start", data: { phase: "initial", total: 1 }, timestamp: 1 } as SSEEvent,
      {
        event: "cross_check_result",
        data: {
          phase: "initial", index: 0, total: 1,
          name: "check_a", status: "failed",
          expected: 100, actual: 90, diff: -10, tolerance: 1,
          message: "off by 10",
        },
        timestamp: 2,
      } as SSEEvent,
      { event: "cross_check_complete", data: { phase: "initial", passed: 0, failed: 1, warnings: 0, not_applicable: 0, pending: 0 }, timestamp: 3 } as SSEEvent,
    ];
    for (const e of initialEvents) {
      state = appReducer(state, { type: "EVENT", payload: e });
    }
    expect(state.crossCheckProgress.phase).toBe("initial");
    expect(state.crossCheckProgress.results[0].status).toBe("failed");

    // Now post-correction pass — the SAME check passes after the agent
    // edits the workbook. The reducer must drop the old result.
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "cross_check_start",
        data: { phase: "post_correction", total: 1 },
        timestamp: 4,
      } as SSEEvent,
    });
    expect(state.crossCheckProgress.phase).toBe("post_correction");
    expect(state.crossCheckProgress.results).toHaveLength(0);

    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "cross_check_result",
        data: {
          phase: "post_correction", index: 0, total: 1,
          name: "check_a", status: "passed",
          expected: 100, actual: 100, diff: 0, tolerance: 1,
          message: "ok after correction",
        },
        timestamp: 5,
      } as SSEEvent,
    });
    expect(state.crossCheckProgress.results[0].status).toBe("passed");
  });

  // ---------------------------------------------------------------------------
  // PLAN-stop-and-validation-visibility Phase 6 — pipeline stage label.
  // ---------------------------------------------------------------------------

  test("pipeline_stage event captures stage onto AppState", () => {
    let state = runningState();
    expect(state.pipelineStage).toBeNull();

    const stages = ["extracting", "merging", "cross_checking", "correcting", "done"] as const;
    for (const stage of stages) {
      state = appReducer(state, {
        type: "EVENT",
        payload: {
          event: "pipeline_stage",
          data: { stage, started_at: 1700000000 },
          timestamp: 1,
        } as SSEEvent,
      });
      expect(state.pipelineStage).toBe(stage);
    }
  });

  test("RUN_STARTED clears stale pipelineStage from previous run", () => {
    let state = runningState();
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "pipeline_stage",
        data: { stage: "done", started_at: 1700000000 },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(state.pipelineStage).toBe("done");

    const restarted = appReducer(state, { type: "RUN_STARTED" });
    expect(restarted.pipelineStage).toBeNull();
  });

  test("RUN_STARTED clears any prior partial_merge banner", () => {
    const running = runningState();
    const withPartial = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "partial_merge",
        data: {
          merged: true,
          merged_path: "/output/abc/filled.xlsx",
          statements_included: ["SOFP"],
          notes_included: [],
          statements_missing: [],
          notes_missing: [],
          error: null,
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(withPartial.partialMerge).not.toBeNull();

    // Starting a new run wipes the banner so users don't see stale
    // "Saved partial workbook" text from the previous Stop All.
    const restarted = appReducer(withPartial, { type: "RUN_STARTED" });
    expect(restarted.partialMerge).toBeNull();
  });

  // ---------------------------------------------------------------------------
  // Persistent draft uploads — Phase C, steps 11-12.
  // /run/{id} is the canonical shareable URL for an upload-then-extract run.
  // It maps to view='extract' so the same workspace UI is used; currentRunId
  // tells ExtractPage to fetch + rehydrate from the server. Existing
  // /history/{id} stays a separate path (still resolves to view='history').
  // ---------------------------------------------------------------------------

  test("parseRouteFromPath maps /run/42 to extract view with currentRunId=42", () => {
    const route = parseRouteFromPath("/run/42");
    expect(route.view).toBe("extract");
    expect(route.currentRunId).toBe(42);
    // History selection is independent — a /run URL never seeds it.
    expect(route.selectedRunId).toBeNull();
  });

  test("parseRouteFromPath /run/42/ (trailing slash) parses identically", () => {
    const route = parseRouteFromPath("/run/42/");
    expect(route.currentRunId).toBe(42);
  });

  test("parseRouteFromPath leaves currentRunId null for /history routes", () => {
    expect(parseRouteFromPath("/history").currentRunId).toBeNull();
    expect(parseRouteFromPath("/history/9").currentRunId).toBeNull();
  });

  test("parseRouteFromPath returns currentRunId=null for the root path", () => {
    expect(parseRouteFromPath("/").currentRunId).toBeNull();
  });

  test("parseRouteFromPath /run/garbage falls back to extract with no run id", () => {
    // Non-numeric suffix means "we don't know which run" — show the
    // generic extract page rather than throwing or routing to history.
    const route = parseRouteFromPath("/run/abc");
    expect(route.view).toBe("extract");
    expect(route.currentRunId).toBeNull();
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
    const shape = state as StrippedChatFields;
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
    const shape = state as StrippedChatFields;
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

  test("RUN_STARTED with notes records notesInRun (PLAN §4 D.3)", () => {
    const withSession = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "test.pdf" },
    });
    const state = appReducer(withSession, {
      type: "RUN_STARTED",
      payload: { statements: ["SOFP"], notes: ["CORP_INFO", "LIST_OF_NOTES"] },
    });
    expect(state.statementsInRun).toEqual(["SOFP"]);
    expect(state.notesInRun).toEqual(["CORP_INFO", "LIST_OF_NOTES"]);
  });

  test("notesTabLabel normalizes every notes identifier shape to the same label", () => {
    // Single source of truth for live tabs, skeletons, and history.
    expect(notesTabLabel("CORP_INFO")).toBe("Notes 10: Corp Info");
    expect(notesTabLabel("notes:CORP_INFO")).toBe("Notes 10: Corp Info");
    expect(notesTabLabel("NOTES_CORP_INFO")).toBe("Notes 10: Corp Info");
    expect(notesTabLabel("LIST_OF_NOTES")).toBe("Notes 12: List of Notes");
    // Unknown key falls back without throwing — forward-compatible with
    // templates that exist before the UI map is updated.
    expect(notesTabLabel("FUTURE_TEMPLATE")).toBe("Notes: FUTURE_TEMPLATE");
  });

  test("appReducer_handles_correction_agent", () => {
    // Phase 7.1: a CORRECTION agent_id event must materialise a tab with
    // a friendly "Correction" label, without any frontend enum extension
    // (backend promise in PLAN §3.2: reuse the existing coarse envelope).
    const running = appReducer(
      appReducer(initialState, {
        type: "UPLOADED",
        payload: { sessionId: "s", filename: "x.pdf" },
      }),
      { type: "RUN_STARTED", payload: {} },
    );
    const after = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "status",
        data: {
          phase: "started",
          message: "Correction agent started.",
          agent_id: "CORRECTION",
          agent_role: "CORRECTION",
        },
        timestamp: 1,
      } as SSEEvent,
    });
    const agent = after.agents["CORRECTION"];
    expect(agent).toBeDefined();
    expect(agent.label).toBe("Correction");
    expect(after.agentTabOrder).toContain("CORRECTION");
  });

  test("appReducer_handles_notes_validator_agent", () => {
    // Phase 7.1: NOTES_VALIDATOR pseudo-agent routes through the same
    // coarse SSE envelope and lands as a dedicated tab.
    const running = appReducer(
      appReducer(initialState, {
        type: "UPLOADED",
        payload: { sessionId: "s", filename: "x.pdf" },
      }),
      { type: "RUN_STARTED", payload: {} },
    );
    const after = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "status",
        data: {
          phase: "started",
          message: "Notes validator started.",
          agent_id: "NOTES_VALIDATOR",
          agent_role: "NOTES_VALIDATOR",
        },
        timestamp: 1,
      } as SSEEvent,
    });
    const agent = after.agents["NOTES_VALIDATOR"];
    expect(agent).toBeDefined();
    expect(agent.label).toBe("Notes Validator");
    expect(after.agentTabOrder).toContain("NOTES_VALIDATOR");
  });

  test("run_complete synthesizes a 'Cross-checks' tab, not 'VALIDATOR'", () => {
    // Bug 4b — the synthetic cross-checks tab used to render with the
    // default uppercased-role fallback ("VALIDATOR"), which sat right
    // next to the real "Notes Validator" agent tab and looked like a
    // duplicate. Pin a friendlier label via PSEUDO_AGENT_LABELS.
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
            { name: "sofp_balance", status: "passed", expected: 100,
              actual: 100, diff: 0, tolerance: 1, message: "OK" },
          ],
          statements_completed: [],
          statements_failed: [],
          notes_completed: [],
          notes_failed: [],
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(state.agents.validator).toBeDefined();
    expect(state.agents.validator.label).toBe("Cross-checks");
  });

  test("notes agent slot gets the friendly tab label derived from role", () => {
    // When a notes SSE event arrives, ensureAgent creates the slot using
    // deriveAgentLabel(agentId, role). Verify the label is the short
    // "Notes 10: Corp Info" form rather than the raw role string.
    const running = appReducer(
      appReducer(initialState, {
        type: "UPLOADED",
        payload: { sessionId: "s", filename: "x.pdf" },
      }),
      {
        type: "RUN_STARTED",
        payload: { statements: [], notes: ["CORP_INFO"] },
      },
    );
    const after = appReducer(running, {
      type: "EVENT",
      payload: {
        event: "status",
        data: {
          phase: "reading_template",
          message: "started",
          agent_id: "notes:CORP_INFO",
          agent_role: "CORP_INFO",
        },
        timestamp: 1,
      } as SSEEvent,
    });
    const agent = after.agents["notes:CORP_INFO"];
    expect(agent).toBeDefined();
    expect(agent.label).toBe("Notes 10: Corp Info");
    expect(agent.role).toBe("CORP_INFO");
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

  // Peer-review regression: the backend's validation-fail paths emit
  // `run_complete { success: false, message: "..." }` with no
  // merge_errors / statements_* / agents. The reducer must preserve
  // `message` into complete.error so the UI (ResultsView + TerminalRow)
  // can render an actionable reason instead of a bare "Failed".
  test("run_complete validation-fail preserves message into complete.error", () => {
    const uploaded = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "test.pdf" },
    });
    const started = appReducer(uploaded, {
      type: "RUN_STARTED",
      payload: { statements: ["SOFP"] },
    });
    const ended = appReducer(started, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: false,
          message: "Model setup failed: missing API key",
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(ended.isComplete).toBe(true);
    expect(ended.isRunning).toBe(false);
    expect(ended.complete).not.toBeNull();
    expect(ended.complete!.success).toBe(false);
    expect(ended.complete!.error).toBe("Model setup failed: missing API key");
  });

  test("run_complete success path leaves complete.error as null", () => {
    const uploaded = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "test.pdf" },
    });
    const started = appReducer(uploaded, {
      type: "RUN_STARTED",
      payload: { statements: ["SOFP"] },
    });
    const done = appReducer(started, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: true,
          merged_workbook: "/tmp/out.xlsx",
          merge_errors: [],
          cross_checks: [],
          statements_completed: ["SOFP"],
          statements_failed: [],
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(done.complete!.error).toBeNull();
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
    const shape = agent as StrippedChatFields;
    expect(shape.thinkingBuffer).toBeUndefined();
    expect(shape.activeThinkingId).toBeUndefined();
    expect(shape.thinkingBlocks).toBeUndefined();
  });

  test("text_delta event does not create a streamingText field", () => {
    let agent = createAgentState("sofp_0", "SOFP", "SOFP");
    agent = agentReducer(agent, {
      event: "text_delta",
      data: { content: "Hello" },
      timestamp: 1,
    } as SSEEvent);
    expect(agent.events).toHaveLength(1);
    const shape = agent as StrippedChatFields;
    expect(shape.streamingText).toBeUndefined();
    expect(shape.textSegments).toBeUndefined();
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

  // Phase 4.1 — after the incremental-merge refactor, the per-event cost of
  // tool_call / tool_result must be bounded by the number of tool calls
  // accumulated so far (linear in M), not by the full event history (linear
  // in N). We can't measure time reliably in CI, but we CAN assert that
  // processing 1000 events completes quickly and produces the expected count.
  //
  // The old O(N²) implementation rebuilt the whole timeline from events on
  // every tool event. At 1000 events this routinely blew past 500 ms on
  // slower CI workers; the linear path should finish in well under 100 ms.
  test("agentReducer processes 1000 tool events in bounded time (#7)", () => {
    let agent = createAgentState("sofp_0", "SOFP", "SOFP");
    const N = 1000;
    const events: SSEEvent[] = [];
    // Half tool_calls, half tool_results — realistic mix.
    for (let i = 0; i < N / 2; i++) {
      events.push({
        event: "tool_call",
        data: { tool_name: "view_pdf_pages", tool_call_id: `c${i}`, args: { pages: [i] } },
        timestamp: i,
      } as SSEEvent);
    }
    for (let i = 0; i < N / 2; i++) {
      events.push({
        event: "tool_result",
        data: { tool_name: "view_pdf_pages", tool_call_id: `c${i}`, result_summary: "ok", duration_ms: 5 },
        timestamp: N + i,
      } as SSEEvent);
    }
    const start = performance.now();
    for (const evt of events) agent = agentReducer(agent, evt);
    const elapsed = performance.now() - start;
    expect(agent.toolTimeline.length).toBe(N / 2);
    // Budget is intentionally generous for CI noise. The old quadratic
    // implementation routinely exceeded 500 ms at this size on slower
    // runners; anything under 300 ms proves the rebuild is gone.
    expect(elapsed).toBeLessThan(300);
  });

  // Phase 10.1 — back-to-back run regression. An UPLOAD after a completed
  // run must wipe every completion field so the second run starts clean.
  // Locks in the invariant behind #41 so future tweaks to UPLOADED can't
  // leak stale ResultsView / cross-check state between runs.
  test("UPLOADED after a completed run clears stale completion fields", () => {
    // Run 1: upload → start → run_complete with cross-checks and success toast
    let state = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "run1", filename: "first.pdf" },
    });
    state = appReducer(state, {
      type: "RUN_STARTED",
      payload: { statements: ["SOFP"] },
    });
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: true,
          merged_workbook: "/tmp/run1.xlsx",
          merge_errors: [],
          cross_checks: [
            {
              name: "SOFP balance",
              status: "passed",
              expected: 100,
              actual: 100,
              diff: 0,
              tolerance: 1,
              message: "",
            },
          ],
          statements_completed: ["SOFP"],
          statements_failed: [],
        },
        timestamp: 1,
      } as SSEEvent,
    });
    // Sanity: run 1 finished with toast, complete, crossChecks populated
    expect(state.isComplete).toBe(true);
    expect(state.complete).not.toBeNull();
    expect(state.crossChecks.length).toBe(1);
    expect(state.toast).not.toBeNull();

    // Run 2: new upload — every completion signal should be wiped.
    state = appReducer(state, {
      type: "UPLOADED",
      payload: { sessionId: "run2", filename: "second.pdf" },
    });
    expect(state.sessionId).toBe("run2");
    expect(state.filename).toBe("second.pdf");
    expect(state.isComplete).toBe(false);
    expect(state.complete).toBeNull();
    expect(state.crossChecks).toEqual([]);
    expect(state.crossChecksPartial).toBe(false);
    expect(state.hasError).toBe(false);
    expect(state.error).toBeNull();
    expect(state.toast).toBeNull();
    expect(state.events).toEqual([]);
    expect(state.agents).toEqual({});
    expect(state.agentTabOrder).toEqual([]);
    expect(state.activeTab).toBeNull();
    expect(state.statementsInRun).toEqual([]);

    state = appReducer(state, {
      type: "RUN_STARTED",
      payload: { statements: ["SOPL"] },
    });
    expect(state.isRunning).toBe(true);
    expect(state.statementsInRun).toEqual(["SOPL"]);
  });

  // -------------------------------------------------------------------------
  // run_complete notes-tab reconciliation (peer-review #3)
  //
  // When the notes coordinator crashes before per-agent `complete` events
  // land, server.py still ships `notes_completed` / `notes_failed` arrays
  // on the run_complete event. The reducer reconciles those into terminal
  // tab states so pending skeletons don't stick forever.
  // -------------------------------------------------------------------------

  test("run_complete materializes missing notes tabs from notes_failed", () => {
    // Simulate: notes coordinator crashed before emitting any per-agent
    // events. The run_complete payload reports CORP_INFO as failed, but
    // no "notes:CORP_INFO" tab exists in state yet.
    let state = runningState();
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: false,
          merged_workbook: null,
          merge_errors: [],
          cross_checks: [],
          statements_completed: [],
          statements_failed: [],
          notes_completed: [],
          notes_failed: ["CORP_INFO"],
        },
        timestamp: 1,
      } as SSEEvent,
    });
    // Tab must now exist, keyed under the live-event id scheme
    // (`notes:<TEMPLATE>`), and land in a terminal state so the UI
    // doesn't keep rendering a pending skeleton.
    expect(state.agents).toHaveProperty("notes:CORP_INFO");
    expect(state.agents["notes:CORP_INFO"].status).toBe("failed");
    expect(state.agentTabOrder).toContain("notes:CORP_INFO");
  });

  test("run_complete does not overwrite a notes tab already terminal via its own complete event", () => {
    // Live per-agent complete landed first (succeeded). The run_complete
    // payload also lists it — reducer must NOT clobber the live status.
    let state = runningState();
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "complete",
        data: {
          success: true,
          agent_id: "notes:ACC_POLICIES",
          agent_role: "ACC_POLICIES",
          workbook_path: "/out/NOTES_ACC_POLICIES_filled.xlsx",
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(state.agents["notes:ACC_POLICIES"].status).toBe("complete");

    // Now the aggregate run_complete lands. Backend reports same template
    // as completed — this must be a no-op for the already-terminal tab.
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: true,
          merged_workbook: "/out/filled.xlsx",
          merge_errors: [],
          cross_checks: [],
          statements_completed: [],
          statements_failed: [],
          notes_completed: ["ACC_POLICIES"],
          notes_failed: [],
        },
        timestamp: 2,
      } as SSEEvent,
    });
    expect(state.agents["notes:ACC_POLICIES"].status).toBe("complete");
  });

  test("run_complete without notes rollup arrays does not crash or alter notes tabs", () => {
    // Back-compat: older backends / replays that omit the notes arrays
    // must not blow up the reducer.
    let state = runningState();
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "status",
        data: { phase: "reading_template", message: "Start", agent_id: "notes:CORP_INFO", agent_role: "CORP_INFO" },
        timestamp: 1,
      } as SSEEvent,
    });
    const preStatus = state.agents["notes:CORP_INFO"].status;

    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: true,
          merged_workbook: "/out/filled.xlsx",
          merge_errors: [],
          cross_checks: [],
          // notes_completed / notes_failed intentionally absent.
          statements_completed: [],
          statements_failed: [],
        },
        timestamp: 2,
      } as SSEEvent,
    });
    // Existing notes tab untouched (status preserved).
    expect(state.agents["notes:CORP_INFO"].status).toBe(preStatus);
  });

  test("run_complete flips running notes tab to terminal when rolled up as failed", () => {
    // Tab exists and is mid-run (no per-agent complete landed). The
    // coordinator-crash synthesis shows it as failed — reducer must
    // flip it, otherwise the tab sits on "running" / "pending" forever.
    let state = runningState();
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "status",
        data: { phase: "viewing_pdf", message: "Viewing", agent_id: "notes:RELATED_PARTY", agent_role: "RELATED_PARTY" },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(state.agents["notes:RELATED_PARTY"].status).not.toBe("failed");

    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: false,
          merged_workbook: null,
          merge_errors: [],
          cross_checks: [],
          statements_completed: [],
          statements_failed: [],
          notes_completed: [],
          notes_failed: ["RELATED_PARTY"],
        },
        timestamp: 2,
      } as SSEEvent,
    });
    expect(state.agents["notes:RELATED_PARTY"].status).toBe("failed");
  });

  // -------------------------------------------------------------------------
  // Bug 1 — same reconciliation pass for face statement tabs.
  //
  // Under proxy buffering on Windows (or any early SSE close) a face-agent's
  // per-agent `complete` event can drop on the floor even though the agent
  // finished and its workbook was merged. Without a backstop, the tab stays
  // stuck at "running" and the strip lights up orange for ever. run_complete
  // ships statements_completed / statements_failed — mirror the notes rule.
  // -------------------------------------------------------------------------

  test("run_complete flips running statement tab to complete from statements_completed", () => {
    let state = runningState();
    // Seed a face tab via a status event; no per-agent complete ever lands.
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "status",
        data: {
          phase: "filling_workbook",
          message: "Filling",
          agent_id: "sofp",
          agent_role: "SOFP",
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(state.agents["sofp"].status).toBe("running");

    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: true,
          merged_workbook: "/out/filled.xlsx",
          merge_errors: [],
          cross_checks: [],
          statements_completed: ["SOFP"],
          statements_failed: [],
          notes_completed: [],
          notes_failed: [],
        },
        timestamp: 2,
      } as SSEEvent,
    });
    expect(state.agents["sofp"].status).toBe("complete");
  });

  test("run_complete flips orphan statement tab to failed from statements_failed", () => {
    let state = runningState();
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "status",
        data: { phase: "viewing_pdf", message: "Viewing", agent_id: "sopl", agent_role: "SOPL" },
        timestamp: 1,
      } as SSEEvent,
    });
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: false,
          merged_workbook: null,
          merge_errors: [],
          cross_checks: [],
          statements_completed: [],
          statements_failed: ["SOPL"],
          notes_completed: [],
          notes_failed: [],
        },
        timestamp: 2,
      } as SSEEvent,
    });
    expect(state.agents["sopl"].status).toBe("failed");
  });

  test("run_complete does not overwrite a statement tab already terminal via its own complete event", () => {
    // The live per-agent complete event is authoritative — reconcile must
    // only fill gaps, never clobber a correctly-terminal status.
    let state = runningState();
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "complete",
        data: {
          success: false,
          agent_id: "soci",
          agent_role: "SOCI",
          error: "timeout",
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(state.agents["soci"].status).toBe("failed");

    // Backend reports SOCI as failed too — no-op expected.
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: false,
          merged_workbook: null,
          merge_errors: [],
          cross_checks: [],
          // Corner case: if backend ever disagrees ("completed" but live
          // said "failed"), trust the live event — it saw the actual error.
          statements_completed: ["SOCI"],
          statements_failed: [],
          notes_completed: [],
          notes_failed: [],
        },
        timestamp: 2,
      } as SSEEvent,
    });
    // Status must remain 'failed' — the reconciler only fills gaps.
    expect(state.agents["soci"].status).toBe("failed");
  });

  test("run_complete materializes missing statement tabs from statements_failed", () => {
    // Coordinator crashed before any per-agent event landed. Backstop must
    // create the tab AND flip it to failed so the user sees the outcome.
    let state = runningState();
    state = appReducer(state, {
      type: "EVENT",
      payload: {
        event: "run_complete",
        data: {
          success: false,
          merged_workbook: null,
          merge_errors: [],
          cross_checks: [],
          statements_completed: [],
          statements_failed: ["SOCF"],
          notes_completed: [],
          notes_failed: [],
        },
        timestamp: 1,
      } as SSEEvent,
    });
    expect(state.agents).toHaveProperty("socf");
    expect(state.agents["socf"].status).toBe("failed");
    expect(state.agentTabOrder).toContain("socf");
  });
});

// ---------------------------------------------------------------------------
// Phase 5.2 / peer-review [M1] — Sheet-12 sub-agent batch-range metadata
// ---------------------------------------------------------------------------

describe("Sheet-12 sub-agent batch ranges", () => {
  test("status:started with batch_note_range + batch_page_range lands on AgentState", () => {
    let agent = createAgentState("notes:LIST_OF_NOTES", "LIST_OF_NOTES", "Notes 12");
    agent = agentReducer(agent, {
      event: "status",
      data: {
        phase: "started",
        message: "notes:LIST_OF_NOTES:sub0 starting (Notes 1-3, pp 18-30, 3 notes)...",
        sub_agent_id: "notes:LIST_OF_NOTES:sub0",
        batch_note_range: [1, 3],
        batch_page_range: [18, 30],
      },
      timestamp: 1,
    } as SSEEvent);
    expect(agent.subAgentBatchRanges).toHaveLength(1);
    expect(agent.subAgentBatchRanges?.[0]).toEqual({
      subAgentId: "notes:LIST_OF_NOTES:sub0",
      notes: [1, 3],
      pages: [18, 30],
    });
  });

  test("multiple sub-agent starts accumulate in first-seen order", () => {
    let agent = createAgentState("notes:LIST_OF_NOTES", "LIST_OF_NOTES", "Notes 12");
    const bases = [
      { id: "sub0", notes: [1, 3], pages: [18, 30] },
      { id: "sub1", notes: [4, 6], pages: [30, 32] },
      { id: "sub2", notes: [7, 9], pages: [32, 33] },
      { id: "sub3", notes: [10, 12], pages: [33, 34] },
      { id: "sub4", notes: [13, 15], pages: [34, 37] },
    ];
    for (let i = 0; i < bases.length; i++) {
      const b = bases[i];
      agent = agentReducer(agent, {
        event: "status",
        data: {
          phase: "started",
          message: `${b.id} starting (${b.notes[0]}-${b.notes[1]})`,
          sub_agent_id: b.id,
          batch_note_range: b.notes,
          batch_page_range: b.pages,
        },
        timestamp: i + 1,
      } as SSEEvent);
    }
    expect(agent.subAgentBatchRanges).toHaveLength(5);
    expect(agent.subAgentBatchRanges?.map(r => r.subAgentId)).toEqual(
      ["sub0", "sub1", "sub2", "sub3", "sub4"]
    );
  });

  test("retry of same sub-agent replaces prior entry rather than duplicating", () => {
    let agent = createAgentState("notes:LIST_OF_NOTES", "LIST_OF_NOTES", "Notes 12");
    agent = agentReducer(agent, {
      event: "status",
      data: {
        phase: "started", message: "first start",
        sub_agent_id: "sub0",
        batch_note_range: [1, 3], batch_page_range: [18, 30],
      },
      timestamp: 1,
    } as SSEEvent);
    // Second "started" — retry scenario.
    agent = agentReducer(agent, {
      event: "status",
      data: {
        phase: "started", message: "retry start",
        sub_agent_id: "sub0",
        batch_note_range: [1, 3], batch_page_range: [18, 30],
      },
      timestamp: 2,
    } as SSEEvent);
    expect(agent.subAgentBatchRanges).toHaveLength(1);
  });

  test("status events without batch range are ignored for sub-agent tracking", () => {
    let agent = createAgentState("notes:LIST_OF_NOTES", "LIST_OF_NOTES", "Notes 12");
    agent = agentReducer(agent, {
      event: "status",
      data: { phase: "viewing_pdf", message: "no batch info" },
      timestamp: 1,
    } as SSEEvent);
    // Either undefined or empty — both acceptable, assert nothing leaked.
    const ranges = agent.subAgentBatchRanges ?? [];
    expect(ranges).toHaveLength(0);
  });

  test("agentSubAgentSummary produces terse tab-level label", () => {
    const agent: AgentState = {
      ...createAgentState("notes:LIST_OF_NOTES", "LIST_OF_NOTES", "Notes 12"),
      subAgentBatchRanges: [
        { subAgentId: "sub0", notes: [1, 3], pages: [18, 30] },
        { subAgentId: "sub1", notes: [4, 6], pages: [30, 32] },
        { subAgentId: "sub4", notes: [13, 15], pages: [34, 37] },
      ],
    };
    expect(agentSubAgentSummary(agent)).toBe("Notes 1-15, pp 18-37");
  });

  test("agentSubAgentSummary returns null when no sub-agents have reported", () => {
    const agent = createAgentState("notes:LIST_OF_NOTES", "LIST_OF_NOTES", "Notes 12");
    expect(agentSubAgentSummary(agent)).toBeNull();
  });

  test("agentSubAgentSummary collapses single-note/single-page batches", () => {
    const agent: AgentState = {
      ...createAgentState("notes:LIST_OF_NOTES", "LIST_OF_NOTES", "Notes 12"),
      subAgentBatchRanges: [
        { subAgentId: "sub0", notes: [5, 5], pages: [22, 22] },
      ],
    };
    expect(agentSubAgentSummary(agent)).toBe("Note 5, p 22");
  });
});

// ---------------------------------------------------------------------------
// bootState — URL-driven state hydration. Deep-linking to /history/<id>
// must both flip the view to history AND preselect the run so the
// full-page detail renders on first paint (no flash of list).
// ---------------------------------------------------------------------------

describe("bootState", () => {
  const origPath = typeof window !== "undefined" ? window.location.pathname : "/";

  afterEach(() => {
    // Restore the URL after each case so later test files don't see
    // /history lingering on the location.
    if (typeof window !== "undefined") {
      window.history.replaceState({}, "", origPath);
    }
  });

  function setPath(path: string) {
    window.history.replaceState({}, "", path);
  }

  test("pathname '/' boots to extract with no selected run", () => {
    setPath("/");
    const s = bootState();
    expect(s.view).toBe("extract");
    expect(s.selectedRunId).toBeNull();
  });

  test("pathname '/history' boots to history with no selected run", () => {
    setPath("/history");
    const s = bootState();
    expect(s.view).toBe("history");
    expect(s.selectedRunId).toBeNull();
  });

  test("pathname '/history/42' boots to history with selectedRunId=42", () => {
    setPath("/history/42");
    const s = bootState();
    expect(s.view).toBe("history");
    expect(s.selectedRunId).toBe(42);
  });

  test("pathname '/history/not-a-number' ignores the id", () => {
    // Defensive — a garbage trailing segment shouldn't crash boot or
    // pre-select a non-existent run. View still lands on history so the
    // user sees the list instead of the extract tab.
    setPath("/history/not-a-number");
    const s = bootState();
    expect(s.view).toBe("history");
    expect(s.selectedRunId).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Peer-review #3 (HIGH, RUN-REVIEW follow-up): sessionRunId tracking
// ---------------------------------------------------------------------------
// Pre-fix, ExtractPage's rehydrate effect skipped re-fetching whenever
// `state.sessionId` was set, regardless of which run that session
// belonged to. Navigating from /run/A to /run/B kept A's session
// attached to B's URL — scout would then run against the wrong PDF.
// The fix carries `sessionRunId` alongside `sessionId` so the effect
// can tell "fresh upload for this run" from "stale prior session".

describe("UPLOADED sessionRunId ownership tracking", () => {
  test("UPLOADED with explicit runId pins sessionRunId to it", () => {
    const state = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "x.pdf", runId: 42 },
    });
    expect(state.sessionId).toBe("abc");
    expect(state.sessionRunId).toBe(42);
  });

  test("UPLOADED without runId falls back to current state.currentRunId", () => {
    // Legacy path / no run_id from upload response — preserve the
    // shareable URL's id as the session owner.
    const seeded = appReducer(initialState, {
      type: "SET_CURRENT_RUN_ID", payload: 7,
    });
    const state = appReducer(seeded, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "x.pdf" },
    });
    expect(state.sessionRunId).toBe(7);
  });

  test("RESET clears sessionRunId so a future upload starts owner-clean", () => {
    const uploaded = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "x.pdf", runId: 9 },
    });
    expect(uploaded.sessionRunId).toBe(9);
    const reset = appReducer(uploaded, { type: "RESET" });
    expect(reset.sessionRunId).toBeNull();
    expect(reset.sessionId).toBeNull();
  });

  test("rehydration UPLOADED with explicit runId beats prior sessionRunId", () => {
    // User uploads a file under run 42, then navigates to /run/100 and
    // ExtractPage's effect dispatches a fresh UPLOADED with runId: 100
    // after fetchRunDetail. The latest dispatch wins.
    const first = appReducer(initialState, {
      type: "UPLOADED",
      payload: { sessionId: "abc", filename: "a.pdf", runId: 42 },
    });
    const second = appReducer(first, {
      type: "UPLOADED",
      payload: { sessionId: "xyz", filename: "b.pdf", runId: 100 },
    });
    expect(second.sessionRunId).toBe(100);
    expect(second.sessionId).toBe("xyz");
  });
});
