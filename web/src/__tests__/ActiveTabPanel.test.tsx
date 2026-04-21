import { describe, test, expect } from "vitest";
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
