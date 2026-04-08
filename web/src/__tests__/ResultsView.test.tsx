import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ResultsView } from "../components/ResultsView";
import type { CompleteData } from "../lib/types";

const completeData: CompleteData = {
  success: true,
  output_path: "/output/abc/result.json",
  excel_path: "/output/abc/filled.xlsx",
  trace_path: "/output/abc/conversation_trace.json",
  total_tokens: 5000,
  cost: 0.0035,
};

const sampleResultJson = {
  fields: {
    "Total assets": 1500000,
    "Cash and cash equivalents": 250000,
    "Retained earnings": null,
  },
  metadata: { sheet_count: 3 },
};

function renderResults(overrides: Record<string, unknown> = {}) {
  const getResultJson = vi.fn().mockResolvedValue(sampleResultJson);
  return {
    ...render(
      <ResultsView
        complete={{ ...completeData, ...overrides } as CompleteData}
        sessionId="abc"
        runStartTime={Date.now() - 120000}
        getResultJson={getResultJson}
      />,
    ),
    getResultJson,
  };
}

describe("ResultsView — P4", () => {
  test("renders 3 tabs: Summary, Data Preview, Downloads", () => {
    renderResults();
    expect(screen.getByRole("button", { name: /summary/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /data preview/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /downloads/i })).toBeInTheDocument();
  });

  test("defaults to Summary tab", () => {
    renderResults();
    // Summary content should be visible
    expect(screen.getByText(/5,000/)).toBeInTheDocument(); // total tokens
    expect(screen.getByText(/\$0\.0035/)).toBeInTheDocument(); // cost
  });

  test("Summary tab shows total tokens, cost, elapsed time, success badge", () => {
    renderResults();
    expect(screen.getByText(/5,000/)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.0035/)).toBeInTheDocument();
    expect(screen.getByText(/02:00/)).toBeInTheDocument(); // ~120s elapsed
    expect(screen.getByText(/Success/i)).toBeInTheDocument();
  });

  test("Data Preview tab renders table with field name + value columns", async () => {
    const { getResultJson } = renderResults();

    fireEvent.click(screen.getByRole("button", { name: /data preview/i }));

    await waitFor(() => {
      expect(getResultJson).toHaveBeenCalledWith("abc");
    });

    await waitFor(() => {
      expect(screen.getByText("Total assets")).toBeInTheDocument();
      expect(screen.getByText("1500000")).toBeInTheDocument();
    });
  });

  test("Data Preview tab highlights empty/null fields", async () => {
    renderResults();

    fireEvent.click(screen.getByRole("button", { name: /data preview/i }));

    await waitFor(() => {
      // The null "Retained earnings" field should show a dash placeholder
      expect(screen.getByText("Retained earnings")).toBeInTheDocument();
      const dashCells = screen.getAllByText("—");
      expect(dashCells.length).toBeGreaterThan(0);
    });
  });

  test("Downloads tab shows 3 download buttons (Excel, JSON, Trace)", () => {
    renderResults();
    fireEvent.click(screen.getByRole("button", { name: /downloads/i }));
    expect(screen.getByText(/Download Merged Excel/)).toBeInTheDocument();
    expect(screen.getByText(/Download JSON/)).toBeInTheDocument();
    expect(screen.getByText(/Download Trace/)).toBeInTheDocument();
  });

  test("tab switching preserves data (no re-fetch on tab switch)", async () => {
    const { getResultJson } = renderResults();

    // Go to Data Preview to trigger fetch
    fireEvent.click(screen.getByRole("button", { name: /data preview/i }));
    await waitFor(() => expect(getResultJson).toHaveBeenCalledTimes(1));

    // Switch to Downloads
    fireEvent.click(screen.getByRole("button", { name: /downloads/i }));
    // Switch back to Data Preview
    fireEvent.click(screen.getByRole("button", { name: /data preview/i }));

    // Should not have fetched again
    expect(getResultJson).toHaveBeenCalledTimes(1);
  });

  test("Retry button re-fetches after a failed load", async () => {
    // Simulate failure then success to verify Retry actually re-issues the request.
    const getResultJson = vi
      .fn()
      .mockRejectedValueOnce(new Error("Network down"))
      .mockResolvedValueOnce(sampleResultJson);

    render(
      <ResultsView
        complete={completeData}
        sessionId="abc"
        runStartTime={Date.now() - 60000}
        getResultJson={getResultJson}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /data preview/i }));

    // First fetch fails, error + Retry shown
    await waitFor(() => {
      expect(screen.getByText(/Network down/)).toBeInTheDocument();
    });
    expect(getResultJson).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    // Second fetch succeeds and data renders
    await waitFor(() => {
      expect(getResultJson).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(screen.getByText("Total assets")).toBeInTheDocument();
    });
  });

  test("uses PwC theme: orange500 active tab", () => {
    renderResults();
    const summaryBtn = screen.getByRole("button", { name: /summary/i });
    // Active tab should have orange bottom border (#FD5108 → rgb(253, 81, 8))
    expect(summaryBtn.getAttribute("style")).toContain("rgb(253, 81, 8)");
  });
});
