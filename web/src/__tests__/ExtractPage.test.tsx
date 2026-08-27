import { describe, test, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ExtractPage } from "../pages/ExtractPage";
import { initialState } from "../lib/appReducer";
import { createAgentState } from "../lib/types";
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
  test("idle work queue exposes a local New extraction action", () => {
    render(<ExtractPage {...makeProps()} />);
    expect(screen.getByRole("heading", { name: "Work queue" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New extraction" })).toBeInTheDocument();
  });

  test("a resumed draft is headed as setup rather than Work queue", () => {
    render(<ExtractPage {...makeProps({ state: { currentRunId: 42, sessionId: "draft-session", sessionRunId: 42, filename: "draft.pdf" } })} />);
    expect(screen.getByRole("heading", { name: "Continue setup" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Work queue" })).toBeNull();
  });

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

    // One review door (Phase 2): a single "Open run report" button leads into
    // the tabbed run detail (whose Figures tab replaces the old separate
    // "Review extracted values" action).
    expect(
      screen.getByRole("button", { name: /open run report/i }),
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

  test("live run uses one progress surface and a grouped workstream workspace", () => {
    const agent = createAgentState("sofp_0", "SOFP", "SOFP");
    agent.status = "running";
    agent.currentPhase = "viewing_pdf";
    const props = makeProps({
      state: {
        sessionId: "test-session",
        filename: "test.pdf",
        isRunning: true,
        currentPhase: "viewing_pdf",
        pipelineStage: "extracting",
        agents: { sofp_0: agent },
        agentTabOrder: ["sofp_0"],
        activeTab: "sofp_0",
        statementsInRun: ["SOFP", "SOPL"],
        tokens: {
          prompt_tokens: 100,
          completion_tokens: 20,
          thinking_tokens: 0,
          cumulative: 120,
          cost_estimate: 0.0123,
        },
      },
    });
    const { container } = render(<ExtractPage {...props} />);

    expect(screen.getByRole("heading", { name: /agents are working in parallel/i })).toBeInTheDocument();
    expect(screen.getByTestId("pipeline-stage-label")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByTestId("pipeline-stage-label")).toHaveAttribute("aria-atomic", "true");
    expect(screen.getByLabelText("Extraction progress")).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Run workstreams" })).toHaveAttribute("aria-orientation", "vertical");
    expect(screen.getByRole("tabpanel", { name: /SOFP activity/i })).toBeInTheDocument();
    expect(screen.getByText("0 of 2 complete")).toBeInTheDocument();
    const usage = container.querySelector("details") as HTMLDetailsElement;
    expect(usage.open).toBe(false);
    expect(usage.querySelector("summary")?.textContent).toContain("$0.0123");
    expect(usage.querySelector("summary [aria-hidden='true']")).toBeInTheDocument();
  });

  test("keeps the live status region mounted while its message changes", () => {
    const first = createAgentState("sofp_0", "SOFP", "SOFP");
    first.status = "running";
    first.events = [
      { event: "status", data: { message: "Reading page 3" }, timestamp: 1 } as never,
    ];
    const base = {
      sessionId: "test-session",
      filename: "test.pdf",
      isRunning: true,
      activeTab: "sofp_0",
      agentTabOrder: ["sofp_0"],
      agents: { sofp_0: first },
    };
    const { rerender } = render(<ExtractPage {...makeProps({ state: base })} />);
    const region = screen.getByRole("status", { name: "Latest agent update" });
    expect(region).toHaveTextContent("Reading page 3");

    const second = {
      ...first,
      events: [
        ...first.events,
        { event: "status", data: { message: "Writing figures" }, timestamp: 2 } as never,
      ],
    };
    rerender(
      <ExtractPage
        {...makeProps({ state: { ...base, agents: { sofp_0: second } } })}
      />,
    );

    const updatedRegion = screen.getByRole("status", { name: "Latest agent update" });
    expect(updatedRegion).toBe(region);
    expect(updatedRegion).toHaveTextContent("Writing figures");
  });

  test("a stopped run is not described as still running", () => {
    const props = makeProps({
      state: {
        sessionId: "test-session",
        filename: "test.pdf",
        isRunning: false,
        isComplete: false,
        hasError: true,
        currentPhase: "viewing_pdf",
        pipelineStage: "extracting",
        tokens: {
          prompt_tokens: 100,
          completion_tokens: 20,
          thinking_tokens: 0,
          cumulative: 120,
          cost_estimate: 0.0123,
        },
      },
    });
    render(<ExtractPage {...props} />);

    expect(screen.getByRole("heading", { name: "Run stopped" })).toBeInTheDocument();
    expect(screen.getByText(/the run is no longer active/i)).toBeInTheDocument();
    expect(screen.queryByText(/while the run continues/i)).not.toBeInTheDocument();
  });
});
