import { describe, test, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { HomeHero } from "../components/HomeHero";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// HomeHero fires fetchHomeStats (3 calls) + fetchRecentRuns (1 call) when
// active. Queue plausible responses for all four.
function primeFetch(opts?: { recent?: unknown[]; fail?: boolean }) {
  mockFetch.mockReset();
  if (opts?.fail) {
    // Server-error response (not a raw rejected promise) so the failure
    // flows through apiFetch's handled throw path — mirrors a real 500
    // and keeps vitest from flagging unhandled rejections.
    mockFetch.mockResolvedValue({ ok: false, status: 500, json: async () => ({ detail: "boom" }) });
    return;
  }
  const recent = opts?.recent ?? [
    { id: 5, created_at: "2026-05-20T09:00:00Z", pdf_filename: "RECENT.pdf", status: "completed", session_id: "s5", statements_run: [], models_used: [], duration_seconds: 1, scout_enabled: false, has_merged_workbook: true },
  ];
  // fetchHomeStats → 3 limit=1 total reads, then fetchRecentRuns → the list.
  mockFetch
    .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 9, limit: 1, offset: 0 }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 2, limit: 1, offset: 0 }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 4, limit: 1, offset: 0 }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 0, limit: 1, offset: 0 }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: recent, total: recent.length, limit: 20, offset: 0 }) });
}

const noop = () => {};

describe("HomeHero", () => {
  beforeEach(() => mockFetch.mockReset());

  test("renders the upload child and, when active, the stats + history sections", async () => {
    primeFetch();
    render(
      <HomeHero active onResumeDraft={noop} onOpenRun={noop} onViewAllRuns={noop}>
        <div>UPLOAD CARD</div>
      </HomeHero>,
    );
    // Upload child (middle section) is always present.
    expect(screen.getByText("UPLOAD CARD")).toBeTruthy();
    // Stats band on top + history pane at the bottom once the fetches resolve.
    expect(screen.getByText("Total runs")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("RECENT.pdf")).toBeTruthy());
  });

  test("does not render the stats/history sections or fetch when inactive", () => {
    render(
      <HomeHero active={false} onResumeDraft={noop} onOpenRun={noop} onViewAllRuns={noop}>
        <div>UPLOAD CARD</div>
      </HomeHero>,
    );
    expect(screen.getByText("UPLOAD CARD")).toBeTruthy();
    expect(screen.queryByText("Total runs")).toBeNull();
    expect(screen.queryByText(/recent runs/i)).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  test("clearing drafts confirms, calls the bulk-delete endpoint, and refetches (E3)", async () => {
    // Initial load: total=9, drafts=2, completed=4, then the recent list.
    primeFetch();
    // After the confirm: the DELETE, then a fresh 5-call reload (4 stats + the
    // recent list) with 0 drafts.
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ deleted: 2 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 7, limit: 1, offset: 0 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 0, limit: 1, offset: 0 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 4, limit: 1, offset: 0 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 0, limit: 1, offset: 0 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 0, limit: 20, offset: 0 }) });

    render(
      <HomeHero active onResumeDraft={noop} onOpenRun={noop} onViewAllRuns={noop}>
        <div>UPLOAD CARD</div>
      </HomeHero>,
    );
    // Wait for the drafts count (2) to render, so the Clear action appears.
    await waitFor(() => expect(screen.getByTestId("clear-drafts")).toBeTruthy());
    fireEvent.click(screen.getByTestId("clear-drafts"));
    // Confirm dialog opens; confirm the sweep.
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /clear drafts/i }));

    // The bulk-delete endpoint was hit with DELETE.
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && c[0].includes("/api/runs/drafts"),
      );
      expect(call).toBeTruthy();
      expect(call![1]?.method).toBe("DELETE");
    });
    // Drafts count refreshed to 0 → the Clear action is gone.
    await waitFor(() => expect(screen.queryByTestId("clear-drafts")).toBeNull());
  });

  test("degrades quietly when the fetch fails (upload child still shown)", async () => {
    primeFetch({ fail: true });
    render(
      <HomeHero active onResumeDraft={noop} onOpenRun={noop} onViewAllRuns={noop}>
        <div>UPLOAD CARD</div>
      </HomeHero>,
    );
    expect(screen.getByText("UPLOAD CARD")).toBeTruthy();
    // Recent list shows its error placeholder; tiles fall back to dashes.
    await waitFor(() => expect(screen.getByText(/couldn't load recent runs/i)).toBeTruthy());
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
  });
});
