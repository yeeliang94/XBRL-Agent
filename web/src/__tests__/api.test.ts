import { describe, test, expect, vi } from "vitest";
import { uploadPdf, getSettings, updateSettings } from "../lib/api";

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
});
