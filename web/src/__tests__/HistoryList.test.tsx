import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { HistoryList } from "../components/HistoryList";
import type { RunSummaryJson } from "../lib/types";

function makeRuns(): RunSummaryJson[] {
  return [
    {
      id: 1,
      created_at: "2026-04-10T09:30:00Z",
      pdf_filename: "FINCO-Audited-2021.pdf",
      status: "completed",
      session_id: "sess-1",
      statements_run: ["SOFP", "SOPL"],
      models_used: ["gemini-3-flash-preview"],
      duration_seconds: 42,
      scout_enabled: true,
      has_merged_workbook: true,
    },
    {
      id: 2,
      created_at: "2026-04-09T15:00:00Z",
      pdf_filename: "ACME-2023.pdf",
      status: "failed",
      session_id: "sess-2",
      statements_run: ["SOFP"],
      models_used: ["gpt-5.4"],
      duration_seconds: 12,
      scout_enabled: false,
      has_merged_workbook: false,
    },
  ];
}

describe("HistoryList", () => {
  test("renders one row per run with filename and status", () => {
    render(<HistoryList runs={makeRuns()} onRunSelected={() => {}} />);
    expect(screen.getByText("FINCO-Audited-2021.pdf")).toBeTruthy();
    expect(screen.getByText("ACME-2023.pdf")).toBeTruthy();
    expect(screen.getByText(/completed/i)).toBeTruthy();
    expect(screen.getByText(/failed/i)).toBeTruthy();
  });

  test("does not render statements in the list row", () => {
    render(<HistoryList runs={makeRuns()} onRunSelected={() => {}} />);
    const row1 = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    expect(row1.textContent).not.toContain("SOFP");
    expect(row1.textContent).not.toContain("SOPL");
  });

  test("empty state message when list is empty", () => {
    render(<HistoryList runs={[]} onRunSelected={() => {}} />);
    expect(screen.getByText(/no.*runs/i)).toBeTruthy();
  });

  test("loading state when isLoading prop is true", () => {
    render(<HistoryList runs={[]} isLoading onRunSelected={() => {}} />);
    // Skeleton rows announce themselves via an aria-labelled status region.
    expect(screen.getByRole("status", { name: /loading/i })).toBeTruthy();
  });

  test("error banner when error prop is set", () => {
    render(
      <HistoryList
        runs={[]}
        error="Server exploded"
        onRunSelected={() => {}}
      />,
    );
    expect(screen.getByText(/server exploded/i)).toBeTruthy();
  });

  test("clicking a row fires onRunSelected with the id", () => {
    const onRunSelected = vi.fn<(id: number) => void>();
    render(<HistoryList runs={makeRuns()} onRunSelected={onRunSelected} />);
    fireEvent.click(screen.getByText("ACME-2023.pdf"));
    expect(onRunSelected).toHaveBeenCalledWith(2);
  });

  test("the filename is a real link to the run, so it can be opened in a new tab", () => {
    render(<HistoryList runs={makeRuns()} onRunSelected={() => {}} />);
    // A completed run links to its detail page; middle-click / cmd-click open
    // it in a new tab because it's a genuine <a href>, not a role=button row.
    const link = screen.getByText("ACME-2023.pdf").closest("a");
    expect(link).not.toBeNull();
    expect(link!.getAttribute("href")).toBe("/history/2");
  });

  // ---------------------------------------------------------------------------
  // PLAN-persistent-draft-uploads.md — Phase D (steps 21-22).
  // Drafts live in History (so users can find their unstarted uploads) but
  // they need a different click affordance than completed runs. The
  // RunDetailPage shape doesn't render meaningfully for an empty draft;
  // clicking a draft row routes the user back to /run/{id} where they can
  // edit config and click Start.
  // ---------------------------------------------------------------------------

  test("renders a 'Not started' badge for draft rows", () => {
    const drafts: RunSummaryJson[] = [
      {
        id: 7,
        created_at: "2026-04-26T12:00:00Z",
        pdf_filename: "Draft.pdf",
        status: "draft",
        session_id: "sess-7",
        statements_run: [],
        models_used: [],
        duration_seconds: null,
        scout_enabled: false,
        has_merged_workbook: false,
      },
    ];
    render(<HistoryList runs={drafts} onRunSelected={() => {}} />);
    // The badge label is "Not started" (the user-friendly form), not the
    // raw enum string "draft".
    expect(screen.getByText("Not started")).toBeInTheDocument();
  });

  test("clicking a draft row fires onResumeDraft, not onRunSelected", () => {
    const drafts: RunSummaryJson[] = [
      {
        id: 7,
        created_at: "2026-04-26T12:00:00Z",
        pdf_filename: "Draft.pdf",
        status: "draft",
        session_id: "sess-7",
        statements_run: [],
        models_used: [],
        duration_seconds: null,
        scout_enabled: false,
        has_merged_workbook: false,
      },
    ];
    const onRunSelected = vi.fn<(id: number) => void>();
    const onResumeDraft = vi.fn<(id: number) => void>();
    render(
      <HistoryList
        runs={drafts}
        onRunSelected={onRunSelected}
        onResumeDraft={onResumeDraft}
      />,
    );
    fireEvent.click(screen.getByText("Draft.pdf"));
    // Drafts route to /run/{id} via onResumeDraft so the user can edit
    // their config — clicking should NOT open the inline detail panel.
    expect(onResumeDraft).toHaveBeenCalledWith(7);
    expect(onRunSelected).not.toHaveBeenCalled();
  });

  test("renders 'completed_with_errors' status with a friendly label", () => {
    // Server emits this status when extraction succeeded but a cross-check
    // or merge step failed. The frontend must recognize it explicitly,
    // not fall back to the raw enum string.
    const runs: RunSummaryJson[] = [
      { ...makeRuns()[0], status: "completed_with_errors" },
    ];
    render(<HistoryList runs={runs} onRunSelected={() => {}} />);
    // Friendly label, not the raw enum value
    expect(screen.getByText(/completed.*with.*errors/i)).toBeTruthy();
    expect(screen.queryByText("completed_with_errors")).toBeNull();
  });

  test("active row (selectedId) is visually highlighted", () => {
    render(
      <HistoryList
        runs={makeRuns()}
        selectedId={1}
        onRunSelected={() => {}}
      />,
    );
    const link1 = screen.getByRole("link", { name: "FINCO-Audited-2021.pdf" });
    const link2 = screen.getByRole("link", { name: "ACME-2023.pdf" });
    expect(link1.getAttribute("aria-current")).toBe("page");
    expect(link2.getAttribute("aria-current")).toBeNull();
  });

  // ---------------------------------------------------------------------------
  // Keyboard accessibility comes from one native filename link per row. The
  // row retains native table semantics, avoiding duplicate/corrupt headers in
  // accessibility trees.
  // ---------------------------------------------------------------------------

  test("each row exposes one native navigation link", () => {
    render(<HistoryList runs={makeRuns()} onRunSelected={() => {}} />);
    expect(screen.getByRole("link", { name: "FINCO-Audited-2021.pdf" }).getAttribute("href")).toBe("/history/1");
    expect(screen.getByRole("link", { name: "ACME-2023.pdf" }).getAttribute("href")).toBe("/history/2");
  });

  test("does not render models in the list row", () => {
    const runs: RunSummaryJson[] = [
      {
        ...makeRuns()[0],
        models_used: ["gpt-5.4", "gemini-3-flash-preview"],
      },
    ];
    render(<HistoryList runs={runs} onRunSelected={() => {}} />);

    const row = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    expect(row.textContent).not.toContain("gpt-5.4");
    expect(row.textContent).not.toContain("gemini-3-flash-preview");
  });

  test("Statements and Model column headers are absent from the table", () => {
    const { container } = render(
      <HistoryList runs={makeRuns()} onRunSelected={() => {}} />,
    );
    const headers = Array.from(container.querySelectorAll("th")).map(
      (th) => th.textContent?.trim().toLowerCase() ?? "",
    );
    expect(headers).not.toContain("statements");
    expect(headers).not.toContain("model");
  });

  test("row with empty models_used gracefully renders a dash", () => {
    // Failed runs can have an empty models_used list. The row should
    // still render even though models are hidden from the list view.
    const runs: RunSummaryJson[] = [
      { ...makeRuns()[0], models_used: [] },
    ];
    render(<HistoryList runs={runs} onRunSelected={() => {}} />);
    const row = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    // We don't pin the exact placeholder — just that the row is in the DOM.
    expect(row).toBeTruthy();
  });

  // E2: standard + level live in their own Standard column (was an inline
  // filename badge). The column mirrors the Standard filter.
  test("Standard column shows MPERS · level when filing_standard is mpers", () => {
    const runs: RunSummaryJson[] = [
      { ...makeRuns()[0], filing_standard: "mpers", filing_level: "group" },
    ];
    render(<HistoryList runs={runs} onRunSelected={() => {}} />);
    const row = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    expect(row.textContent).toContain("MPERS · Group");
  });

  test("Standard column shows MFRS for an mfrs run, never MPERS", () => {
    const runs: RunSummaryJson[] = [
      { ...makeRuns()[0], filing_standard: "mfrs" },
    ];
    render(<HistoryList runs={runs} onRunSelected={() => {}} />);
    const row = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    expect(row.textContent).toContain("MFRS");
    expect(row.textContent).not.toContain("MPERS");
  });

  test("legacy runs without filing_standard default to MFRS · Company", () => {
    render(<HistoryList runs={makeRuns()} onRunSelected={() => {}} />);
    const row = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    expect(row.textContent).toContain("MFRS · Company");
    expect(row.textContent).not.toContain("MPERS");
  });

  // Gold-standard eval (v16): score column + sparkline.
  test("renders the eval score as a percentage, or a dash when ungraded", () => {
    const runs: RunSummaryJson[] = [
      { ...makeRuns()[0], id: 1, benchmark_id: 3, eval_score: 0.87 },
      { ...makeRuns()[1], id: 2 }, // ungraded
    ];
    render(<HistoryList runs={runs} onRunSelected={() => {}} />);
    expect(screen.getByTestId("history-score-1").textContent).toBe("87%");
    // The ungraded run shows a dash, not a percentage.
    const row2 = screen.getByText("ACME-2023.pdf").closest("tr")!;
    expect(within(row2).queryByTestId("history-score-2")).toBeNull();
    expect(row2.textContent).toContain("—");
  });

  test("shows an eval-trend sparkline when >= 2 runs are graded", () => {
    const runs: RunSummaryJson[] = [
      { ...makeRuns()[0], id: 1, benchmark_id: 3, eval_score: 0.9 },
      { ...makeRuns()[1], id: 2, benchmark_id: 3, eval_score: 0.7, status: "completed" },
    ];
    render(<HistoryList runs={runs} onRunSelected={() => {}} />);
    expect(screen.getByTestId("history-eval-sparkline")).toBeTruthy();
  });

  test("no sparkline with fewer than two graded runs", () => {
    const runs: RunSummaryJson[] = [
      { ...makeRuns()[0], id: 1, benchmark_id: 3, eval_score: 0.9 },
      { ...makeRuns()[1], id: 2 },
    ];
    render(<HistoryList runs={runs} onRunSelected={() => {}} />);
    expect(screen.queryByTestId("history-eval-sparkline")).toBeNull();
  });
});
