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

  test("the add form requires a file before submitting", async () => {
    mockFetch((url) => (url === "/api/benchmarks" ? { benchmarks: [] } : {}));
    render(<BenchmarksPage selectedId={null} onSelectBenchmark={() => {}} />);
    await screen.findByTestId("add-benchmark-form");
    fireEvent.change(screen.getByTestId("bench-name"), {
      target: { value: "My benchmark" },
    });
    fireEvent.click(screen.getByTestId("bench-submit"));
    expect(await screen.findByTestId("bench-error")).toHaveTextContent(/workbook/i);
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
