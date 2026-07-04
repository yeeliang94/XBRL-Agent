import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { NotesCoveragePanel } from "../components/NotesCoveragePanel";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

function mockCoverage(payload: unknown) {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
    async () => ({ ok: true, status: 200, json: async () => payload }) as Response,
  );
}

const reviewedPayload = {
  run_id: 7,
  banner: "reviewed",
  inventory_available: true,
  rows: [
    {
      note_num: 1,
      title: "Corporate information",
      status: "placed",
      reason: "",
      placements: [
        { sheet: "Notes-CI", row: 6, row_label: "Company details", kind: "primary" },
      ],
      reviewer_added: false,
      reviewer_verdict: null,
      page_lo: 8,
      page_hi: 9,
      subnotes: [
        { subnote_ref: "(a)", state: "cited" },
        { subnote_ref: "(b)", state: "not_verified" },
      ],
    },
    {
      note_num: 5,
      title: "Investment properties",
      status: "missing",
      reason: "",
      placements: [],
      reviewer_added: false,
      reviewer_verdict: null,
      page_lo: null,
      page_hi: null,
      subnotes: [],
    },
    {
      note_num: 6,
      title: "Property, plant & equipment",
      status: "placed",
      reason: "",
      placements: [
        { sheet: "Notes-Listofnotes", row: 31, row_label: "PPE", kind: "primary" },
        { sheet: "Notes-SummaryofAccPol", row: 14, row_label: "PPE policy", kind: "carve_out" },
      ],
      reviewer_added: true,
      reviewer_verdict: null,
      page_lo: null,
      page_hi: null,
      subnotes: [],
    },
  ],
  summary: { placed: 2, missing: 1, skipped: 0, suspected_gap: 0, total: 3, unresolved: 1 },
};

describe("NotesCoveragePanel", () => {
  test("renders rows, statuses and the summary", async () => {
    mockCoverage(reviewedPayload);
    render(<NotesCoveragePanel runId={7} />);
    await waitFor(() => screen.getByTestId("notes-coverage-panel"));
    expect(screen.getByText("Corporate information")).toBeTruthy();
    expect(screen.getByTestId("coverage-row-5")).toBeTruthy();
    // A placed and a missing status badge both render.
    expect(screen.getAllByTestId("coverage-status-placed").length).toBe(2);
    expect(screen.getByTestId("coverage-status-missing")).toBeTruthy();
    expect(screen.getByTestId("coverage-summary").textContent).toContain("1 unresolved");
    // The reviewer-added carve-out marker shows.
    expect(screen.getByTestId("coverage-added-6")).toBeTruthy();
    expect(screen.getByText("carve-out")).toBeTruthy();
  });

  test("sub-note roll-up expands to per-sub-ref detail", async () => {
    mockCoverage(reviewedPayload);
    render(<NotesCoveragePanel runId={7} />);
    await waitFor(() => screen.getByTestId("notes-coverage-panel"));
    // Collapsed initially.
    expect(screen.queryByTestId("coverage-subnotes-1")).toBeNull();
    fireEvent.click(screen.getByTestId("coverage-subnotes-toggle-1"));
    await waitFor(() => screen.getByTestId("coverage-subnotes-1"));
    const detail = screen.getByTestId("coverage-subnotes-1");
    expect(detail.textContent).toContain("(a)");
    expect(detail.textContent).toContain("cited");
    expect(detail.textContent).toContain("not_verified");
  });

  test("placement click dispatches a focus event", async () => {
    mockCoverage(reviewedPayload);
    const spy = vi.fn();
    window.addEventListener("notes-coverage-focus", spy as EventListener);
    render(<NotesCoveragePanel runId={7} />);
    await waitFor(() => screen.getByTestId("notes-coverage-panel"));
    fireEvent.click(screen.getByTestId("coverage-placement-Notes-CI-6"));
    expect(spy).toHaveBeenCalledTimes(1);
    const ev = spy.mock.calls[0][0] as CustomEvent;
    expect(ev.detail).toEqual({ sheet: "Notes-CI", row: 6 });
    window.removeEventListener("notes-coverage-focus", spy as EventListener);
  });

  test("inventory_unavailable renders the loud banner", async () => {
    mockCoverage({
      run_id: 7,
      banner: "inventory_unavailable",
      inventory_available: false,
      rows: [],
      summary: { placed: 0, missing: 0, skipped: 0, suspected_gap: 0, total: 0, unresolved: 0 },
    });
    render(<NotesCoveragePanel runId={7} />);
    await waitFor(() => screen.getByTestId("coverage-banner-inventory_unavailable"));
  });

  test("not_reviewed renders the draft banner", async () => {
    mockCoverage({ ...reviewedPayload, banner: "not_reviewed" });
    render(<NotesCoveragePanel runId={7} />);
    await waitFor(() => screen.getByTestId("coverage-banner-not_reviewed"));
  });

  test("pre_feature run renders nothing", async () => {
    mockCoverage({
      run_id: 7,
      banner: "pre_feature",
      inventory_available: true,
      rows: [],
      summary: { placed: 0, missing: 0, skipped: 0, suspected_gap: 0, total: 0, unresolved: 0 },
    });
    const { container } = render(<NotesCoveragePanel runId={7} />);
    await waitFor(() => expect(screen.queryByText("Loading coverage…")).toBeNull());
    expect(container.querySelector('[data-testid="notes-coverage-panel"]')).toBeNull();
  });
});
