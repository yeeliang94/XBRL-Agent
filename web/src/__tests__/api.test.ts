import { describe, test, expect, vi, beforeEach } from "vitest";
import {
  uploadPdf,
  getSettings,
  updateSettings,
  fetchRuns,
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
});
