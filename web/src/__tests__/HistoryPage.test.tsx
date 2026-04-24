import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, act } from "@testing-library/react";

// Mock the API client so HistoryPage never hits a real backend.
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    fetchRuns: vi.fn(),
    fetchRunDetail: vi.fn(),
    deleteRun: vi.fn(),
    downloadFilledUrl: (id: number) => `/api/runs/${id}/download/filled`,
  };
});

import { HistoryPage } from "../pages/HistoryPage";
import * as api from "../lib/api";
import type { RunSummaryJson } from "../lib/types";

const fetchRuns = vi.mocked(api.fetchRuns);

const baseRun = {
  id: 1,
  created_at: "2026-04-10T09:30:00Z",
  pdf_filename: "FINCO-Audited-2021.pdf",
  status: "completed",
  session_id: "sess-1",
  statements_run: ["SOFP"],
  models_used: ["gemini-3-flash-preview"],
  duration_seconds: 42,
  scout_enabled: true,
  has_merged_workbook: true,
};

describe("HistoryPage", () => {
  beforeEach(() => {
    fetchRuns.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  test("calls fetchRuns on mount and renders the returned rows", async () => {
    fetchRuns.mockResolvedValue({
      runs: [baseRun, { ...baseRun, id: 2, pdf_filename: "ACME-2023.pdf" }],
      total: 2,
      limit: 50,
      offset: 0,
    });

    render(<HistoryPage />);

    // fetchRuns called immediately on mount
    expect(fetchRuns).toHaveBeenCalledTimes(1);

    // Rows render once the promise resolves
    await waitFor(() => {
      expect(screen.getByText("FINCO-Audited-2021.pdf")).toBeTruthy();
      expect(screen.getByText("ACME-2023.pdf")).toBeTruthy();
    });
  });

  test("typing in the search box re-fetches with the new q (debounced)", async () => {
    // This test uses fake timers because we need deterministic control over
    // the 300ms HistoryFilters debounce. waitFor loops need timer advances.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      fetchRuns.mockResolvedValue({ runs: [], total: 0, limit: 50, offset: 0 });

      render(<HistoryPage />);
      expect(fetchRuns).toHaveBeenCalledTimes(1);

      const search = screen.getByPlaceholderText(/search.*filename/i);
      fireEvent.change(search, { target: { value: "FINCO" } });

      // Not yet — debounce
      expect(fetchRuns).toHaveBeenCalledTimes(1);

      act(() => {
        vi.advanceTimersByTime(400);
      });

      await waitFor(() => {
        expect(fetchRuns).toHaveBeenCalledTimes(2);
      });
      expect(fetchRuns).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: "FINCO" }),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  test("surfaces fetchRuns error in the list", async () => {
    fetchRuns.mockRejectedValue(new Error("backend down"));

    render(<HistoryPage />);

    await waitFor(() => {
      expect(screen.getByText(/backend down/i)).toBeTruthy();
    });
  });

  // --- Phase 6: detail-panel navigation ---

  test("selecting a row loads the detail view", async () => {
    fetchRuns.mockResolvedValue({
      runs: [baseRun, { ...baseRun, id: 2, pdf_filename: "ACME-2023.pdf" }],
      total: 2,
      limit: 50,
      offset: 0,
    });
    const fetchRunDetailMock = vi.mocked(api.fetchRunDetail);
    fetchRunDetailMock.mockResolvedValue({
      id: 2,
      created_at: "2026-04-10T00:00:00Z",
      pdf_filename: "ACME-2023.pdf",
      status: "completed",
      session_id: "sess-2",
      output_dir: "/tmp/out/sess-2",
      merged_workbook_path: "/tmp/out/sess-2/filled.xlsx",
      scout_enabled: false,
      started_at: "2026-04-10T00:00:00Z",
      ended_at: "2026-04-10T00:01:00Z",
      config: { statements: ["SOFP"], variants: {}, models: {}, use_scout: false },
      agents: [],
      cross_checks: [],
    });

    render(<HistoryPage />);
    await waitFor(() => {
      expect(screen.getByText("ACME-2023.pdf")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("ACME-2023.pdf"));

    // fetchRunDetail called with the clicked id
    await waitFor(() => {
      expect(fetchRunDetailMock).toHaveBeenCalledWith(2);
    });
    // Detail panel rendered — the download button is a hallmark of RunDetailView
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /download/i })).toBeTruthy();
    });
  });

  test("deleting from detail removes the row from the list", async () => {
    fetchRuns.mockResolvedValue({
      runs: [baseRun, { ...baseRun, id: 7, pdf_filename: "TO-DELETE.pdf" }],
      total: 2,
      limit: 50,
      offset: 0,
    });
    vi.mocked(api.fetchRunDetail).mockResolvedValue({
      id: 7,
      created_at: "2026-04-10T00:00:00Z",
      pdf_filename: "TO-DELETE.pdf",
      status: "completed",
      session_id: "sess-7",
      output_dir: "/tmp/out/sess-7",
      merged_workbook_path: "/tmp/out/sess-7/filled.xlsx",
      scout_enabled: false,
      started_at: "2026-04-10T00:00:00Z",
      ended_at: "2026-04-10T00:01:00Z",
      config: { statements: ["SOFP"], variants: {}, models: {}, use_scout: false },
      agents: [],
      cross_checks: [],
    });
    vi.mocked(api.deleteRun).mockResolvedValue({ deleted: 7 });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<HistoryPage />);
    await waitFor(() => {
      expect(screen.getByText("TO-DELETE.pdf")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("TO-DELETE.pdf"));
    // Tighter matcher: rows themselves are now role="button" for keyboard
    // a11y, and the TO-DELETE.pdf row has "delete" in its filename, so
    // a loose /delete/i would be ambiguous. Match the exact "Delete run"
    // action button in the detail panel instead.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^delete run$/i })).toBeTruthy();
    });

    // Setup the post-delete refetch response BEFORE clicking Delete so
    // the list re-render after deletion omits the deleted row.
    fetchRuns.mockResolvedValue({
      runs: [baseRun], // id 7 removed
      total: 1,
      limit: 50,
      offset: 0,
    });

    fireEvent.click(screen.getByRole("button", { name: /^delete run$/i }));

    // deleteRun called with the right id
    await waitFor(() => {
      expect(api.deleteRun).toHaveBeenCalledWith(7);
    });
    // Row is gone from the list after the refetch
    await waitFor(() => {
      expect(screen.queryByText("TO-DELETE.pdf")).toBeNull();
    });
  });

  test("download action navigates window.location to the download URL", async () => {
    fetchRuns.mockResolvedValue({
      runs: [{ ...baseRun, id: 9, pdf_filename: "DL.pdf" }],
      total: 1,
      limit: 50,
      offset: 0,
    });
    vi.mocked(api.fetchRunDetail).mockResolvedValue({
      id: 9,
      created_at: "2026-04-10T00:00:00Z",
      pdf_filename: "DL.pdf",
      status: "completed",
      session_id: "sess-9",
      output_dir: "/tmp/out/sess-9",
      merged_workbook_path: "/tmp/out/sess-9/filled.xlsx",
      scout_enabled: false,
      started_at: "2026-04-10T00:00:00Z",
      ended_at: "2026-04-10T00:01:00Z",
      config: { statements: ["SOFP"], variants: {}, models: {}, use_scout: false },
      agents: [],
      cross_checks: [],
    });

    // Patch window.location.assign (jsdom supports this). The production
    // code uses window.location.href = ... which we capture by making href
    // a writable spy.
    const setHref = vi.fn();
    const originalLocation = window.location;
    Object.defineProperty(window, "location", {
      writable: true,
      value: {
        ...originalLocation,
        set href(v: string) { setHref(v); },
        get href() { return originalLocation.href; },
      },
    });

    try {
      render(<HistoryPage />);
      await waitFor(() => expect(screen.getByText("DL.pdf")).toBeTruthy());
      fireEvent.click(screen.getByText("DL.pdf"));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /download/i })).toBeTruthy(),
      );
      fireEvent.click(screen.getByRole("button", { name: /download/i }));
      expect(setHref).toHaveBeenCalledWith("/api/runs/9/download/filled");
    } finally {
      Object.defineProperty(window, "location", {
        writable: true,
        value: originalLocation,
      });
    }
  });

  // ---------------------------------------------------------------------------
  // Pagination — backend defaults to limit=50. Without paging, runs older
  // than #50 are silently invisible from the UI. The plan built backend
  // pagination (Step 2.1) but the frontend never wired it up; this fixes it.
  // ---------------------------------------------------------------------------

  test("renders 'Load more' control when total > runs returned", async () => {
    fetchRuns.mockResolvedValue({
      runs: Array.from({ length: 50 }, (_, i) => ({ ...baseRun, id: i + 1 })),
      total: 80,
      limit: 50,
      offset: 0,
    });

    render(<HistoryPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /load more/i })).toBeTruthy();
    });
    // The button label should hint at how many remain so users know what
    // they're loading. We don't pin the exact wording — just the count.
    expect(screen.getByRole("button", { name: /load more/i }).textContent).toContain("30");
  });

  test("clicking 'Load more' fetches the next page with offset=50", async () => {
    fetchRuns.mockResolvedValueOnce({
      runs: Array.from({ length: 50 }, (_, i) => ({ ...baseRun, id: i + 1 })),
      total: 80,
      limit: 50,
      offset: 0,
    });

    render(<HistoryPage />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /load more/i })).toBeTruthy();
    });

    // Second page returns 30 more runs.
    fetchRuns.mockResolvedValueOnce({
      runs: Array.from({ length: 30 }, (_, i) => ({ ...baseRun, id: i + 51 })),
      total: 80,
      limit: 50,
      offset: 50,
    });

    fireEvent.click(screen.getByRole("button", { name: /load more/i }));

    // Second fetch with offset 50
    await waitFor(() => {
      expect(fetchRuns).toHaveBeenCalledTimes(2);
    });
    expect(fetchRuns).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 50, limit: 50 }),
    );
  });

  test("'Load more' is hidden when all rows are loaded", async () => {
    fetchRuns.mockResolvedValue({
      // Distinct filenames so getByText is unambiguous.
      runs: [baseRun, { ...baseRun, id: 2, pdf_filename: "ACME-2023.pdf" }],
      total: 2,
      limit: 50,
      offset: 0,
    });

    render(<HistoryPage />);
    await waitFor(() => {
      expect(screen.getByText("ACME-2023.pdf")).toBeTruthy();
    });
    expect(screen.queryByRole("button", { name: /load more/i })).toBeNull();
  });

  test("changing filters resets pagination back to offset 0", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      // First page: 50 of 80 runs.
      fetchRuns.mockResolvedValue({
        runs: Array.from({ length: 50 }, (_, i) => ({ ...baseRun, id: i + 1 })),
        total: 80,
        limit: 50,
        offset: 0,
      });

      render(<HistoryPage />);
      await waitFor(() => {
        expect(screen.getByRole("button", { name: /load more/i })).toBeTruthy();
      });

      // Page 2.
      fetchRuns.mockResolvedValueOnce({
        runs: Array.from({ length: 30 }, (_, i) => ({ ...baseRun, id: i + 51 })),
        total: 80,
        limit: 50,
        offset: 50,
      });
      fireEvent.click(screen.getByRole("button", { name: /load more/i }));
      await waitFor(() => expect(fetchRuns).toHaveBeenCalledTimes(2));

      // Now type into the search box. The next fetch should reset offset
      // back to 0 — otherwise the user lands on "page 2" of empty results.
      fetchRuns.mockResolvedValue({
        runs: [],
        total: 0,
        limit: 50,
        offset: 0,
      });
      const search = screen.getByPlaceholderText(/search.*filename/i);
      fireEvent.change(search, { target: { value: "ZZZZ" } });
      act(() => {
        vi.advanceTimersByTime(400);
      });

      await waitFor(() => expect(fetchRuns).toHaveBeenCalledTimes(3));
      expect(fetchRuns).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: "ZZZZ", offset: 0 }),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  // ---------------------------------------------------------------------------
  // Peer-review [HIGH] regression: Load more was appending stale results
  // when the user changed filters while the load-more request was still in
  // flight. The fix snapshots filtersKey at call time and discards any
  // response whose filters no longer match.
  // ---------------------------------------------------------------------------
  test("stale Load more response is discarded when filters change mid-flight", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      // First page under the initial (empty) filter set.
      fetchRuns.mockResolvedValueOnce({
        runs: Array.from({ length: 50 }, (_, i) => ({
          ...baseRun,
          id: i + 1,
          pdf_filename: `initial-${i + 1}.pdf`,
        })),
        total: 80,
        limit: 50,
        offset: 0,
      });

      render(<HistoryPage />);
      await waitFor(() => {
        expect(screen.getByRole("button", { name: /load more/i })).toBeTruthy();
      });

      // Deferred load-more response — resolved only after we've had time
      // to change filters. The extra runs use "STALE-" filenames so we can
      // assert they never made it into the DOM.
      let resolveLoadMore: (value: {
        runs: RunSummaryJson[];
        total: number;
        limit: number;
        offset: number;
      }) => void;
      const loadMorePromise = new Promise<{
        runs: RunSummaryJson[];
        total: number;
        limit: number;
        offset: number;
      }>((resolve) => {
        resolveLoadMore = resolve;
      });
      fetchRuns.mockReturnValueOnce(loadMorePromise);

      fireEvent.click(screen.getByRole("button", { name: /load more/i }));
      await waitFor(() => expect(fetchRuns).toHaveBeenCalledTimes(2));

      // Now type into the search box — this triggers a filters change.
      // The fresh first-page fetch should resolve and replace the list
      // before the load-more response arrives.
      fetchRuns.mockResolvedValueOnce({
        runs: [{ ...baseRun, id: 999, pdf_filename: "FRESH.pdf" }],
        total: 1,
        limit: 50,
        offset: 0,
      });
      const search = screen.getByPlaceholderText(/search.*filename/i);
      fireEvent.change(search, { target: { value: "FRESH" } });
      act(() => {
        vi.advanceTimersByTime(400);
      });
      await waitFor(() => expect(fetchRuns).toHaveBeenCalledTimes(3));
      await waitFor(() => {
        expect(screen.getByText("FRESH.pdf")).toBeTruthy();
      });

      // NOW resolve the stale load-more with the old filter's data.
      resolveLoadMore!({
        runs: [{ ...baseRun, id: 500, pdf_filename: "STALE.pdf" }],
        total: 80,
        limit: 50,
        offset: 50,
      });
      // Flush the microtask so the stale .then runs.
      await act(async () => {
        await Promise.resolve();
      });

      // The stale row MUST NOT be in the DOM — the fresh list owns it now.
      expect(screen.queryByText("STALE.pdf")).toBeNull();
      // Fresh row is still there, unmolested.
      expect(screen.getByText("FRESH.pdf")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  // ---------------------------------------------------------------------------
  // Peer-review [MEDIUM] regression: a Load more failure used to reuse the
  // page-level `error` state, which made HistoryList blank the already-
  // loaded rows and show only the error. Pagination failures should be
  // non-destructive — rows stay visible, error shows inline.
  // ---------------------------------------------------------------------------
  // ---------------------------------------------------------------------------
  // Full-page run detail — Phase 3 of docs/PLAN.md. HistoryPage now swaps
  // out the old modal for a RunDetailPage when a run is selected. Selection
  // state can be driven externally (App.tsx owns it so the URL can round-
  // trip) or fall back to internal state so legacy render calls still work.
  // ---------------------------------------------------------------------------
  test("renders RunDetailPage (with back button) when selectedId prop is provided", async () => {
    fetchRuns.mockResolvedValue({
      runs: [{ ...baseRun, id: 77, pdf_filename: "DEEP-LINK.pdf" }],
      total: 1,
      limit: 50,
      offset: 0,
    });
    vi.mocked(api.fetchRunDetail).mockResolvedValue({
      id: 77,
      created_at: "2026-04-10T00:00:00Z",
      pdf_filename: "DEEP-LINK.pdf",
      status: "completed",
      session_id: "sess-77",
      output_dir: "/tmp/out/sess-77",
      merged_workbook_path: "/tmp/out/sess-77/filled.xlsx",
      scout_enabled: false,
      started_at: "2026-04-10T00:00:00Z",
      ended_at: "2026-04-10T00:01:00Z",
      config: { statements: ["SOFP"], variants: {}, models: {}, use_scout: false },
      agents: [],
      cross_checks: [],
    });

    render(<HistoryPage selectedId={77} onSelectRun={() => {}} />);

    // The back button is the hallmark of the full page — it doesn't exist
    // in the list view, so its presence proves the page mounted.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /back to history/i }),
      ).toBeInTheDocument();
    });
  });

  test("does not render the list while a run detail is open", async () => {
    fetchRuns.mockResolvedValue({
      runs: [
        { ...baseRun, id: 77, pdf_filename: "DEEP-LINK.pdf" },
        { ...baseRun, id: 99, pdf_filename: "OTHER.pdf" },
      ],
      total: 2,
      limit: 50,
      offset: 0,
    });
    vi.mocked(api.fetchRunDetail).mockResolvedValue({
      id: 77,
      created_at: "2026-04-10T00:00:00Z",
      pdf_filename: "DEEP-LINK.pdf",
      status: "completed",
      session_id: "sess-77",
      output_dir: "/tmp/out/sess-77",
      merged_workbook_path: "/tmp/out/sess-77/filled.xlsx",
      scout_enabled: false,
      started_at: "2026-04-10T00:00:00Z",
      ended_at: "2026-04-10T00:01:00Z",
      config: { statements: ["SOFP"], variants: {}, models: {}, use_scout: false },
      agents: [],
      cross_checks: [],
    });

    render(<HistoryPage selectedId={77} onSelectRun={() => {}} />);
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /back to history/i }),
      ).toBeInTheDocument();
    });
    // OTHER.pdf is an OTHER row; when the detail page is up, the list
    // should not be visible so the page feels like a full replacement.
    expect(screen.queryByText("OTHER.pdf")).toBeNull();
  });

  test("Back button never calls history.back() — even when the tab has prior history", async () => {
    // Peer-review [HIGH]: window.history.length > 1 is not a reliable
    // "previous entry is ours" signal. A user who pastes /history/<id>
    // into a tab that already had other pages would, under the old code,
    // get sent OUT of the app by window.history.back(). Guard the
    // contract that Back always clears selection via setSelectedId(null)
    // (which routes through App's URL effect to push /history).
    window.history.pushState({}, "", "/");
    window.history.pushState({}, "", "/history/77");
    expect(window.history.length).toBeGreaterThan(1);
    const backSpy = vi.spyOn(window.history, "back");

    fetchRuns.mockResolvedValue({
      runs: [{ ...baseRun, id: 77, pdf_filename: "DEEP-LINK.pdf" }],
      total: 1,
      limit: 50,
      offset: 0,
    });
    vi.mocked(api.fetchRunDetail).mockResolvedValue({
      id: 77,
      created_at: "2026-04-10T00:00:00Z",
      pdf_filename: "DEEP-LINK.pdf",
      status: "completed",
      session_id: "sess-77",
      output_dir: "/tmp/out/sess-77",
      merged_workbook_path: "/tmp/out/sess-77/filled.xlsx",
      scout_enabled: false,
      started_at: "2026-04-10T00:00:00Z",
      ended_at: "2026-04-10T00:01:00Z",
      config: { statements: ["SOFP"], variants: {}, models: {}, use_scout: false },
      agents: [],
      cross_checks: [],
    });
    const onSelectRun = vi.fn();
    render(<HistoryPage selectedId={77} onSelectRun={onSelectRun} />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /back to history/i }),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /back to history/i }));
    expect(onSelectRun).toHaveBeenCalledWith(null);
    expect(backSpy).not.toHaveBeenCalled();
  });

  test("clicking Back calls onSelectRun(null)", async () => {
    fetchRuns.mockResolvedValue({
      runs: [{ ...baseRun, id: 77, pdf_filename: "DEEP-LINK.pdf" }],
      total: 1,
      limit: 50,
      offset: 0,
    });
    vi.mocked(api.fetchRunDetail).mockResolvedValue({
      id: 77,
      created_at: "2026-04-10T00:00:00Z",
      pdf_filename: "DEEP-LINK.pdf",
      status: "completed",
      session_id: "sess-77",
      output_dir: "/tmp/out/sess-77",
      merged_workbook_path: "/tmp/out/sess-77/filled.xlsx",
      scout_enabled: false,
      started_at: "2026-04-10T00:00:00Z",
      ended_at: "2026-04-10T00:01:00Z",
      config: { statements: ["SOFP"], variants: {}, models: {}, use_scout: false },
      agents: [],
      cross_checks: [],
    });
    const onSelectRun = vi.fn();
    render(<HistoryPage selectedId={77} onSelectRun={onSelectRun} />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /back to history/i }),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /back to history/i }));
    expect(onSelectRun).toHaveBeenCalledWith(null);
  });

  test("Load more failure keeps existing rows visible and shows inline error", async () => {
    fetchRuns.mockResolvedValueOnce({
      runs: Array.from({ length: 50 }, (_, i) => ({
        ...baseRun,
        id: i + 1,
        pdf_filename: `row-${i + 1}.pdf`,
      })),
      total: 80,
      limit: 50,
      offset: 0,
    });

    render(<HistoryPage />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /load more/i })).toBeTruthy();
    });
    // Pick a row that's guaranteed to be present in the first page so we
    // can assert it survives the pagination failure.
    expect(screen.getByText("row-1.pdf")).toBeTruthy();

    fetchRuns.mockRejectedValueOnce(new Error("network exploded"));
    fireEvent.click(screen.getByRole("button", { name: /load more/i }));

    // The inline pagination error appears.
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(/network exploded/i);
    });
    // Already-loaded rows are STILL in the DOM — the list wasn't blanked.
    expect(screen.getByText("row-1.pdf")).toBeTruthy();
    expect(screen.getByText("row-50.pdf")).toBeTruthy();
    // Load more button also survives so the user can retry.
    expect(screen.getByRole("button", { name: /load more/i })).toBeTruthy();
  });
});
