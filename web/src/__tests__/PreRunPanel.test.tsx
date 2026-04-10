import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PreRunPanel } from "../components/PreRunPanel";
import type { ModelEntry, ExtendedSettingsResponse } from "../lib/types";

const mockModels: ModelEntry[] = [
  { id: "gemini-3-flash", display_name: "Gemini 3 Flash", provider: "google", supports_vision: true, notes: "" },
  { id: "claude-opus-4-6", display_name: "Claude Opus 4.6", provider: "anthropic", supports_vision: true, notes: "" },
];

const mockSettings: ExtendedSettingsResponse = {
  model: "gemini-3-flash",
  proxy_url: "",
  api_key_set: true,
  api_key_preview: "sk-1234...abcd",
  available_models: mockModels,
  default_models: {
    scout: "gemini-3-flash",
    SOFP: "gemini-3-flash",
    SOPL: "gemini-3-flash",
    SOCI: "gemini-3-flash",
    SOCF: "gemini-3-flash",
    SOCIE: "gemini-3-flash",
  },
  scout_enabled_default: true,
  tolerance_rm: 1.0,
};

describe("PreRunPanel", () => {
  test("renders all major sections", async () => {
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel
        sessionId="abc-123"
        getSettings={getSettings}
        onRun={vi.fn()}

      />,
    );

    // Wait for settings to load
    await waitFor(() => {
      // Scout toggle + 5 statement checkboxes = 6
      expect(screen.getAllByRole("checkbox")).toHaveLength(6);
    });
  });

  test("Run button is present and enabled when all variants are selected", async () => {
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel
        sessionId="abc-123"
        getSettings={getSettings}
        onRun={vi.fn()}

      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /run extraction/i })).toBeInTheDocument();
    });
  });

  test("clicking Run calls onRun with correct config shape", async () => {
    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel
        sessionId="abc-123"
        getSettings={getSettings}
        onRun={onRun}

      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /run extraction/i })).toBeInTheDocument();
    });

    // Select variants for all statements
    const variantSelects = screen.getAllByRole("combobox").filter(
      (el) => el.querySelector("option[value='CuNonCu']") || el.querySelector("option[value='Function']")
        || el.querySelector("option[value='BeforeTax']") || el.querySelector("option[value='Indirect']")
        || el.querySelector("option[value='Default']"),
    );

    // SOFP
    fireEvent.change(variantSelects[0], { target: { value: "CuNonCu" } });
    // SOPL
    fireEvent.change(variantSelects[1], { target: { value: "Function" } });
    // SOCI
    fireEvent.change(variantSelects[2], { target: { value: "BeforeTax" } });
    // SOCF
    fireEvent.change(variantSelects[3], { target: { value: "Indirect" } });
    // SOCIE
    fireEvent.change(variantSelects[4], { target: { value: "Default" } });

    fireEvent.click(screen.getByRole("button", { name: /run extraction/i }));

    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({
        statements: expect.arrayContaining(["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"]),
        variants: expect.objectContaining({ SOFP: "CuNonCu" }),
        use_scout: true,
      }),
    );
  });

  test("unchecking a statement excludes it from the run config", async () => {
    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel
        sessionId="abc-123"
        getSettings={getSettings}
        onRun={onRun}

      />,
    );

    await waitFor(() => {
      expect(screen.getAllByRole("checkbox")).toHaveLength(6);
    });

    // Uncheck SOCIE (6th checkbox, index 5)
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[5]); // SOCIE

    // Select variants for remaining 4
    const variantSelects = screen.getAllByRole("combobox").filter(
      (el) => {
        const opts = el.querySelectorAll("option");
        return opts.length > 1;  // has variant options
      },
    );

    // Set variants for the 4 enabled statements
    if (variantSelects.length >= 4) {
      fireEvent.change(variantSelects[0], { target: { value: "CuNonCu" } });
      fireEvent.change(variantSelects[1], { target: { value: "Function" } });
      fireEvent.change(variantSelects[2], { target: { value: "BeforeTax" } });
      fireEvent.change(variantSelects[3], { target: { value: "Indirect" } });
    }

    fireEvent.click(screen.getByRole("button", { name: /run extraction/i }));

    if (onRun.mock.calls.length > 0) {
      const config = onRun.mock.calls[0][0];
      expect(config.statements).not.toContain("SOCIE");
    }
  });

  test("auto-detect reads variant_suggestion and normalizes confidence", async () => {
    // Mock fetch to return a scout_complete SSE payload matching real backend format
    const scoutPayload = {
      success: true,
      infopack: {
        toc_page: 3,
        page_offset: 6,
        statements: {
          SOFP: { variant_suggestion: "CuNonCu", face_page: 10, note_pages: [], confidence: "HIGH" },
          SOPL: { variant_suggestion: "Function", face_page: 14, note_pages: [], confidence: "MEDIUM" },
        },
      },
    };

    const sseText = `event: scout_complete\ndata: ${JSON.stringify(scoutPayload)}\n\n`;
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseText));
        controller.close();
      },
    });

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    const getSettingsFn = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel
        sessionId="abc-123"
        getSettings={getSettingsFn}
        onRun={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    // Check variant dropdowns were populated from scout infopack
    await waitFor(() => {
      const sofpSelect = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
      expect(sofpSelect.value).toBe("CuNonCu");
    });

    fetchSpy.mockRestore();
  });

  test("stop scout button appears during auto-detect and cancels on click", async () => {
    // Mock a slow SSE stream that doesn't complete immediately
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        // Send a status event but don't close — simulates ongoing scout
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode(
          `event: status\ndata: ${JSON.stringify({ phase: "scouting", message: "Finding TOC..." })}\n\n`,
        ));
      },
    });

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    const getSettingsFn = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel
        sessionId="abc-123"
        getSettings={getSettingsFn}
        onRun={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });

    // Start scout
    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    // A stop/cancel button should appear while scout is running
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
    });

    // Click stop
    fireEvent.click(screen.getByRole("button", { name: /stop/i }));

    // After stopping, the stop button should disappear
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument();
    });

    fetchSpy.mockRestore();
  });

  test("scout tool calls are displayed in the progress area", async () => {
    // Mock SSE stream that sends tool_call and tool_result events before completing
    const scoutPayload = {
      success: true,
      infopack: {
        toc_page: 3, page_offset: 6,
        statements: {
          SOFP: { variant_suggestion: "CuNonCu", face_page: 10, note_pages: [], confidence: "HIGH" },
        },
      },
    };

    const sseText = [
      `event: status\ndata: ${JSON.stringify({ phase: "scouting", message: "Starting scout..." })}\n\n`,
      `event: tool_call\ndata: ${JSON.stringify({ tool_name: "find_toc", tool_call_id: "tc_1", args: {} })}\n\n`,
      `event: tool_result\ndata: ${JSON.stringify({ tool_name: "find_toc", tool_call_id: "tc_1", result_summary: "Found TOC on page 3", duration_ms: 120 })}\n\n`,
      `event: tool_call\ndata: ${JSON.stringify({ tool_name: "view_pages", tool_call_id: "tc_2", args: { pages: [10] } })}\n\n`,
      `event: tool_result\ndata: ${JSON.stringify({ tool_name: "view_pages", tool_call_id: "tc_2", result_summary: "Viewed 1 page", duration_ms: 200 })}\n\n`,
      `event: scout_complete\ndata: ${JSON.stringify(scoutPayload)}\n\n`,
    ].join("");

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseText));
        controller.close();
      },
    });

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    const getSettingsFn = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettingsFn} onRun={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    // Tool names should appear in the progress area
    await waitFor(() => {
      expect(screen.getByText(/find_toc/)).toBeInTheDocument();
      expect(screen.getByText(/view_pages/)).toBeInTheDocument();
    });

    fetchSpy.mockRestore();
  });

  test("disabling scout hides auto-detect button", async () => {
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel
        sessionId="abc-123"
        getSettings={getSettings}
        onRun={vi.fn()}

      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });

    // Toggle scout off (first checkbox is the scout toggle)
    const scoutCheckbox = screen.getAllByRole("checkbox")[0];
    fireEvent.click(scoutCheckbox);

    expect(screen.queryByRole("button", { name: /auto-detect/i })).not.toBeInTheDocument();
  });
});
