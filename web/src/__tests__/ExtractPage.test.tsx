import { describe, test, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ExtractPage } from "../pages/ExtractPage";
import { initialState } from "../lib/appReducer";
import type { AppState } from "../lib/appReducer";

// Bare minimum no-op props for the component. Each test overrides `state`
// and the two abort handlers (rest stay no-op) to focus on the render gate.
function makeProps(overrides?: { state?: Partial<AppState>; handleAbortAll?: () => Promise<void> }) {
  const state: AppState = { ...initialState, ...(overrides?.state ?? {}) };
  return {
    state,
    dispatch: vi.fn(),
    handleUpload: vi.fn(async () => ({ session_id: "s", filename: "f.pdf" })),
    handleMultiRun: vi.fn(),
    handleAbortAll: overrides?.handleAbortAll ?? (vi.fn(async () => {}) as () => Promise<void>),
    handleAbortAgent: vi.fn(async () => {}) as (id: string) => Promise<void>,
    handleRerunAgent: vi.fn(),
    handleReset: vi.fn(),
  };
}

// ---------------------------------------------------------------------------
// Integration regression guard for the ExtractPage render gate.
//
// RUN_STARTED flips `isRunning: true` but does NOT seed `agents` /
// `agentTabOrder` (see appReducer.ts — those get populated when the first
// SSE event with an `agent_id` lands). Gating the activity shell solely
// on `agentTabOrder.length > 0` therefore hides Stop all during the
// post-RUN_STARTED → pre-first-event window. On Windows behind the
// enterprise proxy that window can stretch while LiteLLM initialises —
// precisely the window where users most need to abort.
//
// This mounts ExtractPage directly so the test fails if anyone narrows
// the render gate back to `agentTabOrder.length > 0` only. A unit test
// on ActiveTabPanel isn't enough — the panel itself renders Stop all
// regardless of events, so the bug lives one layer up.
// ---------------------------------------------------------------------------

describe("ExtractPage — render-gate regression guards", () => {
  test("Stop all is reachable in the RUN_STARTED → first-event window", () => {
    // State shape produced by RUN_STARTED before any SSE event lands:
    // isRunning flipped, statementsInRun populated, but agents/
    // agentTabOrder/events all still empty.
    const props = makeProps({
      state: {
        sessionId: "test-session",
        filename: "test.pdf",
        isRunning: true,
        statementsInRun: ["SOFP", "SOPL"],
        agents: {},
        agentTabOrder: [],
        events: [],
      },
    });
    render(<ExtractPage {...props} />);

    expect(screen.getByRole("button", { name: /stop all/i })).toBeInTheDocument();
  });

  test("activity shell stays hidden before a run is started", () => {
    // Negative case: the gate must NOT open just because statementsInRun
    // is pre-seeded (that can happen from a prior run's config). Only
    // isRunning or a non-empty tab order should trigger the shell.
    const props = makeProps({
      state: {
        sessionId: "test-session",
        filename: "test.pdf",
        isRunning: false,
        statementsInRun: ["SOFP"],
        agents: {},
        agentTabOrder: [],
        events: [],
      },
    });
    render(<ExtractPage {...props} />);

    expect(screen.queryByRole("button", { name: /stop all/i })).not.toBeInTheDocument();
  });
});
