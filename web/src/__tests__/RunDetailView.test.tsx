import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { RunDetailView } from "../components/RunDetailView";
import type { RunDetailJson, RunAgentJson, SSEEvent } from "../lib/types";

// A tool_call / tool_result pair used across the fixture so each agent
// renders a non-empty timeline.
const sampleEvents: SSEEvent[] = [
  {
    event: "tool_call",
    data: {
      tool_name: "read_template",
      tool_call_id: "tc-1",
      args: { path: "/x/01-SOFP-CuNonCu.xlsx" },
    },
    timestamp: 1712830000,
  } as unknown as SSEEvent,
  {
    event: "tool_result",
    data: {
      tool_name: "read_template",
      tool_call_id: "tc-1",
      result_summary: "Loaded template",
      duration_ms: 120,
    },
    timestamp: 1712830001,
  } as unknown as SSEEvent,
];

function makeAgent(overrides: Partial<RunAgentJson> = {}): RunAgentJson {
  return {
    id: 1,
    statement_type: "SOFP",
    variant: "CuNonCu",
    model: "gemini-3-flash-preview",
    status: "completed",
    started_at: "2026-04-10T09:30:00Z",
    ended_at: "2026-04-10T09:31:00Z",
    workbook_path: "/tmp/SOFP_filled.xlsx",
    total_tokens: 1200,
    total_cost: 0.002,
    events: sampleEvents,
    ...overrides,
  };
}

function makeDetail(overrides: Partial<RunDetailJson> = {}): RunDetailJson {
  return {
    id: 42,
    created_at: "2026-04-10T09:30:00Z",
    pdf_filename: "FINCO-Audited-2021.pdf",
    status: "completed",
    session_id: "sess-42",
    output_dir: "/tmp/output/sess-42",
    merged_workbook_path: "/tmp/output/sess-42/filled.xlsx",
    scout_enabled: true,
    started_at: "2026-04-10T09:30:00Z",
    ended_at: "2026-04-10T09:32:00Z",
    config: {
      statements: ["SOFP", "SOPL"],
      variants: { SOFP: "CuNonCu" },
      models: { SOFP: "gemini-3-flash-preview" },
      use_scout: true,
    },
    agents: [
      makeAgent(),
      makeAgent({
        id: 2,
        statement_type: "SOPL",
        variant: "Function",
        status: "failed",
        ended_at: "2026-04-10T09:31:30Z",
        workbook_path: null,
        total_tokens: 800,
        total_cost: 0.001,
      }),
    ],
    cross_checks: [
      {
        name: "sofp_balance",
        status: "passed",
        expected: 100,
        actual: 100,
        diff: 0,
        tolerance: 1,
        message: "OK",
      },
    ],
    ...overrides,
  };
}

