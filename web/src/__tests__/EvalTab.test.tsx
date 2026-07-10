import { describe, test, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { EvalTab } from "../components/EvalTab";
import type { EvalScoreJson } from "../lib/types";

const originalFetch = globalThis.fetch;
afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

const score: EvalScoreJson = {
  benchmark_id: 5,
  gold_cells: 473,
  matched_cells: 412,
  missing_cells: 11,
  mismatch_cells: 50,
  extra_cells: 4,
  scale_mismatch: 3,
  score: 412 / 473,
};

describe("EvalTab", () => {
  test("renders the headline percentage + fraction from an embedded score", () => {
    render(<EvalTab runId={42} initialScore={score} />);
    expect(screen.getByTestId("eval-headline").textContent).toBe("87%");
    expect(screen.getByText(/412 \/ 473/)).toBeTruthy();
  });

  test("the flag line lists only non-zero signals", () => {
    render(<EvalTab runId={42} initialScore={score} />);
    const flags = screen.getByTestId("eval-flags").textContent ?? "";
    expect(flags).toContain("3 scale mismatch");
    expect(flags).toContain("11 missing");
    expect(flags).toContain("4 extra");
  });

  test("a clean run reads 'No issues'", () => {
    render(
      <EvalTab
        runId={42}
        initialScore={{ ...score, missing_cells: 0, mismatch_cells: 0, scale_mismatch: 0, extra_cells: 0, matched_cells: 473 }}
      />,
    );
    expect(screen.getByTestId("eval-flags").textContent).toContain("No issues");
  });

  test("fetches the score when none was embedded", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => score,
    })) as unknown as typeof fetch;
    render(<EvalTab runId={42} />);
    await waitFor(() =>
      expect(screen.getByTestId("eval-headline").textContent).toBe("87%")
    );
  });

  test("shows a 'not graded' message when the fetch returns no score", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 404,
      json: async () => ({}),
    })) as unknown as typeof fetch;
    render(<EvalTab runId={42} />);
    await waitFor(() => expect(screen.getByTestId("eval-no-score")).toBeTruthy());
  });

  test("renders the failure taxonomy when present, only non-zero rows", () => {
    render(
      <EvalTab
        runId={42}
        initialScore={{
          ...score,
          taxonomy: { sign_flip: 2, scale: 3, period_swap: 0, unaddressed: 5 },
        }}
      />,
    );
    const tax = screen.getByTestId("eval-taxonomy");
    expect(tax.textContent).toContain("Sign flipped");
    expect(tax.textContent).toContain("Not reached");
    // A zero-count diagnosis (period_swap) is omitted.
    expect(tax.textContent).not.toContain("Year swapped");
  });

  test("renders per-statement accuracy when present", () => {
    render(
      <EvalTab
        runId={42}
        initialScore={{
          ...score,
          per_statement: {
            SOFP: { gold_cells: 10, matched: 9 },
            SOCF: { gold_cells: 8, matched: 4 },
          },
        }}
      />,
    );
    const ps = screen.getByTestId("eval-per-statement");
    expect(ps.textContent).toContain("SOFP");
    expect(ps.textContent).toContain("90%");
    expect(ps.textContent).toContain("SOCF");
    expect(ps.textContent).toContain("50%");
  });

  test("no taxonomy/per-statement sections on a legacy score", () => {
    render(<EvalTab runId={42} initialScore={score} />);
    expect(screen.queryByTestId("eval-taxonomy")).toBeNull();
    expect(screen.queryByTestId("eval-per-statement")).toBeNull();
  });
});
