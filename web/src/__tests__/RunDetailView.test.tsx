import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { RunDetailView } from "../components/RunDetailView";
import type { RunDetailJson, RunAgentJson, SSEEvent } from "../lib/types";

// The run-detail surface is now tabbed (Overview default). Content for
// Agents / Cross-checks / Notes / Telemetry lives behind its tab, so tests
// click the relevant top-level tab first. Scoped to the run-detail tablist
// so it doesn't collide with the Notes-12 sub-tab bar (also role="tab").
function clickRunTab(name: RegExp) {
  const tablist = screen.getByRole("tablist", { name: /run detail sections/i });
  fireEvent.click(within(tablist).getByRole("tab", { name }));
}

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
    // Reset the URL so a `?tab=` written by a prior test (tab selection now
    // mirrors into the query — R3) doesn't leak into the next test's default.
    window.history.replaceState({}, "", "/");
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

  test("per-agent duration sums turn compute time, not the shared batch window", () => {
    // Run-168 QA fix: face/notes agents are batch-stamped, so started_at/
    // ended_at are the same whole-run window for every row. The Activity
    // duration must instead come from each agent's own per-turn compute
    // time — so two agents sharing an identical timestamp window but with
    // different turn totals show DIFFERENT durations.
    const shared = { started_at: "2026-04-10T09:30:00Z", ended_at: "2026-04-10T09:35:58Z" };
    const detail = makeDetail({
      agents: [
        makeAgent({
          id: 1,
          statement_type: "SOFP",
          ...shared,
          // 90s + 30s = 2m 00s of real compute.
          turns: [
            { duration_ms: 90_000 } as never,
            { duration_ms: 30_000 } as never,
          ],
        }),
        makeAgent({
          id: 2,
          statement_type: "SOPL",
          ...shared,
          // 15s of real compute — same window, very different duration.
          turns: [{ duration_ms: 15_000 } as never],
        }),
      ],
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    clickRunTab(/^activity$/i);
    const list = screen.getByTestId("run-detail-agent-list");
    // Summed compute, not the identical 5m 58s window.
    expect(within(list).getByText("2m 00s")).toBeTruthy();
    expect(within(list).getByText("15s")).toBeTruthy();
    expect(within(list).queryByText("5m 58s")).toBeNull();
  });

  test("duration falls back to the timestamp window when no turn telemetry", () => {
    // Legacy rows / the Sheet-12 fan-out parent carry no per-turn data; the
    // timestamp window is the best available signal there.
    const detail = makeDetail({
      agents: [
        makeAgent({
          id: 1,
          started_at: "2026-04-10T09:30:00Z",
          ended_at: "2026-04-10T09:30:45Z",
          turns: [],
        }),
      ],
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    clickRunTab(/^activity$/i);
    expect(
      within(screen.getByTestId("run-detail-agent-list")).getByText("45s"),
    ).toBeTruthy();
  });

  test("Figures tab is gated on canonical mode (peer-review F6)", () => {
    // Default (canonical disabled) → no Figures tab, matching TopNav/Results
    // gating. (The old duplicate "Review values" header button was removed in
    // Phase 2 — the Figures tab is the single door.)
    const { rerender } = render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    const tablist = screen.getByRole("tablist", { name: /run detail sections/i });
    expect(within(tablist).queryByRole("tab", { name: /^figures$/i })).toBeNull();

    // Canonical enabled → a Figures tab appears. The values open in-place as a
    // tab (no /concepts page jump).
    rerender(
      <RunDetailView
        detail={makeDetail()}
        onDelete={() => {}}
        onDownload={() => {}}
        canonicalEnabled
      />,
    );
    const tablist2 = screen.getByRole("tablist", { name: /run detail sections/i });
    expect(within(tablist2).getByRole("tab", { name: /^figures$/i })).toBeTruthy();
  });

  test("Review tab is gated on canonical mode (docs/Archive/PLAN-reviewer-agent.md)", () => {
    // Canonical off → no Review tab.
    const { rerender } = render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    const tablist = screen.getByRole("tablist", { name: /run detail sections/i });
    expect(within(tablist).queryByRole("tab", { name: /^ai review$/i })).toBeNull();

    // Canonical on → Review tab appears, scoped to the run-detail tablist so
    // it never collides with the Notes-12 sub-tab bar (gotcha #7).
    rerender(
      <RunDetailView
        detail={makeDetail()}
        onDelete={() => {}}
        onDownload={() => {}}
        canonicalEnabled
      />,
    );
    const tablist2 = screen.getByRole("tablist", { name: /run detail sections/i });
    expect(within(tablist2).getByRole("tab", { name: /^ai review$/i })).toBeTruthy();
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
    clickRunTab(/^activity$/i);
    const agentsSection = screen.getByTestId("run-detail-agents");
    expect(agentsSection.textContent).toContain("SOFP");
    expect(agentsSection.textContent).toContain("SOPL");
    expect(agentsSection.textContent?.toLowerCase()).toContain("completed");
    expect(agentsSection.textContent?.toLowerCase()).toContain("failed");
  });

  test("failed agent with error_type renders the failure-class badge (item 9)", () => {
    render(
      <RunDetailView
        detail={makeDetail({
          agents: [
            makeAgent(),
            makeAgent({
              id: 2,
              statement_type: "SOPL",
              status: "failed",
              error_type: "token_budget_exceeded",
              workbook_path: null,
            }),
          ],
        })}
        onDownload={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    clickRunTab(/activity/i);
    const badges = screen.getAllByTestId("agent-error-type");
    expect(badges).toHaveLength(1); // only the failed agent carries it
    expect(badges[0].textContent).toBe("token budget exceeded");
  });

  test("succeeded agents render no error_type badge", () => {
    render(
      <RunDetailView
        detail={makeDetail()}
        onDownload={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    clickRunTab(/activity/i);
    expect(screen.queryAllByTestId("agent-error-type")).toHaveLength(0);
  });

  test("renders cross-check table with the sofp_balance check", () => {
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    clickRunTab(/cross-checks/i);
    expect(screen.getByText("sofp_balance")).toBeTruthy();
    expect(screen.getByText("Passed")).toBeTruthy();
  });

  test("clicking a targeted cross-check drives the source-PDF pane (Step 8 integration)", async () => {
    // Regression for the peer-review HIGH: crossChecksForValidator used to
    // drop target_sheet/target_row, so the row was never clickable here.
    const detail = makeDetail({
      cross_checks: [
        {
          name: "sofp_balance",
          status: "failed",
          expected: 100,
          actual: 90,
          diff: 10,
          tolerance: 1,
          message: "off by 10",
          target_sheet: "SOFP-CuNonCu",
          target_row: 30,
        },
      ],
    });
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (url: string) => {
      if (url.includes("/concepts")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            concepts: [
              {
                concept_uuid: "c1",
                render_sheet: "SOFP-CuNonCu",
                render_row: 30,
                evidence: "Page 7, Note 1",
              },
            ],
          }),
        } as Response;
      }
      if (url.includes("/pdf/info")) {
        return { ok: true, status: 200, json: async () => ({ pages: 50 }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    }) as unknown as typeof fetch;
    try {
      render(
        <RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />,
      );
      // Wait for the concept map to load, then click the failed check.
      clickRunTab(/cross-checks/i);
      const row = await screen.findByTestId("cross-check-row-sofp_balance");
      fireEvent.click(row);
      // The pane resolves the target's evidence ("Page 7") and shows page 7.
      const img = (await screen.findByTestId("pdf-page-image")) as HTMLImageElement;
      expect(img.getAttribute("src")).toBe("/api/runs/42/pdf/page/7.png");
    } finally {
      globalThis.fetch = originalFetch;
    }
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

  test("Delete button opens the confirm dialog and fires onDelete on confirm", () => {
    const onDelete = vi.fn<(id: number) => void>();
    render(
      <RunDetailView detail={makeDetail()} onDelete={onDelete} onDownload={() => {}} />,
    );
    // Click the header trigger to open the shared ConfirmDialog…
    fireEvent.click(screen.getByRole("button", { name: /^delete run$/i }));
    // …then confirm inside the dialog (title identifies the modal).
    const dialog = screen.getByRole("dialog", { name: /delete run/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /^delete run$/i }));
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
    clickRunTab(/^activity$/i);
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

  test("Delete button does NOT fire onDelete when the dialog is cancelled", () => {
    const onDelete = vi.fn<(id: number) => void>();
    render(
      <RunDetailView detail={makeDetail()} onDelete={onDelete} onDownload={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^delete run$/i }));
    const dialog = screen.getByRole("dialog", { name: /delete run/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
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
    clickRunTab(/^activity$/i);
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
    // Agent cards default to collapsed — expand each before asserting on
    // timeline contents so the tool rows are mounted.
    clickRunTab(/^activity$/i);
    const agentCards = container.querySelectorAll("[data-testid='run-detail-agent']");
    expect(agentCards.length).toBe(2);
    agentCards.forEach((card) => {
      const toggle = card.querySelector("button");
      if (toggle) fireEvent.click(toggle);
    });
    const toolCards = container.querySelectorAll("[data-testid='tool-card']");
    expect(toolCards.length).toBe(2);
  });

  test("agent with no events shows an empty timeline", () => {
    const { container } = render(
      <RunDetailView
        detail={makeDetail({
          agents: [makeAgent({ events: [] })],
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    // Expand the (default-collapsed) card so the timeline empty-state
    // copy is rendered.
    clickRunTab(/^activity$/i);
    const toggle = container
      .querySelector("[data-testid='run-detail-agent']")
      ?.querySelector("button");
    if (toggle) fireEvent.click(toggle);
    // AgentTimeline's own empty-state copy — proves the timeline is
    // mounted even when the event list is empty. History runs aren't
    // "running", so the copy reflects no recorded activity rather than the
    // misleading "waiting for the agent to start" placeholder (issue 5).
    expect(screen.getByText(/No timeline activity was recorded/i)).toBeInTheDocument();
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
    clickRunTab(/^activity$/i);
    expect(screen.getByText(/Nothing was recorded for this run yet/i)).toBeTruthy();
  });

  // PLAN §4 D.3: history detail renders notes agents alongside face
  // agents. Backend persists notes rows with statement_type prefixed
  // "NOTES_<TEMPLATE>"; the view normalises this to the same friendly
  // chip the live UI uses (peer-review MEDIUM).
  test("notes agents render with friendly labels, not raw DB enum values", () => {
    const detail = makeDetail({
      agents: [
        makeAgent(),
        makeAgent({
          id: 3,
          statement_type: "NOTES_CORP_INFO",
          variant: null,
          status: "succeeded",
        }),
        makeAgent({
          id: 4,
          statement_type: "NOTES_LIST_OF_NOTES",
          variant: null,
          status: "succeeded",
        }),
      ],
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    clickRunTab(/^activity$/i);
    const agentList = screen.getByTestId("run-detail-agent-list");
    expect(within(agentList).getByText("Notes 10: Corp Info")).toBeTruthy();
    expect(within(agentList).getByText("Notes 12: List of Notes")).toBeTruthy();
    // Ensure the raw enum isn't leaking through anywhere.
    expect(screen.queryByText("NOTES_CORP_INFO")).toBeNull();
  });

  test("ConfigBlock surfaces notes_to_run when the run requested any notes", () => {
    const detail = makeDetail({
      config: {
        statements: ["SOFP"],
        variants: {},
        models: {},
        use_scout: false,
        filing_level: "company",
        notes_to_run: ["CORP_INFO", "LIST_OF_NOTES"],
      },
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    // Scope to the active (Overview) tabpanel so the "Notes" tab in the tab
    // bar isn't mistaken for the config dt label.
    const panel = screen.getByRole("tabpanel");
    // dt label present
    expect(within(panel).getByText("Notes")).toBeTruthy();
    // values rendered as the friendly labels, joined
    expect(
      within(panel).getByText(/Notes 10: Corp Info.*Notes 12: List of Notes/),
    ).toBeTruthy();
  });

  test("ConfigBlock omits Notes row when no notes were selected (face-only)", () => {
    const detail = makeDetail({
      config: {
        statements: ["SOFP"],
        variants: {},
        models: {},
        use_scout: false,
        filing_level: "company",
        notes_to_run: [],
      },
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    // No dt "Notes" row added for empty arrays — avoids "Notes: —" noise.
    // Scope to the Overview tabpanel so the "Notes" tab isn't counted.
    const panel = screen.getByRole("tabpanel");
    expect(within(panel).queryByText(/^Notes$/)).toBeNull();
  });

  test("Notes-12 replay renders sub-tab bar derived from persisted events + filters", () => {
    // Live path gets sub-agent ranges from the reducer; replay must derive
    // them from the persisted `started` status events carrying
    // batch_note_range + batch_page_range + sub_agent_id. This locks the
    // live/replay parity contract for sheet-12 sub-tabs.
    const note12Events: SSEEvent[] = [
      {
        event: "status",
        data: {
          phase: "started",
          message: "sub0 starting",
          sub_agent_id: "notes:LIST_OF_NOTES:sub0",
          batch_note_range: [1, 3],
          batch_page_range: [18, 22],
        },
        timestamp: 1,
      } as unknown as SSEEvent,
      {
        event: "status",
        data: {
          phase: "started",
          message: "sub1 starting",
          sub_agent_id: "notes:LIST_OF_NOTES:sub1",
          batch_note_range: [4, 6],
          batch_page_range: [23, 27],
        },
        timestamp: 2,
      } as unknown as SSEEvent,
      {
        event: "tool_call",
        data: {
          tool_name: "find_toc",
          tool_call_id: "notes:LIST_OF_NOTES:sub0:a",
          args: {},
          sub_agent_id: "notes:LIST_OF_NOTES:sub0",
        },
        timestamp: 3,
      } as unknown as SSEEvent,
      {
        event: "tool_call",
        data: {
          tool_name: "view_pages",
          tool_call_id: "notes:LIST_OF_NOTES:sub1:b",
          args: {},
          sub_agent_id: "notes:LIST_OF_NOTES:sub1",
        },
        timestamp: 4,
      } as unknown as SSEEvent,
    ];
    const detail = makeDetail({
      agents: [
        makeAgent({
          id: 9,
          statement_type: "NOTES_LIST_OF_NOTES",
          variant: null,
          events: note12Events,
        }),
      ],
    });

    const { container } = render(
      <RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />,
    );
    // Expand the (default-collapsed) agent card so the sub-tab bar
    // and timeline mount.
    clickRunTab(/^activity$/i);
    const toggle = container
      .querySelector("[data-testid='run-detail-agent']")
      ?.querySelector("button");
    if (toggle) fireEvent.click(toggle);

    // Sub-tab bar appears: "All" chip + one chip per sub-agent (2). Scope to
    // the Sheet-12 sub-tab bar so the run-detail top tabs aren't counted.
    const subTablist = screen.getByRole("tablist", { name: /sheet-12 sub-agents/i });
    const tabs = within(subTablist).getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(tabs[0]).toHaveTextContent(/all/i);

    // All view shows both sub-agents' tool rows.
    expect(screen.getByText(/locating table of contents/i)).toBeInTheDocument();
    expect(screen.getByText(/checking pdf pages/i)).toBeInTheDocument();

    // Click Sub 1 → only sub0's row remains (ranges are ordered first-seen).
    fireEvent.click(tabs[1]);
    expect(screen.getByText(/locating table of contents/i)).toBeInTheDocument();
    expect(screen.queryByText(/checking pdf pages/i)).not.toBeInTheDocument();
  });

  test("Notes-12 replay without started events renders flat timeline (no sub-tab bar)", () => {
    // Guard: a Notes-12 persisted row without sub_agent_id metadata (e.g.
    // coordinator crashed before fan-out) must still render — the sub-tab
    // bar is gated on sub-agent list being non-empty.
    const flatEvents = sampleEvents;
    const detail = makeDetail({
      agents: [
        makeAgent({
          id: 9,
          statement_type: "NOTES_LIST_OF_NOTES",
          variant: null,
          events: flatEvents,
        }),
      ],
    });
    const { container } = render(
      <RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />,
    );
    // Expand the agent card so we're actually testing "sub-tab bar
    // absent after mount" and not just "body not rendered because
    // collapsed".
    clickRunTab(/^activity$/i);
    const toggle = container
      .querySelector("[data-testid='run-detail-agent']")
      ?.querySelector("button");
    if (toggle) fireEvent.click(toggle);

    // No sub-tab bar rendered for this agent.
    expect(screen.queryByRole("tablist", { name: /sheet-12/i })).not.toBeInTheDocument();
  });

  test("history_detail_renders_correction_agent", () => {
    // Run-168 QA fix: a persisted CORRECTION pseudo-agent renders under
    // the product's name for that pass ("AI review", from the central
    // vocabulary) — no raw DB enum leakage, no legacy "Correction".
    const detail = makeDetail({
      agents: [
        makeAgent(),
        makeAgent({
          id: 99,
          statement_type: "CORRECTION",
          variant: null,
          status: "completed",
          workbook_path: null,
        }),
      ],
    });
    render(
      <RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />,
    );
    clickRunTab(/^activity$/i);
    expect(within(screen.getByTestId("run-detail-agent-list")).getByText("AI review")).toBeTruthy();
  });

  test("history_detail_renders_notes_validator_agent", () => {
    // Counterpart: NOTES_VALIDATOR renders as "Notes review".
    const detail = makeDetail({
      agents: [
        makeAgent(),
        makeAgent({
          id: 100,
          statement_type: "NOTES_VALIDATOR",
          variant: null,
          status: "completed",
          workbook_path: null,
        }),
      ],
    });
    render(
      <RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />,
    );
    clickRunTab(/^activity$/i);
    expect(within(screen.getByTestId("run-detail-agent-list")).getByText("Notes review")).toBeTruthy();
  });

  test("Telemetry tab renders per-turn metrics from the agent payload", () => {
    const detail = makeDetail({
      agents: [
        makeAgent({
          token_breakdown: {
            prompt_tokens: 900,
            completion_tokens: 300,
            turn_count: 2,
            tool_call_count: 1,
          },
          turns: [
            {
              turn_index: 1, node_kind: "model_request", tool_names: null,
              prompt_tokens: 800, completion_tokens: 40, total_tokens: 840,
              cumulative_tokens: 840, cost_estimate: 0.004, duration_ms: 1200,
            },
            {
              turn_index: 2, node_kind: "call_tools", tool_names: "read_template",
              prompt_tokens: 100, completion_tokens: 260, total_tokens: 360,
              cumulative_tokens: 1200, cost_estimate: 0.002, duration_ms: 300,
            },
          ],
        }),
      ],
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    clickRunTab(/^activity$/i);
    const panel = screen.getByTestId("run-detail-telemetry");
    // Tool name from a turn row is shown, proving the per-turn table rendered.
    expect(within(panel).getByText("read_template")).toBeTruthy();
    // The on-demand trace button is offered.
    expect(
      within(panel).getByRole("button", { name: /view full request \/ response trace/i }),
    ).toBeTruthy();
  });

  test("Overview metric strip shows the run-level telemetry rollup", () => {
    const detail = makeDetail({
      telemetry_rollup: {
        total_tokens: 2000,
        total_cost: 0.006,
        prompt_tokens: 1700,
        completion_tokens: 300,
        turn_count: 9,
        tool_call_count: 4,
      },
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    // Overview is the default tab — the strip is visible immediately.
    expect(screen.getByText("2,000")).toBeTruthy();
    expect(screen.getByText("$0.0060")).toBeTruthy();
  });

  test("initialTab='values' opens the Values tab (the /concepts/{id} alias)", () => {
    // The /concepts/{id} route now opens the unified run page directly on
    // Values. ConceptsPage fetches on mount, so stub fetch.
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ concepts: [] }),
    })) as unknown as typeof fetch;
    try {
      render(
        <RunDetailView
          detail={makeDetail()}
          onDelete={() => {}}
          onDownload={() => {}}
          canonicalEnabled
          initialTab="values"
        />,
      );
      const tablist = screen.getByRole("tablist", { name: /run detail sections/i });
      const valuesTab = within(tablist).getByRole("tab", { name: /^figures$/i });
      expect(valuesTab.getAttribute("aria-selected")).toBe("true");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("initialTab='values' with canonical OFF falls back to Overview (no blank page)", () => {
    // Peer-review [6]: if the alias requests Values but canonical mode is off
    // or still loading, the tab isn't available — clamp to Overview rather
    // than rendering no active tab and no panel (a blank page).
    render(
      <RunDetailView
        detail={makeDetail()}
        onDelete={() => {}}
        onDownload={() => {}}
        initialTab="values"
      />,
    );
    // A panel IS rendered (not blank), and it's the Overview config.
    const panel = screen.getByRole("tabpanel");
    expect(within(panel).getByText("Run configuration")).toBeTruthy();
    // No Values tab exists (canonical off), so none can be selected.
    const tablist = screen.getByRole("tablist", { name: /run detail sections/i });
    expect(within(tablist).queryByRole("tab", { name: /^figures$/i })).toBeNull();
    // Overview tab is the active one.
    expect(
      within(tablist).getByRole("tab", { name: /^overview$/i }).getAttribute("aria-selected"),
    ).toBe("true");
  });

  test("a ?tab= deep link opens that tab and clicking a tab writes ?tab= (R3)", () => {
    window.history.replaceState({}, "", "/history/1?tab=checks");
    render(<RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />);
    const tablist = screen.getByRole("tablist", { name: /run detail sections/i });
    // The URL param selects the Cross-checks tab on mount.
    expect(
      within(tablist).getByRole("tab", { name: /cross-?checks/i }).getAttribute("aria-selected"),
    ).toBe("true");
    // Switching to Activity mirrors into the query without a full navigation.
    fireEvent.click(within(tablist).getByRole("tab", { name: /activity/i }));
    expect(new URLSearchParams(window.location.search).get("tab")).toBe("agents");
    // The pathname is left untouched (App.tsx owns that).
    expect(window.location.pathname).toBe("/history/1");
  });

  test("arrow keys move between run-detail tabs (WAI-ARIA tabs pattern)", () => {
    render(<RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />);
    const tablist = screen.getByRole("tablist", { name: /run detail sections/i });
    const overviewTab = within(tablist).getByRole("tab", { name: /^overview$/i });
    // ArrowRight from Overview selects + focuses Agents.
    fireEvent.keyDown(overviewTab, { key: "ArrowRight" });
    const agentsTab = within(tablist).getByRole("tab", { name: /^activity$/i });
    expect(agentsTab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("run-detail-agents")).toBeTruthy();
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

  // Gold-standard eval (v16): the Eval tab is gated on the run's benchmark_id.
  test("Eval tab appears only when the run has a benchmark_id, and shows the score", () => {
    // A normal run: no Eval tab in the run-detail tablist.
    const { unmount } = render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    const tablist = screen.getByRole("tablist", { name: /run detail sections/i });
    expect(within(tablist).queryByRole("tab", { name: /^eval$/i })).toBeNull();
    unmount();

    // An eval run: the tab is present and renders the scorecard when clicked.
    render(
      <RunDetailView
        detail={makeDetail({
          benchmark_id: 5,
          eval_score: {
            benchmark_id: 5,
            gold_cells: 473,
            matched_cells: 412,
            missing_cells: 11,
            mismatch_cells: 50,
            extra_cells: 4,
            scale_mismatch: 3,
            score: 412 / 473,
          },
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    const tablist2 = screen.getByRole("tablist", { name: /run detail sections/i });
    const evalTab = within(tablist2).getByRole("tab", { name: /^eval$/i });
    fireEvent.click(evalTab);
    expect(screen.getByTestId("eval-headline").textContent).toBe("87%");
    expect(screen.getByTestId("eval-flags").textContent).toContain("3 scale mismatch");
    expect(screen.getByTestId("eval-flags").textContent).toContain("11 missing");
  });
});
