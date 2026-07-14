import { describe, test, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
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

  test("a running suite run shows a Stop control that calls /stop", async () => {
    const calls: string[] = [];
    mockRoutes((url, init) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/api/suites") return { suites: [{ id: 1, name: "S", created_at: "", updated_at: "", doc_count: 1, run_count: 1 }] };
      if (url === "/api/suites/1" && (init?.method ?? "GET") === "GET")
        return { id: 1, name: "S", created_at: "", updated_at: "", docs: [] };
      if (url === "/api/suites/1/runs")
        return { suite_run_list: [{ id: 5, suite_id: 1, label: "batch", model: null, app_version: null, status: "running", created_at: "2026-07-10", ended_at: null }] };
      if (url === "/api/benchmarks") return { benchmarks: [] };
      if (url === "/api/suites/1/estimate") return { documents: 1, repeats: 1, extraction_runs: 1, avg_run_seconds: null, estimated_wall_seconds: null, concurrency: 3 };
      return {};
    });
    render(<SuitesPage />);
    fireEvent.click(await screen.findByTestId("suite-card-1"));
    const stop = await screen.findByTestId("suite-run-stop-5");
    fireEvent.click(stop);
    await waitFor(() =>
      expect(calls.some((c) => c === "POST /api/suites/1/runs/5/stop")).toBe(true),
    );
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

  test("empty state separates title, explanation, and the create action (CS4)", async () => {
    mockRoutes((url) => (url === "/api/suites" ? { suites: [] } : {}));
    render(<SuitesPage />);
    const empty = await screen.findByTestId("suites-empty");
    const title = screen.getByText("No evaluation suites yet");
    // Title and explanation are distinct elements — no run-together text.
    expect(title.textContent).toBe("No evaluation suites yet");
    expect(empty.textContent).toContain("Create one above");
    expect(screen.getByTestId("suite-create")).toBeTruthy();
  });

  test("suite-run status renders as monochrome symbol + human label (CS4)", async () => {
    mockRoutes((url) => {
      if (url === "/api/suites") return { suites: [{ id: 1, name: "S", created_at: "", updated_at: "", doc_count: 1, run_count: 1 }] };
      if (url === "/api/suites/1") return { id: 1, name: "S", created_at: "", updated_at: "", docs: [] };
      if (url === "/api/suites/1/runs")
        return { suite_run_list: [{ id: 7, suite_id: 1, label: "", status: "partial", created_at: "2026-07-01T00:00:00Z" }] };
      if (url === "/api/benchmarks") return { benchmarks: [] };
      return {};
    });
    render(<SuitesPage />);
    fireEvent.click(await screen.findByTestId("suite-card-1"));
    const row = await screen.findByTestId("suite-run-7");
    // Raw enum "partial" is translated, and the symbol is neutral aria-hidden.
    expect(row.textContent).toContain("Partial — resume to finish");
    const symbol = row.querySelector('[aria-hidden="true"]');
    expect(symbol?.textContent).toBe("!");
    expect((symbol as HTMLElement).style.color).toBe("rgb(94, 94, 94)");
  });
});

describe("SuitesPage — complete launch config (Step 13, PLAN-evals-hardening)", () => {
  function suiteRoutes(onLaunch: (body: Record<string, unknown>) => void) {
    return (url: string, init?: RequestInit) => {
      if (url === "/api/suites") return { suites: [{ id: 1, name: "S", created_at: "", updated_at: "", doc_count: 1, run_count: 0 }] };
      if (url === "/api/suites/1" && (init?.method ?? "GET") === "GET")
        return { id: 1, name: "S", created_at: "", updated_at: "", docs: [
          { id: 10, label: "d1", source_filename: "d1.pdf", filing_standard: "mfrs", filing_level: "company", benchmark_id: null, created_at: "", denomination: "millions" },
        ] };
      if (url === "/api/suites/1/runs") return { suite_run_list: [] };
      if (url === "/api/benchmarks") return { benchmarks: [] };
      if (url === "/api/settings") return { model: "openai.gpt-5.4", available_models: [{ id: "claude-fable-5", label: "Claude Fable" }] };
      if (url === "/api/suites/1/estimate")
        return { documents: 1, repeats: 1, extraction_runs: 1, avg_run_seconds: 60,
          estimated_wall_seconds: 60, estimated_tokens: 250_000,
          estimated_cost_usd: 3.5, cost_range_usd: [2.5, 4.5], concurrency: 3 };
      if (url === "/api/suites/1/run" && init?.method === "POST") {
        onLaunch(JSON.parse(String(init.body)));
        return { suite_run_id: 9, status: "running" };
      }
      return {};
    };
  }

  test("launch body carries model, statements and notes selections", async () => {
    let launched: Record<string, unknown> | null = null;
    mockRoutes(suiteRoutes((b) => { launched = b; }));
    render(<SuitesPage />);
    fireEvent.click(await screen.findByTestId("suite-card-1"));

    // Model picker offers the Settings default + configured models.
    const model = (await screen.findByTestId("run-model")) as HTMLSelectElement;
    expect(model.options[0].textContent).toContain("openai.gpt-5.4");
    fireEvent.change(model, { target: { value: "claude-fable-5" } });

    // Drop SOCIE, add one notes template.
    fireEvent.click(screen.getByTestId("run-stmt-SOCIE"));
    fireEvent.click(screen.getByTestId("run-note-CORP_INFO"));

    fireEvent.click(screen.getByTestId("run-launch"));
    await waitFor(() => expect(launched).not.toBeNull());
    expect(launched!.model).toBe("claude-fable-5");
    expect(launched!.statements).toEqual(["SOFP", "SOPL", "SOCI", "SOCF"]);
    expect(launched!.notes_to_run).toEqual(["CORP_INFO"]);
  });

  test("estimate shows token + cost figures (Step 4)", async () => {
    mockRoutes(suiteRoutes(() => {}));
    render(<SuitesPage />);
    fireEvent.click(await screen.findByTestId("suite-card-1"));
    const est = await screen.findByTestId("run-estimate");
    expect(est.textContent).toContain("≈0.3M tokens");
    expect(est.textContent).toContain("≈$3.50");
    expect(est.textContent).toContain("$2.50–$4.50");
  });

  test("doc form offers per-document denomination; non-default shows in the list", async () => {
    mockRoutes(suiteRoutes(() => {}));
    render(<SuitesPage />);
    fireEvent.click(await screen.findByTestId("suite-card-1"));
    expect(await screen.findByTestId("doc-denomination")).toBeTruthy();
    const row = screen.getByTestId("doc-row-10");
    expect(row.textContent).toContain("millions");
  });
});
