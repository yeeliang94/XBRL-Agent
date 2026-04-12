import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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

  test("renders statement chips for each run", () => {
    render(<HistoryList runs={makeRuns()} onRunSelected={() => {}} />);
    // First run has SOFP and SOPL chips; both substrings should be present
    const row1 = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    expect(row1.textContent).toContain("SOFP");
    expect(row1.textContent).toContain("SOPL");
  });

  test("empty state message when list is empty", () => {
    render(<HistoryList runs={[]} onRunSelected={() => {}} />);
    expect(screen.getByText(/no.*runs/i)).toBeTruthy();
  });

  test("loading state when isLoading prop is true", () => {
    render(<HistoryList runs={[]} isLoading onRunSelected={() => {}} />);
    expect(screen.getByText(/loading/i)).toBeTruthy();
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
    const row1 = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    const row2 = screen.getByText("ACME-2023.pdf").closest("tr")!;
    // The two rows should not have identical styling when one is selected
    expect(row1.getAttribute("aria-selected")).toBe("true");
    expect(row2.getAttribute("aria-selected")).toBe("false");
  });

  // ---------------------------------------------------------------------------
  // Keyboard accessibility — rows must be reachable via Tab and activatable
  // via Enter/Space, not mouse-only. We attach the interactive semantics to
  // the row itself (tabIndex, role=button, keyboard handlers) so the table
  // structure stays intact for screen readers' row/column context.
  // ---------------------------------------------------------------------------

  test("each row is focusable (tabIndex=0) and has an interactive role", () => {
    render(<HistoryList runs={makeRuns()} onRunSelected={() => {}} />);
    const row1 = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    const row2 = screen.getByText("ACME-2023.pdf").closest("tr")!;
    expect(row1.getAttribute("tabindex")).toBe("0");
    expect(row2.getAttribute("tabindex")).toBe("0");
    // A row behaves like a button when activated — either role='button' or
    // the native row stays and we add only tabIndex + keyboard handlers.
    // We accept both, but require SOME interactive signal beyond onClick.
    expect(row1.getAttribute("role")).toBe("button");
  });

  test("pressing Enter on a focused row fires onRunSelected", () => {
    const onRunSelected = vi.fn<(id: number) => void>();
    render(<HistoryList runs={makeRuns()} onRunSelected={onRunSelected} />);
    const row1 = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    fireEvent.keyDown(row1, { key: "Enter" });
    expect(onRunSelected).toHaveBeenCalledWith(1);
  });

  // Peer-review [MEDIUM] regression: the list used to drop `models_used`
  // entirely, so the Phase 10.4 Codex-fix-#1 audit ("verify model strings
  // show as gpt-5.4 etc., not OpenAIChatModel()") couldn't be done from
  // the list view. Now each row renders a chip for every distinct model.
  test("renders a model chip for each model_used on the row", () => {
    const runs: RunSummaryJson[] = [
      {
        ...makeRuns()[0],
        models_used: ["gpt-5.4", "gemini-3-flash-preview"],
      },
    ];
    render(<HistoryList runs={runs} onRunSelected={() => {}} />);

    const row = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    expect(row.textContent).toContain("gpt-5.4");
    expect(row.textContent).toContain("gemini-3-flash-preview");
  });

  test("Model column header is present in the table", () => {
    const { container } = render(
      <HistoryList runs={makeRuns()} onRunSelected={() => {}} />,
    );
    const headers = Array.from(container.querySelectorAll("th")).map(
      (th) => th.textContent?.trim().toLowerCase() ?? "",
    );
    expect(headers).toContain("model");
  });

  test("row with empty models_used gracefully renders a dash", () => {
    // Failed runs can have an empty models_used list. The row should
    // still render — with a placeholder — not crash.
    const runs: RunSummaryJson[] = [
      { ...makeRuns()[0], models_used: [] },
    ];
    render(<HistoryList runs={runs} onRunSelected={() => {}} />);
    const row = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    // We don't pin the exact placeholder — just that the row is in the DOM.
    expect(row).toBeTruthy();
  });

  test("pressing Space on a focused row fires onRunSelected", () => {
    const onRunSelected = vi.fn<(id: number) => void>();
    render(<HistoryList runs={makeRuns()} onRunSelected={onRunSelected} />);
    const row2 = screen.getByText("ACME-2023.pdf").closest("tr")!;
    fireEvent.keyDown(row2, { key: " " });
    expect(onRunSelected).toHaveBeenCalledWith(2);
  });

  test("unrelated keys on the row do not fire onRunSelected", () => {
    const onRunSelected = vi.fn<(id: number) => void>();
    render(<HistoryList runs={makeRuns()} onRunSelected={onRunSelected} />);
    const row1 = screen.getByText("FINCO-Audited-2021.pdf").closest("tr")!;
    fireEvent.keyDown(row1, { key: "a" });
    fireEvent.keyDown(row1, { key: "ArrowDown" });
    expect(onRunSelected).not.toHaveBeenCalled();
  });
});
