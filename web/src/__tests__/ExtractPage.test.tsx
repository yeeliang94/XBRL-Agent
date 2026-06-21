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
    // Persistent-draft uploads (commit 6e139a4) added `run_id` to the
    // upload response — null is the legacy "no draft row was created"
    // fallback the persistent-draft work itself preserves for older
    // backends. Without it `tsc -b` fails because ExtractPageProps now
    // requires the field.
    handleUpload: vi.fn(async () => ({ session_id: "s", filename: "f.pdf", run_id: null })),
    handleMultiRun: vi.fn(),
    handleAbortAll: overrides?.handleAbortAll ?? (vi.fn(async () => {}) as () => Promise<void>),
    handleAbortAgent: vi.fn(async () => {}) as (id: string) => Promise<void>,
    handleRerunAgent: vi.fn(),
    handleReset: vi.fn(),
    // Forwarded to ResultsView as onOpenRunDetail (the full-run-report door).
    onOpenRun: vi.fn(),
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

  // De-gating regression guard (review-access bug, 2026-06-21). After a run
  // completes, the ONLY in-screen bridge back to the review page is the
  // "Review extracted values" link in ResultsView. It used to be gated on a
  // `canonicalEnabled` flag hydrated by a one-shot /api/config fetch — a
  // raced/failed fetch left the flag false and silently hid the link, leaving
  // the user stranded with only the download button. Canonical mode is
  // mandatory now (gotcha #21), so the gate was removed: the link must appear
  // whenever the completed run's id is known, independent of any flag. This
  // fails if anyone re-introduces a feature-flag gate on the review link.
  test("review link is offered after completion whenever the run id is known", () => {
    const props = makeProps({
      state: {
        sessionId: "test-session",
        filename: "test.pdf",
        isComplete: true,
        complete: {
          success: true,
          output_path: "",
          excel_path: "/output/x/filled.xlsx",
          trace_path: "",
          total_tokens: 0,
          cost: 0,
          runId: 7,
        },
      },
    });
    render(<ExtractPage {...props} />);

    expect(
      screen.getByRole("button", { name: /review extracted values/i }),
    ).toBeInTheDocument();
    // B + C: the results screen also leads into the full run report
    // (flag-independent door to the tabbed run detail).
    expect(
      screen.getByRole("button", { name: /open full run report/i }),
    ).toBeInTheDocument();
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
