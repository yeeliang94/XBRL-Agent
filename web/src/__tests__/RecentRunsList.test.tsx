import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RecentRunsList } from "../components/RecentRunsList";
import type { RunSummaryJson } from "../lib/types";

function makeRuns(): RunSummaryJson[] {
  return [
    {
      id: 10,
      created_at: "2026-05-20T09:30:00Z",
      pdf_filename: "DRAFT-2024.pdf",
      status: "draft",
      session_id: "sess-10",
      statements_run: [],
      models_used: [],
      duration_seconds: null,
      scout_enabled: false,
      has_merged_workbook: false,
    },
    {
      id: 11,
      created_at: "2026-05-19T15:00:00Z",
      pdf_filename: "FINCO-2023.pdf",
      status: "completed",
      session_id: "sess-11",
      statements_run: ["SOFP"],
      models_used: ["gpt-5.4"],
      duration_seconds: 42,
      scout_enabled: true,
      has_merged_workbook: true,
    },
  ];
}

function makeProps(overrides?: Partial<React.ComponentProps<typeof RecentRunsList>>) {
  return {
    runs: makeRuns(),
    onResumeDraft: vi.fn(),
    onOpenRun: vi.fn(),
    onViewAll: vi.fn(),
    ...overrides,
  };
}

describe("RecentRunsList", () => {
  test("renders a card per run with filename and status", () => {
    render(<RecentRunsList {...makeProps()} />);
    expect(screen.getByText("DRAFT-2024.pdf")).toBeTruthy();
    expect(screen.getByText("FINCO-2023.pdf")).toBeTruthy();
    expect(screen.getByText(/completed/i)).toBeTruthy();
  });

  test("clicking a draft fires onResumeDraft with its id", () => {
    const props = makeProps();
    render(<RecentRunsList {...props} />);
    fireEvent.click(screen.getByText("DRAFT-2024.pdf"));
    expect(props.onResumeDraft).toHaveBeenCalledWith(10);
    expect(props.onOpenRun).not.toHaveBeenCalled();
  });

  test("clicking a non-draft run fires onOpenRun with its id", () => {
    const props = makeProps();
    render(<RecentRunsList {...props} />);
    fireEvent.click(screen.getByText("FINCO-2023.pdf"));
    expect(props.onOpenRun).toHaveBeenCalledWith(11);
    expect(props.onResumeDraft).not.toHaveBeenCalled();
  });

  test("View all fires onViewAll", () => {
    const props = makeProps();
    render(<RecentRunsList {...props} />);
    fireEvent.click(screen.getByText(/view all/i));
    expect(props.onViewAll).toHaveBeenCalled();
  });

  test("empty state invites an upload when there are no runs", () => {
    render(<RecentRunsList {...makeProps({ runs: [] })} />);
    expect(screen.getByText(/no runs yet/i)).toBeTruthy();
  });

  test("error state degrades quietly without throwing", () => {
    render(<RecentRunsList {...makeProps({ runs: [], error: "boom" })} />);
    expect(screen.getByText(/couldn't load recent runs/i)).toBeTruthy();
  });

  test("draft rows offer 'Continue setup' and statuses render monochrome (CS3)", () => {
    render(<RecentRunsList {...makeProps()} />);
    expect(screen.getByText("Continue setup")).toBeInTheDocument();
    expect(screen.queryByText("Resume")).toBeNull();
    // Monochrome status: aria-hidden neutral symbol next to the label.
    const completed = screen.getByText("Completed");
    const symbol = completed.parentElement!.parentElement!.querySelector('[aria-hidden="true"]');
    expect(symbol?.textContent).toBe("\u2713");
    expect((symbol as HTMLElement).style.color).toBe("rgb(94, 94, 94)");
  });
});
