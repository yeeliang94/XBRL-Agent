import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react";
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
  test("lists benchmarks with their verified-value count", async () => {
    mockFetch((url) => {
      if (url === "/api/benchmarks") return sampleList;
      return {};
    });
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    expect(await screen.findByText("FINCO 2021 MFRS Company")).toBeTruthy();
    expect(screen.getByText("42 reference values")).toBeTruthy();
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

  // UX-QA #4: delete now goes through the shared ConfirmDialog, not window.confirm.
  test("deleting a benchmark confirms via the shared dialog before calling the API", async () => {
    const calls: string[] = [];
    mockFetch((url, init) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/api/benchmarks") return sampleList;
      return {};
    });
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    fireEvent.click(await screen.findByTestId("benchmark-delete-1"));
    // No API delete yet — the confirm dialog is open.
    expect(calls.some((c) => c.startsWith("DELETE"))).toBe(false);
    const dialog = await screen.findByRole("dialog", { name: /delete benchmark/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /delete benchmark/i }));
    await waitFor(() =>
      expect(calls.some((c) => c === "DELETE /api/benchmarks/1")).toBe(true));
  });

  // UX-QA review fix: the ConfirmDialog is a sibling of the clickable card, so
  // confirming/cancelling a delete must not bubble to the card and open it.
  test("delete confirm/cancel does not open the benchmark", async () => {
    mockFetch((url) => (url === "/api/benchmarks" ? sampleList : {}));
    const onSelect = vi.fn();
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={onSelect} />);
    fireEvent.click(await screen.findByTestId("benchmark-delete-1"));
    const dialog = await screen.findByRole("dialog", { name: /delete benchmark/i });
    // Cancel: card must not open.
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    expect(onSelect).not.toHaveBeenCalled();
    // Reopen and confirm: still must not open the benchmark.
    fireEvent.click(screen.getByTestId("benchmark-delete-1"));
    const dialog2 = await screen.findByRole("dialog", { name: /delete benchmark/i });
    fireEvent.click(within(dialog2).getByRole("button", { name: /delete benchmark/i }));
    await waitFor(() => expect(onSelect).not.toHaveBeenCalled());
  });

  test("upload mode requires a file before submitting", async () => {
    mockFetch((url) => (url === "/api/benchmarks" ? { benchmarks: [] } : {}));
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    await screen.findByTestId("add-benchmark-form");
    fireEvent.click(screen.getByTestId("bench-mode-upload"));
    fireEvent.change(screen.getByTestId("bench-name"), {
      target: { value: "My benchmark" },
    });
    expect(screen.getByTestId("bench-submit")).toBeDisabled();
    expect(screen.getByTestId("bench-create-reason")).toHaveTextContent(/workbook/i);
  });

  test("from-run mode (default) picks a run, then posts to /from-run", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    mockFetch((url, init) => {
      calls.push({ url, init });
      if (url === "/api/benchmarks") return { benchmarks: [] };
      if (url.startsWith("/api/runs")) {
        // The picker fetches each terminal status separately; return run 159
        // only for its actual status so it isn't double-listed.
        const runs = url.includes("status=completed_with_errors")
          ? [
              {
                id: 159, created_at: "2026-06-04T00:00:00Z", pdf_filename: "FINCO.pdf",
                status: "completed_with_errors", session_id: "s159", statements_run: [],
                models_used: [], duration_seconds: 1, scout_enabled: false, has_merged_workbook: true,
              },
            ]
          : [];
        return { runs, total: runs.length, limit: 100, offset: 0 };
      }
      if (url === "/api/benchmarks/from-run")
        return { ok: true, id: 7, ingested: 102, statements: ["SOFP", "SOCIE"], source_run_id: 159, source_run_status: "completed_with_errors" };
      return {};
    });
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    await screen.findByTestId("add-benchmark-form");

    // Name but no run selected → the disabled action explains what's missing.
    fireEvent.change(screen.getByTestId("bench-name"), { target: { value: "From run 159" } });
    expect(screen.getByTestId("bench-submit")).toBeDisabled();
    expect(screen.getByTestId("bench-create-reason")).toHaveTextContent(/run/i);
    expect(calls.some((c) => c.url === "/api/benchmarks/from-run")).toBe(false);

    // The picker lists the finished run; selecting it and submitting posts.
    await waitFor(() =>
      expect(screen.getByTestId("bench-run-id")).toHaveTextContent(/FINCO\.pdf/));
    fireEvent.change(screen.getByTestId("bench-run-id"), { target: { value: "159" } });
    fireEvent.click(screen.getByTestId("bench-submit"));
    expect(await screen.findByTestId("bench-ok")).toHaveTextContent(/102 reference values/);
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

  // --- Step C4: mTool-as-gold source ---

  test("mtool mode requires a unit + a template before submitting", async () => {
    mockFetch((url) => {
      if (url === "/api/benchmarks") return { benchmarks: [] };
      if (url.startsWith("/api/eval/templates"))
        return { templates: [{ template_id: "mfrs-company-sofp-cunoncu-v1", statement: "SOFP", variant: "cunoncu", label: "SOFP · cunoncu" }] };
      return {};
    });
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    await screen.findByTestId("add-benchmark-form");
    fireEvent.click(screen.getByTestId("bench-mode-mtool"));
    fireEvent.change(screen.getByTestId("bench-name"), { target: { value: "Human FINCO" } });
    const file = new File(["x"], "mtool.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    fireEvent.change(screen.getByTestId("bench-file"), { target: { files: [file] } });
    // No unit chosen yet → the action remains disabled with a visible reason.
    expect(screen.getByTestId("bench-submit")).toBeDisabled();
    expect(screen.getByTestId("bench-create-reason")).toHaveTextContent(/unit/i);
  });

  test("mtool happy path posts to /from-mtool and renders the ingest report", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    mockFetch((url, init) => {
      calls.push({ url, init });
      if (url === "/api/benchmarks" && (init?.method ?? "GET") === "GET") return { benchmarks: [] };
      if (url.startsWith("/api/eval/templates"))
        return { templates: [{ template_id: "mfrs-company-sofp-cunoncu-v1", statement: "SOFP", variant: "cunoncu", label: "SOFP · cunoncu" }] };
      if (url === "/api/benchmarks/from-mtool")
        return {
          ok: true, id: 9, ingested: 40,
          matched_by_statement: { SOFP: 40 },
          unmatched_rows: [{ sheet: "SOFP", row: 12, label: "Weird custom line", values: [123] }],
          prose_notes_captured: 2,
          scale_warning: null,
          statements: ["SOFP"],
        };
      return {};
    });
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    await screen.findByTestId("add-benchmark-form");
    fireEvent.click(screen.getByTestId("bench-mode-mtool"));
    fireEvent.change(screen.getByTestId("bench-name"), { target: { value: "Human FINCO" } });
    const file = new File(["x"], "mtool.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    fireEvent.change(screen.getByTestId("bench-file"), { target: { files: [file] } });
    fireEvent.change(screen.getByTestId("bench-unit"), { target: { value: "thousands" } });
    await waitFor(() =>
      expect(screen.getByTestId("bench-template-mfrs-company-sofp-cunoncu-v1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("bench-template-mfrs-company-sofp-cunoncu-v1"));
    fireEvent.click(screen.getByTestId("bench-submit"));

    const report = await screen.findByTestId("bench-ingest-report");
    expect(report).toHaveTextContent(/40 reference values captured/);
    expect(screen.getByTestId("bench-unmatched")).toHaveTextContent(/Weird custom line/);
    const post = calls.find((c) => c.url === "/api/benchmarks/from-mtool");
    expect(post).toBeTruthy();
    expect((post!.init!.body as FormData).get("unit")).toBe("thousands");
    expect((post!.init!.body as FormData).get("template_ids")).toBe(
      JSON.stringify(["mfrs-company-sofp-cunoncu-v1"]),
    );
  });
});
