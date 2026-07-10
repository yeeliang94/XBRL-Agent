import { describe, test, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { ConsistencyPanel } from "../components/ConsistencyPanel";
import type { RepeatGroupJson } from "../lib/types";

const originalFetch = globalThis.fetch;
afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

function mockGroup(group: RepeatGroupJson) {
  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => group,
  })) as unknown as typeof fetch;
}

const baseGroup: RepeatGroupJson = {
  id: 7,
  created_at: "2026-07-10T00:00:00",
  repeats_requested: 3,
  benchmark_id: null,
  status: "complete",
  config: null,
  consistency: {
    available: true,
    n_repeats: 3,
    union_slots: 4,
    unanimous: 2,
    consistency: 0.5,
    presence_disagreements: [
      { key: ["u2", "CY", "Company"], filled_by: [0], n_present: 1, n_repeats: 3 },
    ],
    value_disagreements: [
      { key: ["u1", "CY", "Company"], values: [100, 105, 100], spread: 5 },
    ],
    unanimous_right: null,
    unanimous_wrong: null,
  },
  runs: [
    { id: 1, status: "completed", repeat_index: 0 },
    { id: 2, status: "completed", repeat_index: 1 },
    { id: 3, status: "completed", repeat_index: 2 },
  ],
};

describe("ConsistencyPanel", () => {
  test("renders headline agreement + disagreement tables", async () => {
    mockGroup(baseGroup);
    render(<ConsistencyPanel groupId={7} />);
    await waitFor(() =>
      expect(screen.getByTestId("consistency-headline").textContent).toBe("50%")
    );
    expect(screen.getByTestId("consistency-value-disagreements")).toBeTruthy();
    expect(screen.getByTestId("consistency-presence-disagreements")).toBeTruthy();
    // Value row shows the spread.
    expect(
      screen.getByTestId("consistency-value-disagreements").textContent
    ).toContain("100, 105, 100");
  });

  test("shows unavailable when fewer than 2 repeats finished", async () => {
    mockGroup({
      ...baseGroup,
      status: "partial",
      consistency: { ...baseGroup.consistency!, available: false, consistency: null },
    });
    render(<ConsistencyPanel groupId={7} />);
    await waitFor(() =>
      expect(screen.getByTestId("consistency-unavailable")).toBeTruthy()
    );
    expect(screen.queryByTestId("consistency-headline")).toBeNull();
  });

  test("surfaces the systematic-vs-stochastic cross when gold is attached", async () => {
    mockGroup({
      ...baseGroup,
      benchmark_id: 9,
      consistency: {
        ...baseGroup.consistency!,
        unanimous_right: 1,
        unanimous_wrong: 1,
      },
    });
    render(<ConsistencyPanel groupId={7} />);
    await waitFor(() =>
      expect(screen.getByTestId("consistency-panel").textContent).toContain(
        "Systematic"
      )
    );
  });
});