describe("RunDetailView", () => {
  beforeEach(() => {
    // jsdom does not implement HTMLDialogElement.showModal, used by <dialog>.
    // Stub confirm() so tests can drive the confirm flow without the dialog.
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  test("renders filename, date, and overall status", () => {
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    expect(screen.getByText("FINCO-Audited-2021.pdf")).toBeTruthy();
    // "Completed" appears in both the overall status badge and the SOFP
    // agent-row status; assert at least one is present.
    expect(screen.getAllByText(/completed/i).length).toBeGreaterThan(0);
  });

  test("renders run config: statements, variants, models, scout flag", () => {
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    // Config block should mention each configured statement / model.
    // SOFP/SOPL also appear in the agents table and/or cross-checks, so
    // use getAllByText to assert presence without asserting uniqueness.
    expect(screen.getAllByText(/SOFP/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/SOPL/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/CuNonCu/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/gemini-3-flash-preview/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/scout/i).length).toBeGreaterThan(0);
  });

  test("renders per-agent status list with both agents", () => {
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    // The agents section should list both SOFP (completed) and SOPL (failed)
    const agentsSection = screen.getByTestId("run-detail-agents");
    expect(agentsSection.textContent).toContain("SOFP");
    expect(agentsSection.textContent).toContain("SOPL");
    expect(agentsSection.textContent?.toLowerCase()).toContain("completed");
    expect(agentsSection.textContent?.toLowerCase()).toContain("failed");
  });

  test("renders cross-check table with the sofp_balance check", () => {
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    expect(screen.getByText("sofp_balance")).toBeTruthy();
    expect(screen.getByText("Passed")).toBeTruthy();
  });

  test("Download button is present and wired to onDownload with run id", () => {
    const onDownload = vi.fn<(id: number) => void>();
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={onDownload} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /download/i }));
    expect(onDownload).toHaveBeenCalledWith(42);
  });

  test("Download button is disabled when no merged workbook", () => {
    render(
      <RunDetailView
        detail={makeDetail({ merged_workbook_path: null })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    const btn = screen.getByRole("button", { name: /download/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  test("Delete button triggers confirm and fires onDelete on confirm", () => {
    const onDelete = vi.fn<(id: number) => void>();
    render(
      <RunDetailView detail={makeDetail()} onDelete={onDelete} onDownload={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /delete/i }));
    expect(window.confirm).toHaveBeenCalled();
    expect(onDelete).toHaveBeenCalledWith(42);
  });

  test("agent row with 'succeeded' status renders as Completed", () => {
    // The coordinator persists per-agent status as "succeeded"
    // (coordinator.py:429), distinct from the run-level "completed".
    // The detail view must render this with a friendly badge, not the
    // raw enum string.
    render(
      <RunDetailView
        detail={makeDetail({
          agents: [
            makeAgent({
              status: "succeeded",
              started_at: null,
              ended_at: null,
              workbook_path: null,
              total_tokens: 100,
              total_cost: 0,
            }),
          ],
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    const agentsSection = screen.getByTestId("run-detail-agents");
    // Friendly label appears
    expect(agentsSection.textContent?.toLowerCase()).toContain("completed");
    // Raw enum does NOT leak into the UI
    expect(agentsSection.textContent).not.toContain("succeeded");
  });

  test("run with 'completed_with_errors' status renders friendly label", () => {
    render(
      <RunDetailView
        detail={makeDetail({ status: "completed_with_errors" })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.getByText(/completed.*with.*errors/i)).toBeTruthy();
  });

  test("Delete button does NOT fire onDelete when confirm is cancelled", () => {
    (window.confirm as ReturnType<typeof vi.fn>).mockReturnValueOnce(false);
    const onDelete = vi.fn<(id: number) => void>();
    render(
      <RunDetailView detail={makeDetail()} onDelete={onDelete} onDownload={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^delete run$/i }));
    expect(onDelete).not.toHaveBeenCalled();
  });

  // Peer-review [CRITICAL] regression: deleting a run whose status is still
  // 'running' cascades through run_agents mid-extraction. The backend now
  // refuses with 409, but we also want the UI to make the bad click
  // impossible in the first place.
  test("Delete button is disabled while the run is still running", () => {
    const onDelete = vi.fn<(id: number) => void>();
    render(
      <RunDetailView
        detail={makeDetail({ status: "running" })}
        onDelete={onDelete}
        onDownload={() => {}}
      />,
    );
    const deleteBtn = screen.getByRole("button", { name: /^delete run$/i }) as HTMLButtonElement;
    expect(deleteBtn.disabled).toBe(true);
    // A disabled button must not fire onClick under any circumstance. Even
    // if the user somehow bypasses the disable (e.g. via devtools), the
    // reducer state doesn't lie — but we still validate the happy path.
    fireEvent.click(deleteBtn);
    expect(onDelete).not.toHaveBeenCalled();
  });

  test("legacy runs (no config captured) show a 'Legacy run' badge", () => {
    // Pre-v2 schema rows were backfilled with NULL config / merged path /
    // token counts. The UI already renders the fallback "No run config
    // captured" text, but the badge gives a clear signal to the user that
    // the gaps are expected and not a data-loss bug.
    render(
      <RunDetailView
        detail={makeDetail({ config: null })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.getByText(/legacy run/i)).toBeTruthy();
  });

  test("new runs (config captured) do NOT show the legacy badge", () => {
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    expect(screen.queryByText(/legacy run/i)).toBeNull();
  });

  test("agent.model rendered as PydanticAI repr is cleaned to the inner id", () => {
    // Legacy rows sometimes stored the raw Model.__repr__() instead of the
    // clean model id. The detail view should strip the wrapper so the UI
    // shows "gemini-3-flash-preview" rather than "GoogleModel(...)".
    render(
      <RunDetailView
        detail={makeDetail({
          agents: [
            makeAgent({
              model:
                "GoogleModel(model_name='gemini-3-flash-preview', provider=GoogleProvider)",
              status: "succeeded",
              started_at: null,
              ended_at: null,
              workbook_path: null,
              total_tokens: 100,
              total_cost: 0,
            }),
          ],
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    const agentsSection = screen.getByTestId("run-detail-agents");
    expect(agentsSection.textContent).toContain("gemini-3-flash-preview");
    expect(agentsSection.textContent).not.toContain("GoogleModel(");
  });

  // Phase 9.1: RunDetailView rebuilds the agents section as a stack of
  // AgentTimeline cards (one per agent) instead of a stats table. Each
  // agent card must render the persisted tool-call events through the
  // same ToolCallCard rows used live.
  test("renders one AgentTimeline per agent with persisted tool cards", () => {
    const { container } = render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    // Two agents → two timeline sections, each with one tool-card for
    // the sample read_template call.
    const agentCards = container.querySelectorAll("[data-testid='run-detail-agent']");
    expect(agentCards.length).toBe(2);
    const toolCards = container.querySelectorAll("[data-testid='tool-card']");
    expect(toolCards.length).toBe(2);
  });

  test("agent with no events shows an empty timeline", () => {
    render(
      <RunDetailView
        detail={makeDetail({
          agents: [makeAgent({ events: [] })],
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    // AgentTimeline's own empty-state copy — proves the timeline is
    // mounted even when the event list is empty.
    expect(screen.getByText(/Waiting for the agent to start/i)).toBeInTheDocument();
  });

  // Phase 9.3: legacy runs have no config AND (often) no agents. The
  // view must not crash and must still report status.
  test("legacy run with no agents and null config renders the legacy badge", () => {
    render(
      <RunDetailView
        detail={makeDetail({ config: null, agents: [] })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.getByText(/legacy run/i)).toBeTruthy();
    expect(screen.getByText(/No agents were recorded/i)).toBeTruthy();
  });

  test("Delete button is enabled for terminal statuses", () => {
    // Sanity check: the disable must NOT bleed into completed / failed /
    // aborted statuses. Each of these represents a terminal run and
    // deletion is allowed.
    for (const status of ["completed", "failed", "aborted"] as const) {
      const { unmount } = render(
        <RunDetailView
          detail={makeDetail({ status })}
          onDelete={() => {}}
          onDownload={() => {}}
        />,
      );
      const deleteBtn = screen.getByRole("button", { name: /^delete run$/i }) as HTMLButtonElement;
      expect(deleteBtn.disabled).toBe(false);
      unmount();
    }
  });
});
