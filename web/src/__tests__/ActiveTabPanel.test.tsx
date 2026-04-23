import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ActiveTabPanel } from "../pages/ExtractPage";
import { initialState } from "../lib/appReducer";
import { createAgentState } from "../lib/types";
import { buildToolTimeline } from "../lib/buildToolTimeline";
import type { AppState } from "../lib/appReducer";
import type { SSEEvent, AgentState } from "../lib/types";

// Builds a minimal AppState focused on the active-tab rendering path.
// Everything else (sessionId, toast, runStartTime, …) is left at initial.
function stateWithAgent(
  activeTab: string,
  agent: AgentState,
  extraAgents: Record<string, AgentState> = {},
): AppState {
  return {
    ...initialState,
    agents: { [agent.agentId]: agent, ...extraAgents },
    agentTabOrder: [agent.agentId, ...Object.keys(extraAgents)],
    activeTab,
  };
}

// Convenience: a tool_call event for a Notes-12 sub-agent, shaped exactly
// like listofnotes_subcoordinator._emit produces on the wire.
function subToolCall(subId: string, tcId: string, name: string): SSEEvent {
  // Cast through unknown — the `sub_agent_id` field rides on the event but
  // isn't declared on the ToolCallData discriminated branch, matching the
  // pattern other tests use for extra routing fields.
  return {
    event: "tool_call",
    data: {
      agent_id: "notes:LIST_OF_NOTES",
      agent_role: "LIST_OF_NOTES",
      sub_agent_id: subId,
      tool_call_id: `${subId}:${tcId}`,
      tool_name: name,
      args: {},
    },
    timestamp: 1,
  } as unknown as SSEEvent;
}

