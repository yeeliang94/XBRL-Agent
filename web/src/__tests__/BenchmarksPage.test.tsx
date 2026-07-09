import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { BenchmarksPage } from "../pages/BenchmarksPage";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

function mockFetch(impl: (url: string, init?: RequestInit) => unknown) {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
    async (url: string, init?: RequestInit) => {
      const result = impl(url, init);
      return { ok: true, status: 200, json: async () => result } as Response;
    }
  );
}

const sampleList = {
  benchmarks: [
    {
      id: 1,
      name: "FINCO 2021 MFRS Company",
      document: "FINCO.pdf",
      filing_standard: "mfrs",
      filing_level: "company",
      created_at: "2026-06-04Z",
      statements: ["SOFP", "SOPL"],
      gold_cell_count: 42,
    },
  ],
};

describe("BenchmarksPage", () => {
  test("lists benchmarks with their gold-cell count", async () => {
    mockFetch((url) => {
      if (url === "/api/benchmarks") return sampleList;
      return {};
    });
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    expect(await screen.findByText("FINCO 2021 MFRS Company")).toBeTruthy();
    expect(screen.getByText("42 gold cells")).toBeTruthy();
    expect(screen.getByText(/SOFP · SOPL/)).toBeTruthy();
  });

  test("shows an empty state when the library is empty", async () => {
    mockFetch((url) => (url === "/api/benchmarks" ? { benchmarks: [] } : {}));
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    expect(await screen.findByTestId("benchmarks-empty")).toBeTruthy();
  });

  test("clicking a benchmark card selects it", async () => {
    mockFetch((url) => (url === "/api/benchmarks" ? sampleList : {}));
    const onSelect = vi.fn();
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={onSelect} />);
    const card = await screen.findByTestId("benchmark-card-1");
    fireEvent.click(card);
    expect(onSelect).toHaveBeenCalledWith(1);
  });

  test("upload mode requires a file before submitting", async () => {
    mockFetch((url) => (url === "/api/benchmarks" ? { benchmarks: [] } : {}));
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    await screen.findByTestId("add-benchmark-form");
    fireEvent.click(screen.getByTestId("bench-mode-upload"));
    fireEvent.change(screen.getByTestId("bench-name"), {
      target: { value: "My benchmark" },
    });
    fireEvent.click(screen.getByTestId("bench-submit"));
    expect(await screen.findByTestId("bench-error")).toHaveTextContent(/workbook/i);
  });

  test("from-run mode (default) picks a run, then posts to /from-run", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    mockFetch((url, init) => {
      calls.push({ url, init });
      if (url === "/api/benchmarks") return { benchmarks: [] };
      if (url.startsWith("/api/runs"))
        return {
          runs: [
            {
              id: 159, created_at: "2026-06-04T00:00:00Z", pdf_filename: "FINCO.pdf",
              status: "completed_with_errors", session_id: "s159", statements_run: [],
              models_used: [], duration_seconds: 1, scout_enabled: false, has_merged_workbook: true,
            },
          ],
          total: 1, limit: 100, offset: 0,
        };
      if (url === "/api/benchmarks/from-run")
        return { ok: true, id: 7, ingested: 102, statements: ["SOFP", "SOCIE"], source_run_id: 159, source_run_status: "completed_with_errors" };
      return {};
    });
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    await screen.findByTestId("add-benchmark-form");

    // Name but no run selected → validation error.
    fireEvent.change(screen.getByTestId("bench-name"), { target: { value: "From run 159" } });
    fireEvent.click(screen.getByTestId("bench-submit"));
    expect(await screen.findByTestId("bench-error")).toHaveTextContent(/run/i);
    expect(calls.some((c) => c.url === "/api/benchmarks/from-run")).toBe(false);

    // The picker lists the finished run; selecting it and submitting posts.
    await waitFor(() =>
      expect(screen.getByTestId("bench-run-id")).toHaveTextContent(/FINCO\.pdf/));
    fireEvent.change(screen.getByTestId("bench-run-id"), { target: { value: "159" } });
    fireEvent.click(screen.getByTestId("bench-submit"));
    expect(await screen.findByTestId("bench-ok")).toHaveTextContent(/102 gold cells/);
    const post = calls.find((c) => c.url === "/api/benchmarks/from-run");
    expect(post).toBeTruthy();
    expect(JSON.parse(String(post!.init!.body))).toMatchObject({ run_id: 159, name: "From run 159" });
  });

  test("upload mode surfaces the un-cached-formula warning", async () => {
    mockFetch((url, init) => {
      if (url === "/api/benchmarks" && (init?.method ?? "GET") === "GET") return { benchmarks: [] };
      return { ok: true, id: 3, ingested: 64, statements: ["SOFP"], skipped_formula_cells: 314, warning: "314 gradeable cell(s) were skipped because the workbook's formulas were never recalculated." };
    });
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    await screen.findByTestId("add-benchmark-form");
    fireEvent.click(screen.getByTestId("bench-mode-upload"));
    fireEvent.change(screen.getByTestId("bench-name"), { target: { value: "Up" } });
    const file = new File(["x"], "filled.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    fireEvent.change(screen.getByTestId("bench-file"), { target: { files: [file] } });
    fireEvent.click(screen.getByTestId("bench-submit"));
    expect(await screen.findByTestId("bench-warning")).toHaveTextContent(/314 gradeable cell/);
  });

  test("editor mode renders the gold editor for the selected benchmark", async () => {
    mockFetch((url) => {
      if (url === "/api/benchmarks") return sampleList;
      if (url === "/api/benchmarks/1/concepts") return { benchmark_id: 1, concepts: [] };
      return {};
    });
    render(<BenchmarksPage selectedId={1} onSelectBenchmark={() => {}} />);
    expect(await screen.findByTestId("benchmark-editor-page")).toBeTruthy();
    // Back button returns to the list.
    expect(screen.getByTestId("benchmark-back")).toBeTruthy();
    // The reused ConceptsPage grid mounts in benchmark mode.
    await waitFor(() =>
      expect(screen.getByTestId("benchmark-gold-editor")).toBeTruthy()
    );
  });
});
