import { describe, test, expect, vi } from "vitest";
import { act, render, screen, fireEvent } from "@testing-library/react";
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
  test("pre-scan advisories do not interrupt the extraction workspace", () => {
    render(<ExtractPage {...makeProps({ state: {
      isRunning: true, sessionId: "test-session", filename: "test.pdf",
      scoutWarnings: ["Document page hints are incomplete."],
    } })} />);
    expect(screen.queryByTestId("scout-warnings-banner")).toBeNull();
    expect(screen.queryByText("Document page hints are incomplete.")).toBeNull();
    expect(screen.getByRole("button", { name: /stop run/i })).toBeEnabled();
  });

  test("non-blocking document concerns stay out of the live extraction surface", () => {
    const message = "Thousands and millions disagree; scale reset to unknown.";
    render(<ExtractPage {...makeProps({ state: {
      isRunning: true, sessionId: "test-session", filename: "test.pdf",
      scoutWarnings: [message, "Page hints are incomplete."],
      events: [{ event: "scale_conflict", timestamp: 1, data: {
        severity: "coerced", scout_scale_unit: "thousands", resolved_scale_unit: "unknown", message,
      } }],
    } })} />);
    expect(screen.queryByText(/Thousands and millions disagree/)).toBeNull();
    expect(screen.queryByText("Page hints are incomplete.")).toBeNull();
  });

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

    expect(screen.getByRole("button", { name: /stop run/i })).toBeInTheDocument();
  });

  test("the always-visible Stop run action confirms before aborting", () => {
    const props = makeProps({ state: {
      sessionId: "test-session",
      filename: "test.pdf",
      isRunning: true,
    } });
    render(<ExtractPage {...props} />);

    fireEvent.click(screen.getByRole("button", { name: "Stop run" }));
    expect(props.handleAbortAll).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", { name: "Stop this extraction run?" });
    fireEvent.click(dialog.querySelector<HTMLButtonElement>(".pwc-btn-danger")!);
    expect(props.handleAbortAll).toHaveBeenCalledOnce();
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

    // Completion offers one review destination and no competing result tabs.
    expect(
      screen.getByRole("button", { name: /open overview/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /open overview/i }));
    expect(props.onOpenRun).toHaveBeenCalledWith(7);
    expect(screen.queryByRole("button", { name: "Data Preview" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Downloads" })).toBeNull();
  });

  test("completion transitions automatically to the run overview", () => {
    vi.useFakeTimers();
    try {
      const props = makeProps({ state: {
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
      } });
      render(<ExtractPage {...props} />);
      expect(props.onOpenRun).not.toHaveBeenCalled();
      act(() => vi.advanceTimersByTime(600));
      expect(props.onOpenRun).toHaveBeenCalledWith(7);
    } finally {
      vi.useRealTimers();
    }
  });

  test("opening completed activity cancels the automatic overview transition", () => {
    vi.useFakeTimers();
    try {
      const props = makeProps({ state: {
        sessionId: "test-session",
        filename: "test.pdf",
        isComplete: true,
        currentPhase: "complete",
        events: [{ event: "status", data: { message: "SOFP: Complete" }, timestamp: 1 } as never],
        complete: {
          success: true,
          output_path: "",
          excel_path: "/output/x/filled.xlsx",
          trace_path: "",
          total_tokens: 0,
          cost: 0,
          runId: 7,
        },
      } });
      render(<ExtractPage {...props} />);
      fireEvent.click(screen.getByRole("button", { name: /show run activity/i }));
      act(() => vi.advanceTimersByTime(600));
      expect(props.onOpenRun).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: /hide run activity/i })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test.each([
    ["completed", "Completed"],
    ["completed_with_errors", "Completed with errors"],
    ["failed", "Failed"],
    ["aborted", "Aborted"],
  ])("completion names the %s outcome and keeps one review action", (overallStatus, label) => {
    render(<ExtractPage {...makeProps({ state: {
      sessionId: "s", isComplete: true,
      complete: { success: overallStatus === "completed", overallStatus,
        output_path: "", excel_path: "", trace_path: "", total_tokens: 0, cost: 0, runId: 7 },
    } })} />);
    expect(screen.getByRole("heading", { name: label })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Open overview" })).toHaveLength(1);
    expect(screen.queryByText("Extraction finished")).toBeNull();
  });

  test("starting extraction scrolls the long setup form back to progress", () => {
    const main = document.createElement("main");
    main.id = "main-content";
    main.scrollIntoView = vi.fn();
    document.body.appendChild(main);
    try {
      const props = makeProps();
      const { rerender } = render(<ExtractPage {...props} />);
      rerender(<ExtractPage {...props} state={{ ...props.state, isRunning: true }} />);
      expect(main.scrollIntoView).toHaveBeenCalledOnce();
      rerender(<ExtractPage {...props} state={{ ...props.state, isRunning: true, pipelineStage: "extracting" }} />);
      expect(main.scrollIntoView).toHaveBeenCalledOnce();
    } finally { main.remove(); }
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

    expect(screen.queryByRole("button", { name: /stop run/i })).not.toBeInTheDocument();
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

    expect(screen.getByRole("heading", { name: "Extracting selected statements and notes" })).toBeInTheDocument();
    expect(screen.getByTestId("pipeline-stage-label")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByTestId("pipeline-stage-label")).toHaveAttribute("aria-atomic", "true");
    expect(screen.getByLabelText("Workflow progress")).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Run workstreams" })).toHaveAttribute("aria-orientation", "vertical");
    expect(screen.getByRole("tabpanel", { name: /SOFP activity/i })).toBeInTheDocument();
    expect(screen.getByText("0/2 complete")).toBeInTheDocument();
    expect(screen.queryByText(/leave this page/i)).toBeNull();
    const usage = container.querySelector("details") as HTMLDetailsElement;
    expect(usage.open).toBe(false);
    expect(usage.querySelector("summary")?.textContent).toContain("Technical usage details");
    expect(usage.querySelector("summary")?.textContent).toContain("$0.0123");
    expect(usage.querySelector("summary [aria-hidden='true']")).toBeInTheDocument();
  });

  test("completed extraction workers do not hide a running automatic reviewer", () => {
    const extraction = { ...createAgentState("sofp_0", "SOFP", "SOFP"), status: "complete" as const };
    const reviewer = { ...createAgentState("NOTES_VALIDATOR", "NOTES_VALIDATOR", "Notes review"), status: "running" as const };
    render(<ExtractPage {...makeProps({ state: {
      isRunning: true, pipelineStage: "reviewing_notes", statementsInRun: ["SOFP"],
      agents: { sofp_0: extraction, NOTES_VALIDATOR: reviewer },
      agentTabOrder: ["sofp_0", "NOTES_VALIDATOR"],
    } })} />);
    expect(screen.getByText("1/1 complete")).toBeInTheDocument();
    expect(screen.getByText("1 active")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Reviewing extracted notes" })).toBeInTheDocument();
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
    const region = screen.getByRole("status", { name: "Current agent activity" });
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

    const updatedRegion = screen.getByRole("status", { name: "Current agent activity" });
    expect(updatedRegion).toBe(region);
    expect(updatedRegion).toHaveTextContent("Writing figures");
  });

  test("shows bounded coordinator progress without exposing technical timings", () => {
    const props = makeProps({
      state: {
        sessionId: "test-session",
        filename: "scan.pdf",
        isRunning: true,
        pipelineStage: "transcribing_source",
        pipelineActivity: {
          stage: "transcribing_source",
          started_at: 1,
          message: "Reading scanned note pages: 3 of 8 complete.",
          completed: 3,
          total: 8,
        },
      },
    });
    render(<ExtractPage {...props} />);

    expect(screen.getByRole("heading", { name: "Reading scanned note pages: 3 of 8 complete." })).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Current stage progress" })).toHaveAttribute("aria-valuenow", "3");
    expect(screen.queryByText(/30ms/i)).toBeNull();
  });

  test("surfaces notes formatting as live run activity", () => {
    const notesAgent = createAgentState("notes:CORP_INFO", "CORP_INFO", "Notes 10: Corp Info");
    notesAgent.status = "complete";
    render(<ExtractPage {...makeProps({ state: {
      sessionId: "test-session",
      filename: "test.pdf",
      isRunning: true,
      pipelineStage: "formatting_notes",
      pipelineActivity: {
        stage: "formatting_notes",
        started_at: 1,
        message: "Formatting notes: 2 of 3 sections complete",
        completed: 2,
        total: 3,
      },
      activeTab: "notes-formatting",
      agents: { "notes:CORP_INFO": notesAgent },
      agentTabOrder: ["notes:CORP_INFO"],
      notesInRun: ["CORP_INFO"],
    } })} />);

    expect(screen.getByRole("tab", { name: /notes formatting/i })).toBeInTheDocument();
    expect(screen.getByRole("tabpanel", { name: "Notes formatting activity" })).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Notes formatting progress" })).toHaveAttribute("aria-valuenow", "2");
    expect(screen.getAllByText("Formatting notes: 2 of 3 sections complete")).toHaveLength(2);
  });

  test("auto-selects notes formatting once without trapping later tab changes", () => {
    const props = makeProps({ state: {
      sessionId: "test-session",
      filename: "test.pdf",
      isRunning: true,
      pipelineStage: "formatting_notes",
      activeTab: "sofp_0",
      agents: { sofp_0: createAgentState("sofp_0", "SOFP", "SOFP") },
      agentTabOrder: ["sofp_0"],
      notesInRun: ["CORP_INFO"],
    } });
    const { rerender } = render(<ExtractPage {...props} />);
    expect(props.dispatch).toHaveBeenCalledWith({
      type: "SET_ACTIVE_TAB",
      payload: "notes-formatting",
    });

    props.dispatch.mockClear();
    rerender(<ExtractPage {...props} state={{ ...props.state, activeTab: "sofp_0" }} />);
    expect(props.dispatch).not.toHaveBeenCalled();
  });

  test("shows source preparation as a worker without exposing provider reasoning", () => {
    const preparation = createAgentState(
      "source-preparation", "SOURCE_PREPARATION", "Source preparation",
    );
    preparation.status = "complete";
    preparation.reasoningBlocks = [{
      thinking_id: "source-preparation-thinking",
      content: "Located the scanned notes table and preserved its row structure.",
      startedAt: 1,
      endedAt: 2,
      duration_ms: 1,
      isComplete: true,
      kind: "summary",
      provider: "openai",
      model: "gpt-5.6-luna",
      transport: "responses",
    }];
    const props = makeProps({
      state: {
        sessionId: "test-session",
        filename: "scan.pdf",
        isRunning: true,
        pipelineStage: "extracting",
        activeTab: "source-preparation",
        agents: { "source-preparation": preparation },
        agentTabOrder: ["source-preparation"],
        pdfSidecar: { status: "built", pages: 1 },
      },
    });

    render(<ExtractPage {...props} />);

    expect(screen.getByRole("tab", { name: /source preparation/i })).toBeInTheDocument();
    expect(screen.getByRole("tabpanel", { name: /source preparation activity/i })).toBeInTheDocument();
    expect(screen.queryByText(/located the scanned notes table/i)).toBeNull();
    expect(screen.queryByText("Provider reasoning")).toBeNull();
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
    expect(screen.queryByText(/while the run continues/i)).not.toBeInTheDocument();
    expect(screen.getAllByLabelText("Workflow progress")).toHaveLength(1);
    expect(screen.queryByRole("region", { name: "Document preparation" })).toBeNull();
  });
});
