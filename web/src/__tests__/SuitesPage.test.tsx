import { describe, test, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { SuitesPage } from "../pages/SuitesPage";

const originalFetch = globalThis.fetch;
beforeEach(() => {
  globalThis.fetch = vi.fn();
});
afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

function mockRoutes(routes: Record<string, unknown> | ((url: string, init?: RequestInit) => unknown)) {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
    async (url: string, init?: RequestInit) => {
      const body = typeof routes === "function" ? routes(url, init) : routes[url];
      return { ok: true, status: 200, json: async () => body ?? {} } as Response;
    },
  );
}

describe("SuitesPage", () => {
  test("lists suites and shows the empty state", async () => {
    mockRoutes((url) => (url === "/api/suites" ? { suites: [] } : {}));
    render(<SuitesPage />);
    expect(await screen.findByTestId("suites-empty")).toBeTruthy();
  });

  test("renders a suite card and opens the suite detail", async () => {
    mockRoutes((url) => {
      if (url === "/api/suites") return { suites: [{ id: 3, name: "Reg set", created_at: "", updated_at: "", doc_count: 2, run_count: 1 }] };
      if (url === "/api/suites/3") return { id: 3, name: "Reg set", created_at: "", updated_at: "", docs: [] };
      if (url === "/api/suites/3/runs") return { suite_run_list: [] };
      if (url === "/api/benchmarks") return { benchmarks: [] };
      return {};
    });
    render(<SuitesPage />);
    const card = await screen.findByTestId("suite-card-3");
    expect(card.textContent).toContain("Reg set");
    fireEvent.click(card);
    // Suite detail shows the launch form + documents section.
    expect(await screen.findByTestId("suite-tab-setup")).toBeTruthy();
    expect(screen.getByTestId("run-launch")).toBeTruthy();
  });

  test("launch form shows the run estimate", async () => {
    mockRoutes((url, init) => {
      if (url === "/api/suites") return { suites: [{ id: 1, name: "S", created_at: "", updated_at: "", doc_count: 2, run_count: 0 }] };
      if (url === "/api/suites/1" && (init?.method ?? "GET") === "GET")
        return { id: 1, name: "S", created_at: "", updated_at: "", docs: [
          { id: 10, label: "d1", source_filename: "d1.pdf", filing_standard: "mfrs", filing_level: "company", benchmark_id: null, created_at: "" },
          { id: 11, label: "d2", source_filename: "d2.pdf", filing_standard: "mfrs", filing_level: "company", benchmark_id: 4, created_at: "" },
        ] };
      if (url === "/api/suites/1/runs") return { suite_run_list: [] };
      if (url === "/api/benchmarks") return { benchmarks: [] };
      if (url === "/api/suites/1/estimate")
        return { documents: 2, repeats: 1, extraction_runs: 2, avg_run_seconds: 120, estimated_wall_seconds: 80, concurrency: 3 };
      return {};
    });
    render(<SuitesPage />);
    fireEvent.click(await screen.findByTestId("suite-card-1"));
    const est = await screen.findByTestId("run-estimate");
    expect(est.textContent).toContain("2 extraction runs");
  });

  test("results tab renders the trend container and compare picker", async () => {
    mockRoutes((url) => {
      if (url === "/api/suites") return { suites: [{ id: 1, name: "S", created_at: "", updated_at: "", doc_count: 1, run_count: 2 }] };
      if (url === "/api/suites/1") return { id: 1, name: "S", created_at: "", updated_at: "", docs: [] };
      if (url === "/api/suites/1/runs") return { suite_run_list: [] };
      if (url === "/api/benchmarks") return { benchmarks: [] };
      if (url === "/api/suites/1/results")
        return { suite_id: 1, points: [
          { suite_run_id: 1, label: "base", model: "m", app_version: "v1", created_at: "", status: "complete", mean_accuracy: 0.8, mean_consistency: 0.9, mean_cross_check_pass_rate: 1.0 },
          { suite_run_id: 2, label: "after", model: "m", app_version: "v2", created_at: "", status: "complete", mean_accuracy: 0.9, mean_consistency: 0.95, mean_cross_check_pass_rate: 1.0 },
        ] };
      return {};
    });
    render(<SuitesPage />);
    fireEvent.click(await screen.findByTestId("suite-card-1"));
    fireEvent.click(await screen.findByTestId("suite-tab-results"));
    expect(await screen.findByTestId("results-trend")).toBeTruthy();
    expect(screen.getByTestId("compare-a")).toBeTruthy();
    expect(screen.getByTestId("compare-b")).toBeTruthy();
  });
});