describe("ActiveTabPanel — Sheet-12 sub-tabs", () => {
  test("renders NotesSubTabBar only when activeTab is Notes-12 and sub-agents exist", () => {
    const notes12 = createAgentState("notes:LIST_OF_NOTES", "LIST_OF_NOTES", "Notes 12");
    notes12.subAgentBatchRanges = [
      { subAgentId: "notes:LIST_OF_NOTES:sub0", notes: [1, 3], pages: [18, 22] },
      { subAgentId: "notes:LIST_OF_NOTES:sub1", notes: [4, 6], pages: [23, 27] },
    ];
    notes12.events = [subToolCall("notes:LIST_OF_NOTES:sub0", "a", "find_toc")];

    const state = stateWithAgent("notes:LIST_OF_NOTES", notes12);
    render(<ActiveTabPanel state={state} />);

    // Sub-tab bar is present; it renders an "All" chip + one per sub-agent.
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(tabs[0]).toHaveTextContent(/all/i);
  });

  test("does NOT render NotesSubTabBar for non-Notes-12 tabs", () => {
    const sofp = createAgentState("sofp_0", "SOFP", "SOFP");
    sofp.subAgentBatchRanges = [
      // Even with sub-agent metadata present (hypothetical), non-Notes-12
      // tabs must not show the bar. Locks the gate to the agent_id check.
      { subAgentId: "sofp_0:sub0", notes: [1, 3], pages: [18, 22] },
    ];
    const state = stateWithAgent("sofp_0", sofp);
    render(<ActiveTabPanel state={state} />);

    expect(screen.queryByRole("tablist", { name: /sheet-12/i })).not.toBeInTheDocument();
  });

  test("does NOT render NotesSubTabBar when Notes-12 has no sub-agent batches yet", () => {
    // Pre-fan-out: the Notes-12 tab can be active before any `started` SSE
    // event has populated `subAgentBatchRanges`. The flat timeline should
    // render without an empty sub-bar flashing on screen.
    const notes12 = createAgentState("notes:LIST_OF_NOTES", "LIST_OF_NOTES", "Notes 12");
    const state = stateWithAgent("notes:LIST_OF_NOTES", notes12);
    render(<ActiveTabPanel state={state} />);

    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  test("selecting a sub-tab filters the timeline by sub_agent_id", () => {
    // Two sub-agents emit one tool_call each. The All view shows both;
    // clicking a sub chip restricts to that sub's events only.
    const notes12 = createAgentState("notes:LIST_OF_NOTES", "LIST_OF_NOTES", "Notes 12");
    notes12.subAgentBatchRanges = [
      { subAgentId: "notes:LIST_OF_NOTES:sub0", notes: [1, 3], pages: [18, 22] },
      { subAgentId: "notes:LIST_OF_NOTES:sub1", notes: [4, 6], pages: [23, 27] },
    ];
    const sub0Call = subToolCall("notes:LIST_OF_NOTES:sub0", "a", "find_toc");
    const sub1Call = subToolCall("notes:LIST_OF_NOTES:sub1", "b", "view_pages");
    notes12.events = [sub0Call, sub1Call];
    // The live reducer keeps toolTimeline in sync with events via
    // applyStreamingEvent — replicate that here so the "All" branch (which
    // reuses the pre-computed toolTimeline) has rows to render.
    notes12.toolTimeline = buildToolTimeline(notes12.events);

    const state = stateWithAgent("notes:LIST_OF_NOTES", notes12);
    render(<ActiveTabPanel state={state} />);

    // All view shows both tool rows.
    expect(screen.getByText(/locating table of contents/i)).toBeInTheDocument();
    expect(screen.getByText(/checking pdf pages/i)).toBeInTheDocument();

    // Click the Sub 1 chip (index 1 in tabs; index 0 is "All").
    const subChips = screen.getAllByRole("tab");
    fireEvent.click(subChips[1]);

    // Now only sub0's tool row is visible.
    expect(screen.getByText(/locating table of contents/i)).toBeInTheDocument();
    expect(screen.queryByText(/checking pdf pages/i)).not.toBeInTheDocument();
  });

  test("default activeSubId is null (All tab selected)", () => {
    const notes12 = createAgentState("notes:LIST_OF_NOTES", "LIST_OF_NOTES", "Notes 12");
    notes12.subAgentBatchRanges = [
      { subAgentId: "notes:LIST_OF_NOTES:sub0", notes: [1, 3], pages: [18, 22] },
    ];
    const state = stateWithAgent("notes:LIST_OF_NOTES", notes12);
    render(<ActiveTabPanel state={state} />);

    const allTab = screen.getAllByRole("tab")[0];
    expect(allTab.getAttribute("aria-selected")).toBe("true");
  });
});

// ---------------------------------------------------------------------------
// Activity-header toolbar (Stop / Rerun / Stop all). These controls used
// to live as sibling buttons inside AgentTabs (per-pill abort × / rerun ⟲).
// They moved here so the tab strip stays a clean navigation row and the
// destructive actions get proper toolbar styling instead of floating between
// pills. Each test pins the gating contract for one button.
// ---------------------------------------------------------------------------

describe("ActiveTabPanel — header toolbar", () => {
  test("Stop button appears when active agent is running and onAbortAgent is wired", () => {
    const sofp = createAgentState("sofp_0", "SOFP", "SOFP");
    sofp.status = "running";
    sofp.events = [{ event: "status", data: { phase: "filling_workbook", message: "" }, timestamp: 1 } as SSEEvent];
    const state = { ...stateWithAgent("sofp_0", sofp), isRunning: true };
    const onAbort = vi.fn();
    render(<ActiveTabPanel state={state} onAbortAgent={onAbort} />);

    const btn = screen.getByRole("button", { name: /stop sofp/i });
    fireEvent.click(btn);
    expect(onAbort).toHaveBeenCalledWith("sofp_0");
  });

  test("Stop button is hidden when active agent has already completed", () => {
    // The gate is `status === "running"` — pin the negative branch so
    // a "complete" tab never surfaces a no-op Stop button.
    const sofp = createAgentState("sofp_0", "SOFP", "SOFP");
    sofp.status = "complete";
    sofp.events = [{ event: "complete", data: {}, timestamp: 1 } as unknown as SSEEvent];
    const state = { ...stateWithAgent("sofp_0", sofp), isRunning: false };
    render(<ActiveTabPanel state={state} onAbortAgent={() => {}} />);

    expect(screen.queryByRole("button", { name: /stop sofp/i })).not.toBeInTheDocument();
  });

  test("Stop button is hidden on scout/validator tabs even when status is running", () => {
    // Scout has its own controls in the pre-run panel; validator is a
    // cross-check phase, not a runnable agent. Mirror of the AgentTabs
    // SPECIAL_TAB_IDS contract from the old per-pill abort gate.
    const scout = createAgentState("scout", "scout", "Scout");
    scout.status = "running";
    scout.events = [{ event: "status", data: { phase: "viewing_pdf", message: "" }, timestamp: 1 } as SSEEvent];
    const state = { ...stateWithAgent("scout", scout), isRunning: true };
    render(<ActiveTabPanel state={state} onAbortAgent={() => {}} />);

    expect(screen.queryByRole("button", { name: /^stop scout$/i })).not.toBeInTheDocument();
  });

  test("Rerun button appears on failed face-statement tab when not isRunning", () => {
    const sofp = createAgentState("sofp_0", "SOFP", "SOFP");
    sofp.status = "failed";
    sofp.events = [{ event: "status", data: { phase: "filling_workbook", message: "" }, timestamp: 1 } as SSEEvent];
    const state = { ...stateWithAgent("sofp_0", sofp), isRunning: false };
    const onRerun = vi.fn();
    render(<ActiveTabPanel state={state} onRerunAgent={onRerun} />);

    const btn = screen.getByRole("button", { name: /rerun sofp/i });
    fireEvent.click(btn);
    expect(onRerun).toHaveBeenCalledWith("sofp_0");
  });

  test("Rerun button also appears on a cancelled tab (the other half of the gate)", () => {
    // The gate accepts both "failed" and "cancelled" — failed is covered
    // above, this pins cancelled so a one-sided gate change can't slip
    // through unnoticed.
    const sofp = createAgentState("sofp_0", "SOFP", "SOFP");
    sofp.status = "cancelled";
    sofp.events = [{ event: "status", data: { phase: "filling_workbook", message: "" }, timestamp: 1 } as SSEEvent];
    const state = { ...stateWithAgent("sofp_0", sofp), isRunning: false };
    render(<ActiveTabPanel state={state} onRerunAgent={() => {}} />);

    expect(screen.getByRole("button", { name: /rerun sofp/i })).toBeInTheDocument();
  });

  test("Rerun button appears on failed notes tab (Phase D.3 symmetry)", () => {
    const notes = createAgentState("notes:CORP_INFO", "CORP_INFO", "Corporate Information");
    notes.status = "failed";
    notes.events = [{ event: "status", data: { phase: "filling_workbook", message: "" }, timestamp: 1 } as SSEEvent];
    const state = { ...stateWithAgent("notes:CORP_INFO", notes), isRunning: false };
    render(<ActiveTabPanel state={state} onRerunAgent={() => {}} />);

    expect(screen.getByRole("button", { name: /rerun corporate information/i })).toBeInTheDocument();
  });

  test("Rerun button is hidden when isRunning (avoids concurrent writes)", () => {
    // Peer-review finding #5 (old AgentTabs): the rerun gate must respect
    // a global isRunning flag so users can't kick off a duplicate POST
    // while the original run is still draining.
    const sofp = createAgentState("sofp_0", "SOFP", "SOFP");
    sofp.status = "failed";
    sofp.events = [{ event: "status", data: { phase: "filling_workbook", message: "" }, timestamp: 1 } as SSEEvent];
    const state = { ...stateWithAgent("sofp_0", sofp), isRunning: true };
    render(<ActiveTabPanel state={state} onRerunAgent={() => {}} />);

    expect(screen.queryByRole("button", { name: /rerun sofp/i })).not.toBeInTheDocument();
  });

  test("Rerun button is hidden on scout and validator tabs even when failed", () => {
    // Peer-review finding #1 carryover: handleRerunAgent always builds
    // face-statement payloads, so rerunning scout/validator would POST
    // a guaranteed-fail config. Hide the button for those tabs.
    const validator = createAgentState("validator", "validator", "Validator");
    validator.status = "failed";
    const state = { ...stateWithAgent("validator", validator), isRunning: false };
    render(<ActiveTabPanel state={state} onRerunAgent={() => {}} />);

    expect(screen.queryByRole("button", { name: /rerun validator/i })).not.toBeInTheDocument();
  });

  test("Stop all button appears whenever isRunning and onAbortAll is wired", () => {
    const sofp = createAgentState("sofp_0", "SOFP", "SOFP");
    sofp.status = "running";
    sofp.events = [{ event: "status", data: { phase: "filling_workbook", message: "" }, timestamp: 1 } as SSEEvent];
    const state = { ...stateWithAgent("sofp_0", sofp), isRunning: true };
    const onAbortAll = vi.fn();
    render(<ActiveTabPanel state={state} onAbortAll={onAbortAll} />);

    const btn = screen.getByRole("button", { name: /stop all/i });
    fireEvent.click(btn);
    expect(onAbortAll).toHaveBeenCalledTimes(1);
  });

  test("Stop all button also renders on the validator tab (one-click escape always available)", () => {
    // Stop-all is meaningful regardless of which tab is selected. Pin it
    // to the validator branch too so users don't lose the escape hatch
    // after the cross-checks tab gets auto-focused on completion.
    const validator = createAgentState("validator", "validator", "Validator");
    const state = { ...stateWithAgent("validator", validator), isRunning: true };
    render(<ActiveTabPanel state={state} onAbortAll={() => {}} />);

    expect(screen.getByRole("button", { name: /stop all/i })).toBeInTheDocument();
  });

  test("Stop all button renders even when active agent has emitted no events yet", () => {
    // Regression guard: ExtractPage previously gated `<ActiveTabPanel />`
    // behind `state.events.length > 0`, which hid Stop all during the
    // window between RUN_STARTED and the first SSE event. That window
    // can stretch on Windows behind the enterprise proxy while
    // LiteLLM/model creation initialises. The panel itself must render
    // the toolbar regardless of event count.
    const sofp = createAgentState("sofp_0", "SOFP", "SOFP");
    sofp.status = "running";
    sofp.events = []; // no events yet — the no-events window
    const state = { ...stateWithAgent("sofp_0", sofp), isRunning: true };
    render(<ActiveTabPanel state={state} onAbortAll={() => {}} />);

    expect(screen.getByRole("button", { name: /stop all/i })).toBeInTheDocument();
  });

  test("toolbar buttons absent when callbacks not wired (legacy/test-only callers)", () => {
    // ExtractPage always wires the callbacks, but tests construct the
    // panel with just `state`. Guard the gate so missing callbacks don't
    // surface buttons that would no-op on click.
    const sofp = createAgentState("sofp_0", "SOFP", "SOFP");
    sofp.status = "running";
    sofp.events = [{ event: "status", data: { phase: "filling_workbook", message: "" }, timestamp: 1 } as SSEEvent];
    const state = { ...stateWithAgent("sofp_0", sofp), isRunning: true };
    render(<ActiveTabPanel state={state} />);

    expect(screen.queryByRole("button", { name: /stop sofp/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /stop all/i })).not.toBeInTheDocument();
  });
});
