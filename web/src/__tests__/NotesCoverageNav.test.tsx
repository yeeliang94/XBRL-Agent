import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { NotesCoverageNav } from "../components/NotesCoverageNav";
import type { CoverageNavRow } from "../components/NotesCoverageNav";

function mockCoverage(payload: unknown, status = 200) {
  globalThis.fetch = vi.fn(async () => ({
    ok: status < 400,
    status,
    json: async () => payload,
  })) as unknown as typeof fetch;
}

const PLACED: CoverageNavRow = {
  note_num: 5,
  title: "Revenue",
  status: "placed",
  reviewer_verdict: null,
  placements: [{ sheet: "Notes-SummaryofAccPol", row: 7, row_label: "Revenue", kind: "primary" }],
  page_lo: 12,
  page_hi: 13,
};
const MISSING: CoverageNavRow = {
  note_num: 8,
  title: "Contingencies",
  status: "missing",
  reviewer_verdict: null,
  placements: [],
  page_lo: 20,
  page_hi: null,
};

const SAMPLE = {
  run_id: 42,
  banner: "reviewed",
  inventory_available: true,
  rows: [PLACED, MISSING],
  summary: { placed: 1, missing: 1, skipped: 0, suspected_gap: 0, total: 2, unresolved: 1 },
};

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => cleanup());

describe("NotesCoverageNav", () => {
  test("renders one row per note with a status dot + summary", async () => {
    mockCoverage(SAMPLE);
    render(<NotesCoverageNav runId={42} onSelectNote={() => {}} />);
    await waitFor(() => screen.getByTestId("notes-coverage-nav"));
    expect(screen.getByTestId("coverage-nav-summary").textContent).toContain(
      "1 of 2 notes placed",
    );
    expect(screen.getByTestId("coverage-nav-note-5").textContent).toContain("Revenue");
    expect(screen.getByTestId("coverage-nav-note-8").textContent).toContain("Contingencies");
    expect(screen.getByTestId("coverage-nav-dot-placed")).toBeTruthy();
    expect(screen.getByTestId("coverage-nav-dot-missing")).toBeTruthy();
  });

  test("clicking a note reports the full row so the parent can navigate", async () => {
    mockCoverage(SAMPLE);
    const onSelectNote = vi.fn();
    render(<NotesCoverageNav runId={42} onSelectNote={onSelectNote} />);
    await waitFor(() => screen.getByTestId("coverage-nav-note-5"));
    fireEvent.click(screen.getByTestId("coverage-nav-note-5"));
    expect(onSelectNote).toHaveBeenCalledWith(PLACED);
  });

  test("highlights the note whose sheet is currently shown", async () => {
    mockCoverage(SAMPLE);
    render(
      <NotesCoverageNav
        runId={42}
        activeSheet="Notes-SummaryofAccPol"
        onSelectNote={() => {}}
      />,
    );
    await waitFor(() => screen.getByTestId("coverage-nav-note-5"));
    expect(screen.getByTestId("coverage-nav-note-5").getAttribute("aria-current")).toBe(
      "true",
    );
    expect(
      screen.getByTestId("coverage-nav-note-8").getAttribute("aria-current"),
    ).toBeNull();
  });

  test("stays loud when the inventory is unavailable", async () => {
    mockCoverage({
      run_id: 42,
      banner: "inventory_unavailable",
      inventory_available: false,
      rows: [],
      summary: { placed: 0, missing: 0, skipped: 0, suspected_gap: 0, total: 0, unresolved: 0 },
    });
    render(<NotesCoverageNav runId={42} onSelectNote={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId("coverage-nav-inventory_unavailable")).toBeTruthy(),
    );
  });

  test("self-hides on an empty / pre-feature run", async () => {
    mockCoverage({ run_id: 42, banner: "pre_feature", inventory_available: true, rows: [] });
    const { container } = render(<NotesCoverageNav runId={42} onSelectNote={() => {}} />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    // Nothing rendered — the nav container never appears.
    expect(screen.queryByTestId("notes-coverage-nav")).toBeNull();
    expect(container.textContent).not.toContain("placed");
  });

  test("reports content presence via onVisible", async () => {
    // Has rows → visible.
    mockCoverage(SAMPLE);
    const onVisible = vi.fn();
    render(<NotesCoverageNav runId={42} onSelectNote={() => {}} onVisible={onVisible} />);
    await waitFor(() => expect(onVisible).toHaveBeenCalledWith(true));
    cleanup();

    // pre_feature empty → not visible.
    mockCoverage({ run_id: 42, banner: "pre_feature", inventory_available: true, rows: [] });
    const onVisible2 = vi.fn();
    render(<NotesCoverageNav runId={42} onSelectNote={() => {}} onVisible={onVisible2} />);
    await waitFor(() => expect(onVisible2).toHaveBeenCalledWith(false));
    cleanup();

    // inventory_unavailable with no rows → still visible (loud banner).
    mockCoverage({
      run_id: 42,
      banner: "inventory_unavailable",
      inventory_available: false,
      rows: [],
      summary: { placed: 0, missing: 0, skipped: 0, suspected_gap: 0, total: 0, unresolved: 0 },
    });
    const onVisible3 = vi.fn();
    render(<NotesCoverageNav runId={42} onSelectNote={() => {}} onVisible={onVisible3} />);
    await waitFor(() => expect(onVisible3).toHaveBeenCalledWith(true));
  });
});
