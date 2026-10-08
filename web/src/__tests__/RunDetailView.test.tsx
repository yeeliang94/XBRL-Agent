import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, within, waitFor } from "@testing-library/react";
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

function activityRows() {
  return within(screen.getByRole("tablist", { name: "Run workstreams" })).getAllByRole("tab");
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

test.each([
  [null, "Guidance was not recorded for this run."],
  [{ texts: { figures: "" }, revision: 0, updated_by: null, updated_at: null }, "No team guidance was applied."],
  [{ texts: { figures: "Recorded classification guidance" }, revision: 1, updated_by: "Admin", updated_at: null }, "Recorded classification guidance"],
] as const)("run overview shows recorded guidance without substituting current settings: %s", (snapshot, expected) => {
  render(<RunDetailView detail={makeDetail({ agent_instructions: snapshot })} onDelete={() => {}} onDownload={() => {}} />);
  const summary = screen.getByText("Team guidance used");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  fireEvent.click(summary);
  expect(screen.getByText(expected)).toBeVisible();
});

test("unsupported historical filings show a notice and disable redo and rechecks", async () => {
  render(<RunDetailView detail={makeDetail({ filing_standard: undefined, config: { filing_standard: "clbg" } })}
    onDelete={vi.fn()} onDownload={vi.fn()} onRestart={vi.fn()} />);
  await waitFor(() => {
    expect(screen.getByText(/CLBG filings are no longer supported/)).toBeVisible();
    expect(screen.getByRole("button", { name: /redo/i })).toBeDisabled();
  });
  clickRunTab(/cross-checks/i);
  expect(screen.getByRole("button", { name: "Rerun checks" })).toBeDisabled();
});

test("historical CLBG activity keeps income and fund labels", () => {
  render(<RunDetailView detail={makeDetail({ filing_standard: undefined,
    config: { filing_standard: "clbg" }, agents: [
      makeAgent({ id: 1, statement_type: "SOPL", variant: "Nature" }),
      makeAgent({ id: 2, statement_type: "SOCIE", variant: "Default" }),
    ] })} onDelete={vi.fn()} onDownload={vi.fn()} />);
  clickRunTab(/^activity$/i);
  expect(screen.getAllByText("Income and expenditure")[0]).toBeVisible();
  expect(screen.getByText("Changes in funds")).toBeVisible();
  expect(screen.queryByText("Changes in equity")).toBeNull();
});

function makeAgent(overrides: Partial<RunAgentJson> = {}): RunAgentJson {
  return {
    id: 1,
    statement_type: "SOFP",
    variant: "CuNonCu",
    model: "gemini-3-flash-preview",
    // A face agent never lands on "completed" — that is a pseudo-agent status
    // (CORRECTION / NOTES_VALIDATOR). Face rows are succeeded / failed /
    // cancelled / completed_with_errors / skipped, and the incomplete-statement
    // banner keys on exactly that distinction.
    status: "succeeded",
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

  test("run-detail tab wrappers are presentational so the tablist owns its tabs", () => {
    render(<RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />);
    const tablist = screen.getByRole("tablist", { name: /run detail sections/i });
    expect(within(tablist).getAllByRole("presentation")).toHaveLength(
      within(tablist).getAllByRole("tab").length,
    );
  });

  test("renders filename, date, and overall status", () => {
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    expect(screen.getByText("FINCO-Audited-2021.pdf")).toBeTruthy();
    // "Complete" appears in both the overall status badge and the SOFP
    // agent-row status; assert at least one is present.
    expect(screen.getAllByText(/^complete$/i).length).toBeGreaterThan(0);
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
    const list = screen.getByRole("tablist", { name: "Run workstreams" });
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
      within(screen.getByRole("tablist", { name: "Run workstreams" })).getByText("45s"),
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

  test("renders filing-focused run config without advanced model details", () => {
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    // Config block should mention each configured statement / model.
    // SOFP/SOPL also appear in the agents table and/or cross-checks, so
    // use getAllByText to assert presence without asserting uniqueness.
    expect(screen.getAllByText(/SOFP/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/SOPL/).length).toBeGreaterThan(0);
    // Variant renders in plain language now (D2), not the raw "CuNonCu" code.
    expect(screen.getAllByText(/Current \/ Non-current/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/gemini-3-flash-preview/)).toBeNull();
    expect(screen.queryByText(/^scout$/i)).toBeNull();
  });

  test("does not count a successful source transcript as an item needing review", () => {
    render(
      <RunDetailView
        detail={makeDetail({ pdf_sidecar: { status: "built", pages: 20, usage: { in: 56760, out: 13976 } } })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.queryByTestId("items-to-check")).toBeNull();
  });

  test("shows failed transcript pages as an Overview issue when transcription is partial", () => {
    render(
      <RunDetailView
        detail={makeDetail({ pdf_sidecar: { status: "built", pages: 18, partial: true, failed_pages: [12, 15] } })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.getByTestId("items-to-check")).toHaveTextContent("Source transcript has failed pages: 12, 15");
  });

  test("no transcript notice when the pass did not apply", () => {
    render(
      <RunDetailView detail={makeDetail({ pdf_sidecar: null })} onDelete={() => {}} onDownload={() => {}} />,
    );
    expect(screen.queryByTestId("pdf-sidecar-notice")).toBeNull();
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
    expect(agentsSection.textContent?.toLowerCase()).toContain("complete");
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
    const failedRow = activityRows()
      .find((row) => row.textContent?.includes("Profit or loss"));
    expect(failedRow).toBeTruthy();
    fireEvent.click(failedRow!);
    const badges = screen.getAllByTestId("agent-error-type");
    expect(badges).toHaveLength(1); // only the failed agent carries it
    expect(badges[0].textContent).toBe("Token limit reached");
    expect(badges[0]).toHaveAttribute(
      "title",
      "Review large or repeated context in the trace before retrying.",
    );
  });

  test("provider rejection is identified as an operational failure", () => {
    render(<RunDetailView detail={makeDetail({
      agents: [makeAgent({ status: "failed", error_type: "provider_rejected" })],
    })} onDownload={vi.fn()} onDelete={vi.fn()} />);
    clickRunTab(/activity/i);
    fireEvent.click(activityRows()[0]);
    expect(screen.getByTestId("agent-error-type")).toHaveTextContent(
      "Model provider rejected the request",
    );
  });

  test("failed agent renders the exact persisted terminal detail", () => {
    const refusal = "Write to SOCF-Indirect!B137 was blocked because it is formula-owned.";
    render(
      <RunDetailView
        detail={makeDetail({
          agents: [makeAgent({
            statement_type: "SOCF",
            status: "failed",
            error_type: "save_gate_refused",
            error_message: refusal,
          })],
        })}
        onDownload={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    clickRunTab(/activity/i);
    fireEvent.click(activityRows()[0]);
    expect(screen.getByTestId("agent-error-message")).toHaveTextContent(refusal);
  });

  test("failed workstream leads with failure and hides stale completion copy", () => {
    render(<RunDetailView detail={makeDetail({ agents: [makeAgent({
      statement_type: "NOTES_ACC_POLICIES",
      status: "failed",
      error_message: "Notes agent finished without writing any payloads",
      events: [
        { event: "status", data: { message: "ACC_POLICIES: complete" }, timestamp: 1 } as SSEEvent,
        { event: "complete", data: { success: true }, timestamp: 2 } as SSEEvent,
        { event: "error", data: { message: "Notes agent finished without writing any payloads" }, timestamp: 3 } as SSEEvent,
      ],
    })] })} onDelete={() => {}} />);
    clickRunTab(/^activity$/i);
    const detail = screen.getByTestId("run-detail-agent");
    expect(within(detail).getAllByText("Notes agent finished without writing any payloads").length).toBeGreaterThan(0);
    expect(within(detail).queryByText("Finished its assigned work")).toBeNull();
    expect(within(detail).queryByText("ACC_POLICIES: complete")).toBeNull();
    expect(within(detail).getByTestId("agent-error-message")).toHaveTextContent("without writing any payloads");
  });

  test("saved Activity keeps model and usage details in the technical disclosure", () => {
    render(<RunDetailView detail={makeDetail({ agents: [makeAgent({ token_breakdown: {
      turn_count: 2, tool_call_count: 3, prompt_tokens: 900, completion_tokens: 300,
      thinking_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
    } })] })} onDelete={() => {}} />);
    clickRunTab(/^activity$/i);
    const detail = screen.getByTestId("run-detail-agent");
    expect(within(detail).queryByText(/2 turns/)).toBeNull();
    fireEvent.click(within(detail).getByText("Technical activity"));
    expect(within(detail).getByText(/2 turns/)).toBeInTheDocument();
  });

  test("failed notes-review roster shows the specific verification gap, not its internal code", () => {
    render(<RunDetailView detail={makeDetail({ agents: [makeAgent({
      statement_type: "NOTES_VALIDATOR", status: "failed", error_type: "tool_exception", error_message: null,
      events: [
        { event: "error", data: { message: "The notes reviewer left 26 sub-note references unverified. The review remains incomplete." }, timestamp: 1 } as SSEEvent,
        { event: "error", data: { message: "notes_reviewer_subnotes_unverified" }, timestamp: 2 } as SSEEvent,
      ],
    })] })} onDelete={() => {}} />);
    clickRunTab(/^activity$/i);
    expect(activityRows()[0]).toHaveTextContent("26 sub-note references unverified");
    expect(screen.queryByText("notes_reviewer_subnotes_unverified")).toBeNull();
    expect(screen.getByTestId("run-detail-agent")).toHaveTextContent("The notes reviewer left 26 sub-note references unverified");
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
    // Check name renders in plain language (D1); raw id is the title tooltip.
    expect(screen.getByTitle("sofp_balance")).toBeTruthy();
    expect(screen.getByText("Passed")).toBeTruthy();
  });

  test("surfaces a blocked cross-check as work needing attention", () => {
    render(<RunDetailView detail={makeDetail({
      cross_checks: [{ name: "sofp_balance", status: "blocked", expected: null,
        actual: null, diff: null, tolerance: null, message: "SOFP unavailable" }],
    })} onDelete={vi.fn()} onDownload={vi.fn()} />);
    expect(screen.getByText(/waiting for required statement data/i)).toBeInTheDocument();
  });

  test("offers a full redo from the overview without changing the prior run", () => {
    const onRestart = vi.fn();
    render(
      <RunDetailView
        detail={makeDetail()}
        onDelete={vi.fn()}
        onRestart={onRestart}
      />,
    );

    const actions = screen.getByRole("region", { name: "Run actions" });
    expect(actions).toContainElement(screen.getByRole("button", { name: "Redo run" }));
    fireEvent.click(within(actions).getByRole("button", { name: "Redo run" }));

    expect(onRestart).toHaveBeenCalledWith(42);
  });

  test("does not offer redo while the run is active", () => {
    render(<RunDetailView detail={makeDetail({ status: "running" })}
      onDelete={vi.fn()} onRestart={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Redo run" })).toBeNull();
  });

  test("refreshes cross-checks when the same run receives a newer snapshot", async () => {
    const first = makeDetail({ cross_checks: [] });
    const { rerender } = render(
      <RunDetailView detail={first} onDelete={() => {}} onDownload={() => {}} />,
    );
    clickRunTab(/cross-checks/i);
    expect(screen.queryByTestId("cross-check-row-sofp_balance")).toBeNull();

    rerender(
      <RunDetailView
        detail={makeDetail()}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("cross-check-row-sofp_balance")).toBeTruthy(),
    );
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
          comparands: [
            { label: "Total assets", sheet: "SOFP-CuNonCu", statement: "SOFP", role: "lhs", period: "CY", value: 100 },
            { label: "Total assets", sheet: "SOFP-CuNonCu", statement: "SOFP", role: "lhs", period: "PY", value: 80 },
          ],
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
      expect(within(row).getByRole("columnheader", { name: "Previous year" })).toBeInTheDocument();
      expect(within(row).getByRole("cell", { name: /^80$/ })).toBeInTheDocument();
      fireEvent.click(within(row).getByRole("button", { name: "Review figures" }));
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
    fireEvent.click(screen.getByRole("button", { name: /prepare mtool draft/i }));
    expect(onDownload).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Prepare mTool draft" })).toBeTruthy();
  });

  test("Draft preparation does not require the old merged workbook", () => {
    render(
      <RunDetailView
        detail={makeDetail({ merged_workbook_path: null })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    const btn = screen.getByRole("button", { name: /prepare mtool draft/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  test("Delete button opens the confirm dialog and fires onDelete on confirm", () => {
    const onDelete = vi.fn<(id: number) => void>();
    render(
      <RunDetailView detail={makeDetail()} onDelete={onDelete} onDownload={() => {}} />,
    );
    // Click the header trigger to open the shared ConfirmDialog…
    fireEvent.click(screen.getByRole("button", { name: /^delete run$/i }));
    // …then confirm inside the dialog (title identifies the modal).
    const dialog = screen.getByRole("dialog", { name: /delete this run/i });
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
    expect(agentsSection.textContent?.toLowerCase()).toContain("complete");
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
    expect(screen.getAllByText("Needs review").length).toBeGreaterThan(0);
  });

  test("Delete button does NOT fire onDelete when the dialog is cancelled", () => {
    const onDelete = vi.fn<(id: number) => void>();
    render(
      <RunDetailView detail={makeDetail()} onDelete={onDelete} onDownload={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^delete run$/i }));
    const dialog = screen.getByRole("dialog", { name: /delete this run/i });
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

  test("older runs identify their limited historical details", () => {
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
    expect(screen.getByText(/limited historical details/i)).toBeTruthy();
  });

  test("new runs (config captured) do NOT show the legacy badge", () => {
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    expect(screen.queryByText(/limited historical details/i)).toBeNull();
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

  test("uses a roster and focused detail while keeping technical events on demand", () => {
    render(
      <RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />,
    );
    clickRunTab(/^activity$/i);
    expect(activityRows()).toHaveLength(2);
    const selected = activityRows()[0];
    const panel = screen.getByTestId("run-detail-agent");
    expect(selected).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", selected.id);
    expect(panel).toHaveAccessibleName();
    expect(screen.getAllByTestId("run-detail-agent")).toHaveLength(1);
    const technicalActivity = screen.getByText("Technical activity").closest("details");
    expect(technicalActivity).not.toHaveAttribute("open");
    expect(within(technicalActivity!).queryByTestId("tool-card")).toBeNull();
    fireEvent.click(screen.getByText("Technical activity"));
    expect(technicalActivity).toHaveAttribute("open");
    expect(within(technicalActivity!).getByTestId("tool-card")).toBeInTheDocument();
    fireEvent.click(activityRows()[1]);
    const nextTab = activityRows()[1];
    const nextPanel = screen.getByTestId("run-detail-agent");
    expect(nextTab).toHaveAttribute("aria-controls", nextPanel.id);
    expect(nextPanel).toHaveAttribute("aria-labelledby", nextTab.id);
    expect(nextPanel).toHaveAccessibleName();
    expect(screen.getAllByTestId("run-detail-agent")).toHaveLength(1);
  });

  test("source summary uses the numeric page range, not event arrival order", () => {
    const detail = makeDetail({
      agents: [
        makeAgent({
          events: [
            {
              event: "status",
              data: { message: "Read page 14" },
              timestamp: 1,
            } as SSEEvent,
            {
              event: "status",
              data: { message: "Compared with page 3" },
              timestamp: 2,
            } as SSEEvent,
          ],
        }),
      ],
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    clickRunTab(/^activity$/i);

    expect(screen.getAllByText("Source pages 3–14").length).toBeGreaterThan(0);
    expect(screen.queryByText("Source pages 14–3")).toBeNull();
  });

  test("groups extraction and review while showing terminal attention beside active cleanup", () => {
    render(<RunDetailView detail={makeDetail({ status: "running", agents: [
      makeAgent({ id: 1, statement_type: "SOFP", status: "succeeded" }),
      makeAgent({ id: 2, statement_type: "NOTES_ACC_POLICIES", status: "succeeded" }),
      makeAgent({ id: 3, statement_type: "CORRECTION", status: "completed_with_errors" }),
      makeAgent({ id: 4, statement_type: "NOTES_CLEANUP", status: "running" }),
    ] })} onDelete={() => {}} />);
    clickRunTab(/^activity$/i);
    const workstreams = screen.getByRole("tablist", { name: "Run workstreams" });
    expect(within(workstreams).getByText("Financial statements")).toBeVisible();
    expect(within(workstreams).getByText("Notes")).toBeVisible();
    expect(within(workstreams).getByText("Run checks")).toBeVisible();
    const review = within(workstreams).getByRole("tab", { name: /AI review/ });
    expect(review).toHaveTextContent("Needs review");
    expect(review).not.toHaveTextContent("Working");
    expect(within(workstreams).getByRole("tab", { name: /Notes cleanup/ })).toHaveTextContent("Working");
    fireEvent.click(review);
    expect(screen.getByRole("region", { name: "Recorded activity" })).toBeVisible();
    fireEvent.keyDown(review, { key: "ArrowDown" });
    expect(within(workstreams).getByRole("tab", { name: /Notes cleanup/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("region", { name: "Live activity" })).toBeVisible();
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
    clickRunTab(/^activity$/i);
    fireEvent.click(screen.getByText("Technical activity"));
    // AgentTimeline's own empty-state copy — proves the timeline is
    // mounted even when the event list is empty. History runs aren't
    // "running", so the copy reflects no recorded activity rather than the
    // misleading "waiting for the agent to start" placeholder (issue 5).
    expect(screen.getByText(/No timeline activity was recorded/i)).toBeInTheDocument();
  });

  test("saved Activity retains observed formatting and cleanup outcomes through polling", () => {
    const detail = makeDetail({ status: "running", agents: [
      makeAgent({ statement_type: "CORRECTION", status: "completed" }),
    ], run_events: [
      { event: "pipeline_stage", data: { stage: "formatting_notes", message: "Formatting 4 note sections." }, timestamp: 1 },
      { event: "pipeline_stage", data: { stage: "cleaning_notes", completed: 1, total: 4, message: "Checking notes: 1 of 4 fields." }, timestamp: 2 },
    ] });
    const { rerender } = render(<RunDetailView detail={detail} onDelete={vi.fn()} />);
    clickRunTab(/^activity$/i);
    const workstreams = screen.getByRole("tablist", { name: "Run workstreams" });
    expect(within(workstreams).getByRole("tab", { name: /AI review/ })).toHaveTextContent("Complete");
    expect(within(workstreams).getByRole("tab", { name: /Notes formatting/ })).toHaveTextContent("Complete");
    fireEvent.click(within(workstreams).getByRole("tab", { name: /Notes cleanup/ }));
    expect(within(screen.getByRole("list", { name: "Activity updates" })).getByText("Checking notes: 1 of 4 fields.")).toBeVisible();
    rerender(<RunDetailView detail={{ ...detail, status: "completed_with_errors", run_events: [
      ...detail.run_events!, { event: "error", data: { type: "notes_cleanup_incomplete", message: "Notes cleanup did not finish." }, timestamp: 3 },
    ] }} onDelete={vi.fn()} />);
    expect(within(workstreams).getByRole("tab", { name: /Notes cleanup/ })).toHaveAttribute("aria-selected", "true");
    expect(within(workstreams).getByRole("tab", { name: /Notes cleanup/ })).toHaveTextContent("Failed");
    expect(screen.getByRole("region", { name: "Recorded activity" })).toBeVisible();
  });

  // Phase 9.3: legacy runs have no config AND (often) no agents. The
  // view must not crash and must still report status.
  test("legacy run with no agents and null config explains its limited details", () => {
    render(
      <RunDetailView
        detail={makeDetail({ config: null, agents: [] })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.getByText(/limited historical details/i)).toBeTruthy();
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
    const agentList = screen.getByRole("tablist", { name: "Run workstreams" });
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

    render(
      <RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />,
    );
    clickRunTab(/^activity$/i);
    fireEvent.click(screen.getByText("Technical activity"));

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
    render(
      <RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />,
    );
    clickRunTab(/^activity$/i);
    fireEvent.click(screen.getByText("Technical activity"));

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
    expect(within(screen.getByRole("tablist", { name: "Run workstreams" })).getByText("AI review")).toBeTruthy();
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
    expect(within(screen.getByRole("tablist", { name: "Run workstreams" })).getByText("Notes review")).toBeTruthy();
  });

  test("Telemetry tab renders per-turn metrics from the agent payload", () => {
    const detail = makeDetail({
      agents: [
        makeAgent({
          token_breakdown: {
            prompt_tokens: 900,
            completion_tokens: 300,
            thinking_tokens: 60,
            turn_count: 2,
            tool_call_count: 1,
          },
          turns: [
            {
              turn_index: 1, node_kind: "model_request", tool_names: null,
              prompt_tokens: 800, completion_tokens: 40, total_tokens: 840,
              thinking_tokens: 20,
              cumulative_tokens: 840, cost_estimate: 0.004, duration_ms: 1200,
            },
            {
              turn_index: 2, node_kind: "call_tools", tool_names: "read_template",
              prompt_tokens: 100, completion_tokens: 260, total_tokens: 360,
              thinking_tokens: 40,
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
    expect(within(panel).getByRole("columnheader", { name: "Reasoning" })).toBeTruthy();
    expect(within(panel).getByText(/60 reasoning tokens/i)).toBeTruthy();
    // The on-demand trace button is offered.
    expect(
      within(panel).getByRole("button", { name: /view full request \/ response trace/i }),
    ).toBeTruthy();
  });

  test("a cost from a placeholder rate is labelled as an estimate", () => {
    // The registry has carried `pricing_unconfirmed` since the GPT-5.6 models
    // were added, and nothing displayed it — so a guessed rate rendered in the
    // same shape as a published one. GPT-5.6 Luna was priced at the 5.5 tier
    // and overstated cost 25x while looking authoritative (2026-08-03).
    const detail = makeDetail({
      agents: [makeAgent({ model: "gpt-5.6-luna", pricing_unconfirmed: true })],
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    clickRunTab(/^activity$/i);
    const panel = screen.getByTestId("run-detail-telemetry");
    expect(within(panel).getByText(/\(est\. rate\)/i)).toBeTruthy();
  });

  test("a cost from a published rate carries no estimate label", () => {
    const detail = makeDetail({
      agents: [makeAgent({ model: "gpt-5.4", pricing_unconfirmed: false })],
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    clickRunTab(/^activity$/i);
    const panel = screen.getByTestId("run-detail-telemetry");
    expect(within(panel).queryByText(/\(est\. rate\)/i)).toBeNull();
  });

  test("Activity disclosure shows the run-level telemetry rollup", () => {
    const detail = makeDetail({
      telemetry_rollup: {
        total_tokens: 2000,
        total_cost: 0.006,
        prompt_tokens: 1700,
        completion_tokens: 300,
        thinking_tokens: 75,
        turn_count: 9,
        tool_call_count: 4,
      },
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    fireEvent.click(screen.getByRole("tab", { name: /activity/i }));
    fireEvent.click(screen.getByText(/performance details/i));
    expect(screen.getByText("2,000")).toBeTruthy();
    expect(screen.getByText("Reasoning tokens")).toBeTruthy();
    expect(screen.getByText("75")).toBeTruthy();
    // Cost is rounded to cents now, not shown to 4 decimals ($0.0060 → $0.01).
    expect(screen.getByText("$0.01")).toBeTruthy();
  });

  test("Overview leads with workbook readiness and elapsed time", () => {
    const detail = makeDetail({
      agents: [makeAgent()],
      cross_checks: [
        { name: "sofp_balance", status: "passed", expected: 1, actual: 1, diff: 0, tolerance: 1, message: "" },
        { name: "socf_articulation", status: "failed", expected: 2, actual: 1, diff: 1, tolerance: 1, message: "" },
      ],
      telemetry_rollup: {
        total_tokens: 2000, total_cost: 0.01, prompt_tokens: 1700,
        completion_tokens: 300, turn_count: 9, tool_call_count: 4,
      },
    });
    const { container } = render(
      <RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />,
    );
    expect(screen.getByText("Workbook")).toBeTruthy();
    expect(screen.getByText("Checks need attention")).toBeTruthy();
    expect(screen.getByText("Elapsed time")).toBeTruthy();
    expect(screen.getByText("2m 00s")).toBeTruthy();
    const warning = screen.getByRole("alert");
    expect(warning).toHaveTextContent(/cash-flow movements/i);
    expect(screen.queryByTestId("items-to-check")).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
    expect(container.textContent).not.toContain("Total tokens");
    fireEvent.click(screen.getByRole("tab", { name: /activity/i }));
    expect(screen.getByText(/performance details/i)).toBeTruthy();
  });

  test.each(["completed", "completed_with_errors"])("%s Overview directs the operator to verify figures against the source", (status) => {
    render(<RunDetailView detail={makeDetail({ status, agents: [makeAgent()] })} onDelete={() => {}} canonicalEnabled />);
    expect(screen.getByText("Verify extracted figures against the source PDF before filing.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review figures" }));
    expect(within(screen.getByRole("tablist", { name: /run detail sections/i })).getByRole("tab", { name: "Figures" })).toHaveAttribute("aria-selected", "true");
  });

  test("notes-only Overview does not ask for figure verification", () => {
    render(<RunDetailView detail={makeDetail({
      config: { ...makeDetail().config!, statements: [], notes_to_run: ["CORP_INFO"] },
      agents: [],
    })} onDelete={() => {}} canonicalEnabled />);
    expect(screen.queryByRole("button", { name: "Review figures" })).toBeNull();
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
      expect(screen.getByTestId("run-detail-values")).toBeInTheDocument();
      expect(screen.getByRole("tablist", { name: /run detail sections/i })).toBeInTheDocument();
      // Run management and output setup stay on Overview. The review surface
      // keeps the workbook download but drops unrelated destructive/tools UI.
      expect(screen.queryByRole("button", { name: /fill mtool template/i })).toBeNull();
      expect(screen.queryByRole("button", { name: /delete run/i })).toBeNull();
      expect(screen.getByRole("button", { name: /prepare mtool draft/i })).toBeTruthy();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("Notes lazy-mounts the unified workspace directly in Notes mode", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/notes_cells")) {
        return new Response(JSON.stringify({ sheets: [] }), { status: 200 });
      }
      if (url.includes("/notes-coverage")) {
        return new Response(JSON.stringify({
          run_id: 42,
          banner: "pre_feature",
          inventory_available: true,
          rows: [],
          summary: { placed: 0, missing: 0, skipped: 0, suspected_gap: 0, total: 0, unresolved: 0 },
        }), { status: 200 });
      }
      if (url.includes("/facts/edited_count")) {
        return new Response(JSON.stringify({ count: 0 }), { status: 200 });
      }
      if (url.includes("/conflicts")) {
        return new Response(JSON.stringify({ conflicts: [] }), { status: 200 });
      }
      return new Response(JSON.stringify({ concepts: [] }), { status: 200 });
    }) as unknown as typeof fetch;
    try {
      render(
        <RunDetailView
          detail={makeDetail()}
          onDelete={() => {}}
          onDownload={() => {}}
          canonicalEnabled
          initialTab="notes"
        />,
      );
      expect(screen.getByTestId("review-notes-panel")).toBeInTheDocument();
      expect(screen.getByRole("tablist", { name: /run detail sections/i })).toBeInTheDocument();
      expect(screen.queryByTestId("sheet-nav-__notes__")).toBeNull();
      expect(screen.queryByTestId("run-detail-values")).toBeNull();
      await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/runs/42/concepts",
        expect.any(Object),
      ));
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("Notes review keeps inventory-unavailable coverage visible without expanding audit details", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/notes-coverage")) {
        return new Response(JSON.stringify({
          run_id: 42,
          banner: "inventory_unavailable",
          inventory_available: false,
          rows: [],
          summary: { placed: 0, missing: 0, skipped: 0, suspected_gap: 0, total: 0, unresolved: 0 },
        }), { status: 200 });
      }
      if (url.includes("/notes_integrity")) {
        return new Response(JSON.stringify({
          run_id: 42,
          state: "legacy",
          mode: null,
          notes: [],
          summary: null,
          findings: [],
          input_kind: null,
        }), { status: 200 });
      }
      if (url.includes("/notes_tables")) {
        return new Response(JSON.stringify({
          run_id: 42,
          tables: [],
          summary: { tables: 0, plain: 0, styled: 0, source: 0, flagged: 0, cells_with_tables: 0 },
        }), { status: 200 });
      }
      if (url.includes("/notes-review/status")) {
        return new Response(JSON.stringify({ status: "done" }), { status: 200 });
      }
      if (url.includes("/notes-review")) {
        return new Response(JSON.stringify({
          run_id: 42,
          has_reviewer_version: false,
          diff: [],
          flags: [],
        }), { status: 200 });
      }
      if (url.includes("/api/settings")) {
        return new Response(JSON.stringify({
          model: "",
          available_models: [],
          default_models: {},
        }), { status: 200 });
      }
      if (url.includes("/notes_cells")) {
        return new Response(JSON.stringify({ sheets: [] }), { status: 200 });
      }
      if (url.includes("/conflicts")) {
        return new Response(JSON.stringify({ conflicts: [] }), { status: 200 });
      }
      return new Response(JSON.stringify({ concepts: [] }), { status: 200 });
    }) as unknown as typeof fetch;
    try {
      render(
        <RunDetailView
          detail={makeDetail()}
          onDelete={() => {}}
          onDownload={() => {}}
          canonicalEnabled
          initialTab="notes"
        />,
      );
      expect(
        await screen.findByTestId("coverage-banner-inventory_unavailable"),
      ).toBeInTheDocument();
      expect(screen.getByRole("region", { name: "Source note inventory" })).toContainElement(screen.getByTestId("coverage-banner-inventory_unavailable"));
      expect(screen.queryByTestId("notes-coverage-panel")).not.toBeInTheDocument();
      expect(screen.getByText("Notes audit details").closest("details")).not.toHaveAttribute("open");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("heavy Figures and Notes workspaces stay unmounted on Overview", () => {
    render(
      <RunDetailView
        detail={makeDetail()}
        onDelete={() => {}}
        onDownload={() => {}}
        canonicalEnabled
      />,
    );
    expect(screen.queryByTestId("concepts-page")).toBeNull();
    expect(screen.queryByTestId("run-detail-notes-review")).toBeNull();
    expect(screen.queryByTestId("run-detail-values")).toBeNull();
  });

  test.each(["values", "notes"] as const)(
    "%s review omits the repeated run-wide warning",
    async (initialTab) => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/notes-coverage")) {
          return new Response(JSON.stringify({
            run_id: 42,
            banner: "pre_feature",
            inventory_available: true,
            rows: [],
            summary: { placed: 0, missing: 0, skipped: 0, suspected_gap: 0, total: 0, unresolved: 0 },
          }), { status: 200 });
        }
        if (url.includes("/notes_cells")) {
          return new Response(JSON.stringify({ sheets: [] }), { status: 200 });
        }
        if (url.includes("/conflicts")) {
          return new Response(JSON.stringify({ conflicts: [] }), { status: 200 });
        }
        if (url.includes("edited_count")) {
          return new Response(JSON.stringify({ count: 0 }), { status: 200 });
        }
        return new Response(JSON.stringify({ concepts: [] }), { status: 200 });
      }) as unknown as typeof fetch;
      try {
        render(
          <RunDetailView
            detail={makeDetail({
              status: "completed_with_errors",
              agents: [makeAgent()],
              cross_checks: [{
                name: "sofp_balance",
                status: "failed",
                expected: 100,
                actual: 90,
                diff: 10,
                tolerance: 1,
                message: "Mismatch",
              }],
            })}
            onDelete={() => {}}
            onDownload={() => {}}
            canonicalEnabled
            initialTab={initialTab}
          />,
        );
        expect(screen.queryByTestId("review-run-warning")).toBeNull();
      } finally {
        globalThis.fetch = originalFetch;
      }
    },
  );

  test("Figures review warns when one statement did not finish", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/conflicts")) {
        return new Response(JSON.stringify({ conflicts: [] }), { status: 200 });
      }
      if (url.includes("edited_count")) {
        return new Response(JSON.stringify({ count: 0 }), { status: 200 });
      }
      return new Response(JSON.stringify({ concepts: [] }), { status: 200 });
    }) as unknown as typeof fetch;
    try {
      render(
        <RunDetailView
          detail={makeDetail({
            status: "completed",
            agents: [makeAgent(), makeAgent({ id: 2, statement_type: "SOCF", status: "failed" })],
          })}
          onDelete={() => {}}
          onDownload={() => {}}
          canonicalEnabled
          initialTab="values"
        />,
      );
      expect(await screen.findByTestId("review-incomplete-warning")).toHaveTextContent(
        /cannot be filed/i,
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("Cross-checks can rerun checks against the current saved figures", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/recheck")) {
        return new Response(JSON.stringify({
          results: [
            { name: "sofp_balance", status: "passed", expected: 100, actual: 100, diff: 0, tolerance: 1, message: "OK" },
            { name: "socf_articulation", status: "warning", expected: null, actual: null, diff: null, tolerance: null, message: "Advisory" },
          ],
        }), { status: 200 });
      }
      return new Response(JSON.stringify({ concepts: [] }), { status: 200 });
    }) as unknown as typeof fetch;
    try {
      render(<RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />);
      clickRunTab(/^cross-checks$/i);
      fireEvent.click(screen.getByTestId("recheck-btn"));
      expect(await screen.findByTestId("recheck-summary")).toHaveTextContent(
        "1 passed · 0 failed · 0 blocked · 1 warnings",
      );
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/runs/42/recheck",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("switching run IDs remounts the Figures workspace", () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ concepts: [] }),
    })) as unknown as typeof fetch;
    try {
      const props = {
        onDelete: () => {},
        onDownload: () => {},
        canonicalEnabled: true,
        initialTab: "values" as const,
      };
      const { rerender } = render(<RunDetailView detail={makeDetail()} {...props} />);
      fireEvent.click(screen.getByTestId("col-hide-pdf"));
      expect(screen.getByTestId("col-show-pdf")).toBeInTheDocument();

      rerender(<RunDetailView detail={makeDetail({ id: 43 })} {...props} />);
      expect(screen.getByTestId("col-hide-pdf")).toBeInTheDocument();
      expect(screen.queryByTestId("col-show-pdf")).toBeNull();
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

  test("switching saved-run tabs returns a scrolled page to the new panel", () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    Object.defineProperty(window, "scrollY", { configurable: true, value: 700 });
    try {
      render(<RunDetailView detail={makeDetail()} onDelete={() => {}} />);
      vi.spyOn(screen.getByTestId("run-tab-anchor"), "getBoundingClientRect")
        .mockReturnValue({ top: -700 } as DOMRect);
      clickRunTab(/cross-checks/i);
      expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "auto" });
      expect(screen.getByTitle("sofp_balance")).toBeInTheDocument();
    } finally {
      Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });
    }
  });

  test("arrow keys move between run-detail tabs (WAI-ARIA tabs pattern)", () => {
    render(<RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />);
    const tablist = screen.getByRole("tablist", { name: /run detail sections/i });
    const overviewTab = within(tablist).getByRole("tab", { name: /^overview$/i });
    // With canonical mode off, ArrowLeft from Overview wraps to Activity.
    fireEvent.keyDown(overviewTab, { key: "ArrowLeft" });
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

  // Human-file comparison (docs/human-mtool-file-comparison-plan.md, Step 7):
  // only a finished run can be compared.
  test("Compare with human file is offered on completed runs only", () => {
    const { unmount } = render(
      <RunDetailView detail={makeDetail({ status: "completed" })} onDelete={() => {}} onDownload={() => {}} />,
    );
    expect(screen.getByRole("button", { name: "Compare with human file" })).toBeInTheDocument();
    unmount();
    render(
      <RunDetailView detail={makeDetail({ status: "draft" })} onDelete={() => {}} onDownload={() => {}} />,
    );
    expect(screen.queryByRole("button", { name: "Compare with human file" })).toBeNull();
  });

  test("completed_with_errors surfaces a failed check as a blocking warning", () => {
    render(
      <RunDetailView
        detail={makeDetail({
          status: "completed_with_errors",
          // All-succeeded agents so the single alert under test is the
          // cross-check banner, not the incomplete-statement one.
          agents: [makeAgent()],
          cross_checks: [
            {
              name: "sofp_balance",
              status: "failed",
              expected: 100,
              actual: 90,
              diff: 10,
              tolerance: 1,
              message: "assets vs equity+liab",
            },
          ],
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    const warning = screen.getByRole("alert");
    // The human-readable check name appears, not the raw id.
    expect(warning.textContent).toMatch(/balance/i);
    expect(warning.textContent).toMatch(/expected 100.*actual 90.*difference 10/i);
    expect(screen.queryByTestId("items-to-check")).toBeNull();
    const download = screen.getByRole("button", { name: /prepare mtool draft/i });
    expect(download.className).toMatch(/secondary/i);
  });

  test("an extraction issue routes to Activity when no consistency check failed", () => {
    render(<RunDetailView detail={makeDetail({ status: "completed_with_errors", cross_checks: [], agents: [makeAgent({ status: "completed_with_errors" })] })}
      onDelete={() => {}} onDownload={() => {}} />);
    const items = screen.getByTestId("items-to-check");
    expect(items).toHaveTextContent("Extraction or review finished with issues.");
    expect(screen.queryByText(/consistency check didn.t pass/i)).toBeNull();
    expect(screen.queryByRole("button", { name: "View cross-checks" })).toBeNull();
    fireEvent.click(within(items).getByRole("button", { name: "View activity" }));
    expect(screen.getByTestId("run-detail-agents")).toBeInTheDocument();
  });

  test("the workbook preparation action remains available from review tools", () => {
    render(
      <RunDetailView
        detail={makeDetail({ status: "completed_with_errors", agents: [makeAgent()] })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: /fill mtool template/i })).toBeNull();
    expect(screen.getAllByRole("button", { name: /prepare mtool draft/i })).toHaveLength(1);
    fireEvent.click(screen.getByRole("tab", { name: /cross-checks/i }));
    expect(screen.queryByRole("button", { name: /fill mtool template/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /delete run/i })).toBeNull();
    expect(screen.getByRole("button", { name: /prepare mtool draft/i })).toBeTruthy();
  });

  test.each(["failed", "aborted"] as const)("%s run explains template preparation before opening the picker", (status) => {
    render(<RunDetailView detail={makeDetail({ status })} onDelete={() => {}} />);
    expect(screen.queryByText(/partial workbook was preserved/i)).toBeNull();
    expect(screen.getByText(/Saved figures may be incomplete/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Prepare investigation draft" }));
    const confirmation = screen.getByRole("dialog");
    expect(confirmation).toHaveTextContent("choose an mTool template to fill with the saved figures");
    fireEvent.click(within(confirmation).getByRole("button", { name: "Choose mTool template" }));
    expect(screen.getByRole("dialog", { name: "Prepare mTool draft" })).toBeInTheDocument();
  });

  test("flagged run confirms before downloading an investigation draft", () => {
    const onDownload = vi.fn();
    render(
      <RunDetailView
        detail={makeDetail({ status: "completed_with_errors" })}
        onDelete={() => {}}
        onDownload={onDownload}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /prepare mtool draft/i }));
    expect(screen.getByRole("dialog").textContent).toMatch(/not ready to file/i);
    expect(onDownload).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: /^choose mtool template$/i }));
    expect(onDownload).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Prepare mTool draft" })).toBeTruthy();
  });

  test("clean completed run shows no warning banner and primary workbook preparation", () => {
    render(
      // Explicitly all-succeeded: the shared fixture carries a FAILED SOPL,
      // which is a legitimate warning condition of its own.
      <RunDetailView
        detail={makeDetail({ agents: [makeAgent()] })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
    const download = screen.getByRole("button", { name: /prepare mtool draft/i });
    expect(download.className).toMatch(/primary/i);
  });

  test("does not announce a failed check as final while automatic review is still running", () => {
    render(
      <RunDetailView
        detail={makeDetail({
          status: "running",
          cross_checks: [{
            name: "sofp_balance",
            status: "failed",
            expected: 100,
            actual: 90,
            diff: 10,
            tolerance: 1,
            message: "review running",
          }],
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.queryByTestId("failed-check-warning")).toBeNull();
    expect(screen.getByText("Not ready yet")).toBeInTheDocument();
  });

  // --- UX-QA #2: abort control for a wedged running run ---
  test("running run keeps Abort visible and Delete disabled, and confirms before aborting", () => {
    const onForceAbort = vi.fn();
    render(
      <RunDetailView
        detail={makeDetail({ status: "running", merged_workbook_path: null })}
        onDelete={() => {}}
        onDownload={() => {}}
        onForceAbort={onForceAbort}
      />,
    );
    expect(screen.getByRole("button", { name: /delete run/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /stop run/i }));
    // Confirm dialog gates the action — its confirm button shares the label, so
    // click the last "Stop run" button (the dialog's, not the header trigger).
    const abortButtons = screen.getAllByRole("button", { name: /^stop run$/i });
    fireEvent.click(abortButtons[abortButtons.length - 1]);
    expect(onForceAbort).toHaveBeenCalledWith(42);
  });

  test("working run Overview shows what is working, a completion count and a ticking time", () => {
    render(
      <RunDetailView
        detail={makeDetail({
          status: "running",
          merged_workbook_path: null,
          started_at: new Date(Date.now() - 65_000).toISOString(),
          ended_at: null,
          agents: [
            makeAgent({ id: 1, statement_type: "SOFP", status: "succeeded" }),
            makeAgent({ id: 2, statement_type: "SOPL", status: "running" }),
            makeAgent({ id: 3, statement_type: "SOCF", status: "running" }),
          ],
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    const summary = screen.getByTestId("live-run-summary");
    expect(within(summary).getByRole("status")).toHaveTextContent(/^Working on .+ and .+$/);
    expect(summary).toHaveTextContent("1 of 3 workstreams complete");
    // Elapsed time counts from the start instead of showing a dash.
    expect(screen.getByText(/^1m 0[5-9]s$/)).toBeInTheDocument();
    fireEvent.click(within(summary).getByRole("button", { name: "View activity" }));
    expect(screen.getByTestId("run-detail-agents")).toBeInTheDocument();
  });

  // UX-QA #14: Activity lists statements in reading order, not backend order.
  test("Activity orders agents scout → SOFP → SOPL → SOCI → SOCIE → SOCF", () => {
    const detail = makeDetail({
      agents: [
        makeAgent({ id: 1, statement_type: "SOCF" }),
        makeAgent({ id: 2, statement_type: "SCOUT" }),
        makeAgent({ id: 3, statement_type: "SOFP" }),
        makeAgent({ id: 4, statement_type: "SOPL" }),
      ],
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    clickRunTab(/activity/i);
    const rows = activityRows();
    const order = rows.map((row) => row.textContent);
    // Document scan first, then face statements in reading order (SOCF last).
    expect(order[0]).toMatch(/document preparation/i);
    expect(order[1]).toMatch(/Statement of financial position/);
    expect(order[2]).toMatch(/Profit or loss/);
    expect(order[3]).toMatch(/Cash flows/);
  });

  test("Activity gives source preparation a plain-language label", () => {
    const detail = makeDetail({
      agents: [makeAgent({ id: 1, statement_type: "SOURCE_PREPARATION" })],
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    clickRunTab(/activity/i);

    const row = activityRows()[0];
    expect(row).toHaveTextContent("Source preparation");
    expect(row).not.toHaveTextContent("SOURCE_PREPARATION");
  });

  test("advisory checks render as one compact review action", () => {
    const detail = makeDetail({
      // "warning" is a runtime advisory status the outcomes logic compares as a
      // string; the typed union doesn't list it, so cast the fixture.
      cross_checks: [
        { name: "sofp_balance", status: "passed", expected: 1, actual: 1, diff: 0, tolerance: 1, message: "OK" },
        { name: "notes_consistency", status: "warning" as never, expected: null, actual: null, diff: null, tolerance: null, message: "advisory" },
      ],
    });
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    const items = screen.getByTestId("items-to-check");
    expect(items).toHaveTextContent("advisory");
    fireEvent.click(within(items).getByRole("button", { name: "View cross-checks" }));
    expect(screen.getByRole("tab", { name: "Cross-checks" })).toHaveAttribute("aria-selected", "true");
  });

  test("running run without onForceAbort falls back to the disabled Delete", () => {
    render(
      <RunDetailView
        detail={makeDetail({ status: "running", merged_workbook_path: null })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    const del = screen.getByRole("button", { name: /delete run/i });
    expect(del).toBeDisabled();
    expect(screen.queryByRole("button", { name: /abort run/i })).toBeNull();
  });

  test("run status is monochrome; active tab uses a quiet surface without an indicator line (CS6)", () => {
    render(
      <RunDetailView detail={makeDetail({ status: "completed" })} onDelete={() => {}} onDownload={() => {}} />,
    );
    // Monochrome status: aria-hidden ✓ in grey700 next to the explicit label.
    const label = screen.getAllByText("Complete")[0];
    const symbol = label.parentElement!.querySelector('[aria-hidden="true"]');
    expect(symbol?.getAttribute("data-status-icon")).toBe("success");
    expect((symbol as HTMLElement).style.color).toBe("rgb(0, 0, 0)");

    // Shared tab treatment: dark active text and a quiet selected surface.
    const tablist = screen.getByRole("tablist", { name: /run detail sections/i });
    const active = within(tablist)
      .getAllByRole("tab")
      .find((t) => t.getAttribute("aria-selected") === "true") as HTMLElement;
    expect(active.style.color).toBe("rgb(0, 0, 0)");
    expect(active.style.background).toBe("rgb(245, 247, 248)");
    expect(active.style.borderBottom).toBe("");
  });
});

// --- Run-84: a statement that stopped early must not pass as complete ---
describe("incomplete face statements", () => {
  test("a capped statement warns even when the RUN reports completed", () => {
    // The exact run-84 shape: run status `completed`, one face agent capped.
    // Every other banner keys on run status, so this one has to key on its own
    // condition or the page reads as a clean extraction.
    render(
      <RunDetailView
        detail={makeDetail({
          status: "completed",
          agents: [
            makeAgent(),
            makeAgent({ id: 2, statement_type: "SOCF", variant: "Indirect",
                        status: "failed" }),
          ],
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    const banner = screen.getByRole("alert");
    expect(banner.textContent).toMatch(/did not finish extracting/i);
    expect(banner.textContent).toMatch(/Statement of Cash Flows/i);
    expect(banner.textContent).toMatch(/cannot be filed/i);
  });

  test("running statement agents are not described as failed or unfileable", () => {
    render(
      <RunDetailView
        detail={makeDetail({
          status: "running",
          agents: [
            makeAgent({ status: "running" }),
            makeAgent({ id: 2, statement_type: "SOCF", status: "running" }),
          ],
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.queryByText(/did not finish extracting/i)).toBeNull();
    expect(screen.queryByText(/cannot be filed/i)).toBeNull();
  });

  test("overview exposes the filing name as the page heading", () => {
    const detail = makeDetail();
    render(<RunDetailView detail={detail} onDelete={() => {}} onDownload={() => {}} />);
    const heading = screen.getByRole("heading", { level: 1, name: detail.pdf_filename });
    expect(heading).toHaveStyle({ fontSize: "28px" });
    clickRunTab(/^activity$/i);
    expect(heading).toHaveStyle({ fontSize: "28px" });
  });

  test("a skipped statement is not reported as unfinished", () => {
    // `skipped` = a NotPrepared variant with no template to fill. A legitimate
    // non-outcome, not a half-read statement.
    render(
      <RunDetailView
        detail={makeDetail({
          status: "completed",
          agents: [
            makeAgent(),
            makeAgent({ id: 2, statement_type: "SOCI", status: "skipped" }),
          ],
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("a failed NOTES template does not report a missing face statement", () => {
    render(
      <RunDetailView
        detail={makeDetail({
          status: "completed",
          agents: [
            makeAgent(),
            makeAgent({ id: 2, statement_type: "NOTES_LIST_OF_NOTES",
                        status: "failed" }),
          ],
        })}
        onDelete={() => {}}
        onDownload={() => {}}
      />,
    );
    const alerts = screen.queryAllByRole("alert");
    expect(alerts.every((a) => !/did not finish extracting/i.test(a.textContent ?? "")))
      .toBe(true);
  });
});

test("failed runs without agents or output can export diagnostics and retry a failed download", async () => {
  let exportRequests = 0;
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    if (url !== "/api/runs/42/diagnostics") return new Response("null", { headers: { "Content-Type": "application/json" } });
    exportRequests += 1;
    return exportRequests === 1
      ? new Response(JSON.stringify({ detail: "Export could not be prepared." }), { status: 500 })
      : new Response("zip", { headers: { "Content-Type": "application/zip" } });
  });
  const createUrl = vi.fn(() => "blob:diagnostics");
  const previousCreate = URL.createObjectURL;
  const previousRevoke = URL.revokeObjectURL;
  URL.createObjectURL = createUrl;
  URL.revokeObjectURL = vi.fn();
  const save = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  try {
    window.history.replaceState(null, "", "/history/1?tab=overview");
    render(<RunDetailView detail={makeDetail({ status: "failed", agents: [], output_dir: "", merged_workbook_path: null })} onDelete={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Export diagnostics" })).toBeNull();
    clickRunTab(/^activity$/i);
    expect(screen.getByRole("button", { name: "Export diagnostics" })).toBeEnabled();
    expect(screen.getByText(/May contain financial content/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Export diagnostics" }));
    const activity = screen.getByTestId("run-detail-agents");
    await waitFor(() => expect(within(activity).getByRole("alert")).toBeVisible());
    expect(screen.getByRole("button", { name: "Export diagnostics" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Export diagnostics" }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(exportRequests).toBe(2);
    expect(within(activity).queryByRole("alert")).toBeNull();
  } finally {
    cleanup();
    fetchMock.mockRestore();
    save.mockRestore();
    URL.createObjectURL = previousCreate;
    URL.revokeObjectURL = previousRevoke;
  }
});
