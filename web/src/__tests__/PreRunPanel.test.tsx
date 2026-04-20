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
      // Scout toggle + 5 statement checkboxes + 5 notes checkboxes = 11
      expect(screen.getAllByRole("checkbox")).toHaveLength(11);
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
      expect(screen.getAllByRole("checkbox")).toHaveLength(11);
    });

    // Uncheck SOCIE (6th checkbox, index 5 — scout toggle + 5 statement checkboxes)
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

  test("scout tool calls update progress and populate variants", async () => {
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

    // After scout completes, the variant dropdown should be populated
    await waitFor(() => {
      const sofpSelect = screen.getAllByRole("combobox")[0];
      expect(sofpSelect).toHaveValue("CuNonCu");
    });

    fetchSpy.mockRestore();
  });

  test("scout returning zero detected statements keeps rows enabled + shows notice", async () => {
    // Covers the "scout succeeded but found nothing" path. Previously this
    // silently unchecked every statement, which collapsed the Variants UI
    // and left the user with no affordance to continue.
    const scoutPayload = {
      success: true,
      infopack: {
        toc_page: 3,
        page_offset: 0,
        statements: {}, // empty dict — scout couldn't identify any statements
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
      <PreRunPanel sessionId="abc-123" getSettings={getSettingsFn} onRun={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });

    // Statements all start enabled (makeAllEnabled).
    const checkboxesBefore = screen.getAllByRole("checkbox") as HTMLInputElement[];
    // Indices 1..5 are the 5 statement checkboxes.
    for (let i = 1; i <= 5; i++) expect(checkboxesBefore[i].checked).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    // After scout returns empty: statements must stay checked (no silent
    // unchecking) and a notice must surface explaining what happened.
    await waitFor(() => {
      expect(screen.getByText(/didn't detect any statements/i)).toBeInTheDocument();
    });
    const checkboxesAfter = screen.getAllByRole("checkbox") as HTMLInputElement[];
    for (let i = 1; i <= 5; i++) expect(checkboxesAfter[i].checked).toBe(true);

    fetchSpy.mockRestore();
  });

  test("empty scout preserves manually-picked variants (peer-review finding #2)", async () => {
    // Regression for the case where the empty-scout guard preserved
    // checkboxes but still overwrote every variant selection — the notice
    // claimed "keeping your current selection" while we silently reset the
    // dropdowns to "".
    const scoutPayload = {
      success: true,
      infopack: {
        toc_page: 3,
        page_offset: 0,
        statements: {}, // zero detections
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
      <PreRunPanel sessionId="abc-123" getSettings={getSettingsFn} onRun={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });

    // Manually pick a SOFP variant BEFORE running scout. The variant
    // selector always renders all 5 dropdowns (Fix B); the first combobox
    // corresponds to SOFP.
    const sofpVariant = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    fireEvent.change(sofpVariant, { target: { value: "CuNonCu" } });
    expect(sofpVariant.value).toBe("CuNonCu");

    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    await waitFor(() => {
      expect(screen.getByText(/didn't detect any statements/i)).toBeInTheDocument();
    });
    // After the empty-scout return, the manual variant must still be set.
    const sofpVariantAfter = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    expect(sofpVariantAfter.value).toBe("CuNonCu");

    fetchSpy.mockRestore();
  });

  test("filing level defaults to company and can be toggled to group", async () => {
    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={onRun} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /company/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /group/i })).toBeInTheDocument();
    });

    // Click Group
    fireEvent.click(screen.getByRole("button", { name: /group/i }));

    // Trigger a run
    fireEvent.click(screen.getByRole("button", { name: /run extraction/i }));

    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({ filing_level: "group" }),
    );
  });

  test("filing level defaults to company in payload", async () => {
    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={onRun} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /run extraction/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /run extraction/i }));

    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({ filing_level: "company" }),
    );
  });

  test("renders 5 notes checkboxes, all off by default", async () => {
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={vi.fn()} />,
    );

    await waitFor(() => {
      // Each notes checkbox uses its full label as aria-label.
      expect(
        screen.getByRole("checkbox", { name: /corporate information \(note 10\)/i }),
      ).toBeInTheDocument();
    });

    // All 5 notes labels present + all unchecked.
    const noteLabels = [
      /corporate information \(note 10\)/i,
      /accounting policies \(note 11\)/i,
      /list of notes \(note 12\)/i,
      /issued capital \(note 13\)/i,
      /related party transactions \(note 14\)/i,
    ];
    for (const label of noteLabels) {
      const cb = screen.getByRole("checkbox", { name: label }) as HTMLInputElement;
      expect(cb.checked).toBe(false);
    }
  });

  test("ticking a notes checkbox populates notes_to_run on submit", async () => {
    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={onRun} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /run extraction/i })).toBeInTheDocument();
    });

    // Enable Notes 10 and Notes 13
    fireEvent.click(
      screen.getByRole("checkbox", { name: /corporate information \(note 10\)/i }),
    );
    fireEvent.click(
      screen.getByRole("checkbox", { name: /issued capital \(note 13\)/i }),
    );

    fireEvent.click(screen.getByRole("button", { name: /run extraction/i }));

    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({
        notes_to_run: ["CORP_INFO", "ISSUED_CAPITAL"],
      }),
    );
  });

  test("notes model picker lands on notes_models for enabled templates only", async () => {
    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={onRun} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /run extraction/i })).toBeInTheDocument();
    });

    // Enable Notes 11 only, then pick a non-default model for it.
    fireEvent.click(
      screen.getByRole("checkbox", { name: /accounting policies \(note 11\)/i }),
    );

    // Notes model dropdowns use explicit aria-labels to stay unambiguous
    // against the five statement model dropdowns and five variant pickers.
    const notes11ModelSelect = screen.getByRole("combobox", {
      name: /model for accounting policies \(note 11\)/i,
    }) as HTMLSelectElement;
    fireEvent.change(notes11ModelSelect, { target: { value: "claude-opus-4-6" } });

    fireEvent.click(screen.getByRole("button", { name: /run extraction/i }));

    // notes_models must include the enabled template with the override, and
    // NOT include templates the user left unchecked (mirrors `models`).
    const payload = onRun.mock.calls[0][0];
    expect(payload.notes_to_run).toEqual(["ACC_POLICIES"]);
    expect(payload.notes_models).toEqual({ ACC_POLICIES: "claude-opus-4-6" });
  });

  test("no notes selected → notes_to_run is empty (face-only run, no regression)", async () => {
    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={onRun} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /run extraction/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /run extraction/i }));

    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({ notes_to_run: [] }),
    );
  });

  test("notes-only run is allowed when every statement is unchecked", async () => {
    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={onRun} />,
    );

    await waitFor(() => {
      expect(screen.getAllByRole("checkbox").length).toBeGreaterThan(0);
    });

    // Uncheck all 5 statement checkboxes (indices 1-5; index 0 is scout toggle).
    const checkboxes = screen.getAllByRole("checkbox");
    for (let i = 1; i <= 5; i++) fireEvent.click(checkboxes[i]);

    // With no face statements and no notes, Run must be disabled.
    const runBtn = screen.getByRole("button", { name: /run extraction/i }) as HTMLButtonElement;
    expect(runBtn.disabled).toBe(true);

    // Enable a single notes checkbox → Run must re-enable.
    fireEvent.click(
      screen.getByRole("checkbox", { name: /list of notes \(note 12\)/i }),
    );
    expect(runBtn.disabled).toBe(false);

    fireEvent.click(runBtn);
    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({
        statements: [],
        notes_to_run: ["LIST_OF_NOTES"],
      }),
    );
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
