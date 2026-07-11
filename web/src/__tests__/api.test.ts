import { describe, test, expect, vi, beforeEach } from "vitest";
import {
  uploadPdf,
  getSettings,
  updateSettings,
  fetchRuns,
  fetchRecentRuns,
  fetchHomeStats,
  fetchRunDetail,
  deleteRun,
  downloadFilledUrl,
} from "../lib/api";

// Mock fetch globally
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

describe("API client", () => {
  test("uploadPdf sends FormData and returns session", async () => {
    const file = new File(["%PDF-1.4"], "test.pdf", {
      type: "application/pdf",
    });
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ session_id: "abc123", filename: "test.pdf" }),
    });

    const result = await uploadPdf(file);
    expect(result.session_id).toBe("abc123");
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/upload",
      expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
    );
  });

  test("getSettings returns config", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        model: "vertex_ai.gemini-3-flash-preview",
        proxy_url: "https://proxy.example.com",
        api_key_set: false,
        api_key_preview: "",
      }),
    });

    const settings = await getSettings();
    expect(settings.api_key_set).toBe(false);
  });

  test("updateSettings POSTs new config", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: "ok" }),
    });
    await updateSettings({
      api_key: "new-key",
      model: "vertex_ai.gemini-3-flash-preview",
    });
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/settings",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );
  });

  // --- Phase 5: History API client ---
  describe("fetchRuns", () => {
    beforeEach(() => mockFetch.mockClear());

    test("builds URL with no params for empty filters", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ runs: [], total: 0, limit: 50, offset: 0 }),
      });
      const result = await fetchRuns({});
      expect(mockFetch).toHaveBeenCalledWith("/api/runs", undefined);
      expect(result.runs).toEqual([]);
    });

    test("builds query string from all filter fields", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ runs: [], total: 0, limit: 20, offset: 0 }),
      });
      await fetchRuns({
        q: "FINCO",
        status: "completed",
        dateFrom: "2026-01-01T00:00:00Z",
        dateTo: "2026-12-31T23:59:59Z",
        limit: 20,
        offset: 0,
      });
      const calledUrl = mockFetch.mock.calls[0][0] as string;
      // URL builder is order-insensitive — assert each key appears
      expect(calledUrl).toContain("/api/runs?");
      expect(calledUrl).toContain("q=FINCO");
      expect(calledUrl).toContain("status=completed");
      // Server expects from/to aliases (middleware remaps to date_from/date_to)
      expect(calledUrl).toMatch(/from=2026-01-01/);
      expect(calledUrl).toMatch(/to=2026-12-31/);
      expect(calledUrl).toContain("limit=20");
      expect(calledUrl).toContain("offset=0");
    });

    test("omits empty-string filters from the URL", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ runs: [], total: 0, limit: 50, offset: 0 }),
      });
      await fetchRuns({ q: "", status: "" });
      const calledUrl = mockFetch.mock.calls[0][0] as string;
      expect(calledUrl).not.toContain("q=");
      expect(calledUrl).not.toContain("status=");
    });
  });

  // --- Homepage split-hero helpers (PLAN-homepage-redesign.md) ---
  describe("fetchRecentRuns", () => {
    beforeEach(() => mockFetch.mockClear());

    test("fetches a wide window and surfaces results ahead of drafts, capped to limit", async () => {
      // Newest-first page: newest two are drafts, older ones are results.
      const rows = [
        { id: 6, status: "draft" },
        { id: 5, status: "draft" },
        { id: 4, status: "completed" },
        { id: 3, status: "completed_with_errors" },
        { id: 2, status: "draft" },
        { id: 1, status: "completed" },
      ];
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ runs: rows, total: 6, limit: 20, offset: 0 }),
      });
      const result = await fetchRecentRuns(3);
      const calledUrl = mockFetch.mock.calls[0][0] as string;
      // Wider window than the display limit so results further back surface.
      expect(calledUrl).toContain("limit=20");
      expect(calledUrl).not.toContain("status=");
      // Results first (newest-first within group), then drafts, capped to 3.
      expect(result.map((r) => r.id)).toEqual([4, 3, 1]);
    });

    test("defaults to a limit of 5 (fetching a wider window)", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ runs: [], total: 0, limit: 20, offset: 0 }),
      });
      await fetchRecentRuns();
      const calledUrl = mockFetch.mock.calls[0][0] as string;
      expect(calledUrl).toContain("limit=20");
    });
  });

  describe("fetchHomeStats", () => {
    beforeEach(() => mockFetch.mockClear());

    test("derives the counts from each filter's server total", async () => {
      // Six parallel calls, each reading `total` off a limit=1 page.
      // Order: draft, completed-this-month, completed_with_errors-this-month,
      // completed_with_errors-all-time, correction_exhausted, running.
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 3, limit: 1, offset: 0 }) })
        .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 7, limit: 1, offset: 0 }) })
        .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 2, limit: 1, offset: 0 }) })
        .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 8, limit: 1, offset: 0 }) })
        .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 1, limit: 1, offset: 0 }) })
        .mockResolvedValueOnce({ ok: true, json: async () => ({ runs: [], total: 4, limit: 1, offset: 0 }) });

      const stats = await fetchHomeStats();
      expect(stats).toEqual({ drafts: 3, completedThisMonth: 9, needsReview: 9, active: 4 });

      const urls = mockFetch.mock.calls.map((c) => c[0] as string);
      // Drafts call carries the status filter.
      expect(urls.some((u) => u.includes("status=draft"))).toBe(true);
      // Both completed variants this month carry a first-of-month date floor.
      const completedUrl = urls.find((u) => u.includes("status=completed&"));
      expect(completedUrl ?? urls.find((u) => /status=completed(&|$)/.test(u))).toMatch(/from=\d{4}-\d{2}-01/);
      const reviewUrls = urls.filter((u) => u.includes("status=completed_with_errors"));
      expect(reviewUrls).toHaveLength(2);
      expect(reviewUrls.some((u) => !u.includes("from="))).toBe(true);
    });
  });

  test("fetchRunDetail calls /api/runs/{id}", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: 42,
        pdf_filename: "x.pdf",
        status: "completed",
        agents: [],
        cross_checks: [],
      }),
    });
    const detail = await fetchRunDetail(42);
    expect(mockFetch).toHaveBeenCalledWith("/api/runs/42", undefined);
    expect(detail.id).toBe(42);
  });

  // Phase 8: RunAgentJson should carry the per-agent SSE event list so
  // History can replay the timeline via buildToolTimeline(). Legacy rows
  // that predate Phase 6.5 persistence may omit the field entirely — the
  // client must backfill an empty array so UI consumers can treat the
  // field as always-present.
  test("fetchRunDetail returns agents with events array", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: 7,
        created_at: "2026-04-11T09:00:00Z",
        pdf_filename: "x.pdf",
        status: "completed",
        session_id: "s7",
        output_dir: "/tmp/s7",
        merged_workbook_path: null,
        scout_enabled: false,
        started_at: null,
        ended_at: null,
        config: null,
        agents: [
          {
            id: 1,
            statement_type: "SOFP",
            variant: "CuNonCu",
            model: "gemini-3-flash-preview",
            status: "succeeded",
            started_at: null,
            ended_at: null,
            workbook_path: null,
            total_tokens: 100,
            total_cost: 0,
            events: [
              {
                event: "tool_call",
                data: {
                  tool_name: "read_template",
                  tool_call_id: "call-1",
                  args: { path: "01-SOFP-CuNonCu.xlsx" },
                },
                timestamp: 1712830000,
              },
            ],
          },
        ],
        cross_checks: [],
      }),
    });
    const detail = await fetchRunDetail(7);
    expect(detail.agents[0].events).toBeDefined();
    expect(detail.agents[0].events.length).toBe(1);
    expect(detail.agents[0].events[0].event).toBe("tool_call");
  });

  test("fetchRunDetail backfills events=[] when the server sent null or a non-array", async () => {
    // The client contract is "coerce anything that isn't an array to []".
    // Cover two non-undefined shapes the backend could realistically emit:
    // an explicit null (e.g. a nullable column) and an object (e.g. a
    // future misconfigured serialiser). Both must land as [].
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: 9,
        created_at: "2026-04-11T09:00:00Z",
        pdf_filename: "legacy.pdf",
        status: "completed",
        session_id: "s9",
        output_dir: "/tmp/s9",
        merged_workbook_path: null,
        scout_enabled: false,
        started_at: null,
        ended_at: null,
        config: null,
        agents: [
          {
            id: 1,
            statement_type: "SOFP",
            variant: null,
            model: null,
            status: "succeeded",
            started_at: null,
            ended_at: null,
            workbook_path: null,
            total_tokens: null,
            total_cost: null,
            events: null,
          },
          {
            id: 2,
            statement_type: "SOPL",
            variant: null,
            model: null,
            status: "succeeded",
            started_at: null,
            ended_at: null,
            workbook_path: null,
            total_tokens: null,
            total_cost: null,
            // Non-array garbage — the client must not let it reach consumers.
            events: {},
          },
        ],
        cross_checks: [],
      }),
    });
    const detail = await fetchRunDetail(9);
    expect(detail.agents[0].events).toEqual([]);
    expect(detail.agents[1].events).toEqual([]);
  });

  test("fetchRunDetail backfills events=[] when the server omitted the field", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: 8,
        created_at: "2026-04-11T09:00:00Z",
        pdf_filename: "legacy.pdf",
        status: "completed",
        session_id: "s8",
        output_dir: "/tmp/s8",
        merged_workbook_path: null,
        scout_enabled: false,
        started_at: null,
        ended_at: null,
        config: null,
        // Legacy row: agent object with no `events` key at all.
        agents: [
          {
            id: 1,
            statement_type: "SOFP",
            variant: null,
            model: null,
            status: "succeeded",
            started_at: null,
            ended_at: null,
            workbook_path: null,
            total_tokens: null,
            total_cost: null,
          },
        ],
        cross_checks: [],
      }),
    });
    const detail = await fetchRunDetail(8);
    // Defensive backfill so downstream renderers can always spread over
    // `events` without null-checking.
    expect(detail.agents[0].events).toEqual([]);
  });

  test("deleteRun issues DELETE /api/runs/{id}", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ deleted: 42 }),
    });
    await deleteRun(42);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/runs/42",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  test("downloadFilledUrl builds the per-run download URL", () => {
    expect(downloadFilledUrl(42)).toBe("/api/runs/42/download/filled");
  });

  test("downloadFilledUrl rejects non-integer runIds so bad state can't be injected into the path", () => {
    expect(() => downloadFilledUrl(NaN)).toThrow(/positive integer/);
    expect(() => downloadFilledUrl(1.5)).toThrow(/positive integer/);
    expect(() => downloadFilledUrl(-1)).toThrow(/positive integer/);
    expect(() => downloadFilledUrl(0)).toThrow(/positive integer/);
  });
});
