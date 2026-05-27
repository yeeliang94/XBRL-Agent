import { describe, test, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
    .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: recent, total: recent.length, limit: 5, offset: 0 }) });
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
