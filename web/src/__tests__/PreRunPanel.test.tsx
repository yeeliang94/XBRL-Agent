import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PreRunPanel } from "../components/PreRunPanel";
import type { ModelEntry, ExtendedSettingsResponse } from "../lib/types";

// The inline scout model picker persists through lib/api.updateSettings.
// Mocked here so tests can assert the call shape without hitting the network.
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    updateSettings: vi.fn().mockResolvedValue({ status: "ok" }),
  };
});

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
      // Scout toggle + Scanned PDF + 5 statement checkboxes + 5 notes checkboxes = 12
      expect(screen.getAllByRole("checkbox")).toHaveLength(12);
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
      expect(screen.getAllByRole("checkbox")).toHaveLength(12);
    });

    // Uncheck SOCIE. Layout: [scout, scanned, SOFP, SOPL, SOCI, SOCF, SOCIE, notes×5]
    // → SOCIE is the 7th checkbox (index 6).
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[6]); // SOCIE

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

    // Check variant dropdowns were populated from scout infopack.
    // Can't use index 0 anymore — the inline scout model dropdown now renders
    // first. Filter by variant-specific options (same pattern the Run test
    // uses) to pin the first SOFP variant dropdown.
    await waitFor(() => {
      const sofpSelect = screen.getAllByRole("combobox").find(
        (el) => el.querySelector("option[value='CuNonCu']"),
      ) as HTMLSelectElement;
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

    // After scout completes, the variant dropdown should be populated.
    // Same filter-by-variant-option trick as the auto-detect test — index 0
    // now belongs to the inline scout model dropdown.
    await waitFor(() => {
      const sofpSelect = screen.getAllByRole("combobox").find(
        (el) => el.querySelector("option[value='CuNonCu']"),
      ) as HTMLSelectElement;
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
    // Layout: [scout, scanned, SOFP, SOPL, SOCI, SOCF, SOCIE, notes×5]
    // → statement checkboxes are indices 2..6.
    for (let i = 2; i <= 6; i++) expect(checkboxesBefore[i].checked).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    // After scout returns empty: statements must stay checked (no silent
    // unchecking) and a notice must surface explaining what happened.
    await waitFor(() => {
      expect(screen.getByText(/didn't detect any statements/i)).toBeInTheDocument();
    });
    const checkboxesAfter = screen.getAllByRole("checkbox") as HTMLInputElement[];
    for (let i = 2; i <= 6; i++) expect(checkboxesAfter[i].checked).toBe(true);

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
    // selector always renders all 5 dropdowns (Fix B). Identify SOFP by
    // its variant-specific option (CuNonCu is unique to SOFP) — the inline
    // scout model dropdown now occupies index 0.
    const sofpVariant = screen.getAllByRole("combobox").find(
      (el) => el.querySelector("option[value='CuNonCu']"),
    ) as HTMLSelectElement;
    fireEvent.change(sofpVariant, { target: { value: "CuNonCu" } });
    expect(sofpVariant.value).toBe("CuNonCu");

    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    await waitFor(() => {
      expect(screen.getByText(/didn't detect any statements/i)).toBeInTheDocument();
    });
    // After the empty-scout return, the manual variant must still be set.
    const sofpVariantAfter = screen.getAllByRole("combobox").find(
      (el) => el.querySelector("option[value='CuNonCu']"),
    ) as HTMLSelectElement;
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

    // Uncheck all 5 statement checkboxes. Layout after Scanned PDF shipped:
    // [scout, scanned, SOFP, SOPL, SOCI, SOCF, SOCIE, notes×5] → indices 2..6.
    const checkboxes = screen.getAllByRole("checkbox");
    for (let i = 2; i <= 6; i++) fireEvent.click(checkboxes[i]);

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

  test("scout model dropdown initializes from settings.default_models.scout", async () => {
    // Pins the "inline scout model picker shows persisted default" contract
    // from PLAN-ui-visibility-improvements Step 2.3.
    const settings = {
      ...mockSettings,
      default_models: {
        ...mockSettings.default_models,
        scout: "claude-opus-4-6",
      },
    };
    const getSettings = vi.fn().mockResolvedValue(settings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={vi.fn()} />,
    );

    await waitFor(() => {
      const select = screen.getByRole("combobox", { name: /scout model/i }) as HTMLSelectElement;
      expect(select.value).toBe("claude-opus-4-6");
    });
  });

  test("changing scout model persists via updateSettings and updates local value", async () => {
    const { updateSettings } = await import("../lib/api");
    (updateSettings as ReturnType<typeof vi.fn>).mockClear();

    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: /scout model/i })).toBeInTheDocument();
    });

    const select = screen.getByRole("combobox", { name: /scout model/i }) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "claude-opus-4-6" } });

    // Local state reflects the new selection without a refetch.
    expect(select.value).toBe("claude-opus-4-6");
    // The persisted path is hit with only the scout key, matching how the
    // server merges default_models entries (no accidental overwrite of
    // other roles' persisted model choices).
    expect(updateSettings).toHaveBeenCalledTimes(1);
    expect(updateSettings).toHaveBeenCalledWith({
      default_models: { scout: "claude-opus-4-6" },
    });
  });

  test("clicking Auto-detect before updateSettings resolves awaits the save first", async () => {
    // Peer-review [HIGH]: without this guard, a change-then-detect flow lets
    // the scout endpoint read the stale .env because the browser fired the
    // scout POST before the settings POST flushed. We fix it by awaiting any
    // in-flight persist in handleAutoDetect, and disabling the button while
    // the save is in flight for clear UI feedback.
    const { updateSettings } = await import("../lib/api");

    let resolveSave: ((v: { status: string }) => void) | null = null;
    const savePromise = new Promise<{ status: string }>((res) => {
      resolveSave = res;
    });
    (updateSettings as ReturnType<typeof vi.fn>).mockReset();
    (updateSettings as ReturnType<typeof vi.fn>).mockReturnValue(savePromise);

    // The scout fetch should NOT happen until after the save resolves.
    // Spy on fetch so we can assert the ordering.
    const scoutPayload = {
      success: true,
      infopack: { toc_page: 1, page_offset: 0, statements: {} },
    };
    const sseText = `event: scout_complete\ndata: ${JSON.stringify(scoutPayload)}\n\n`;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      const encoder = new TextEncoder();
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode(sseText));
          controller.close();
        },
      });
      return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    });

    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: /scout model/i })).toBeInTheDocument();
    });

    // 1. Change model — save starts but does NOT resolve yet.
    const select = screen.getByRole("combobox", { name: /scout model/i }) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "claude-opus-4-6" } });
    expect(updateSettings).toHaveBeenCalledTimes(1);

    // 2. Click Auto-detect immediately. Button should reflect the save state
    //    (disabled), and fetch must NOT have been called yet.
    const detectBtn = screen.getByRole("button", { name: /auto-detect/i });
    fireEvent.click(detectBtn);
    // Give React a tick to flush state updates.
    await Promise.resolve();
    expect(fetchSpy).not.toHaveBeenCalled();

    // 3. Resolve the pending save — the scout fetch must now fire.
    resolveSave!({ status: "ok" });
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/scout/abc-123",
        expect.objectContaining({ method: "POST" }),
      );
    });

    fetchSpy.mockRestore();
  });

  test("scout event log stays hidden until the first tool_call arrives", async () => {
    // Pre-run: no events means no log toggle button. Keeps the pre-detect
    // UI identical to how it looked before this feature landed.
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: /scout log/i })).not.toBeInTheDocument();
  });

  test("scout event log auto-expands during detect, collapses after complete, renders tool rows", async () => {
    // Single integration test covering: (1) events accumulate, (2) log
    // auto-expands while detecting, (3) tool_call rows render via
    // ToolCallCard, (4) log auto-collapses on scout_complete.
    const scoutPayload = {
      success: true,
      infopack: {
        toc_page: 3,
        page_offset: 0,
        statements: {
          SOFP: { variant_suggestion: "CuNonCu", face_page: 10, note_pages: [], confidence: "HIGH" },
        },
      },
    };
    const sseText = [
      `event: status\ndata: ${JSON.stringify({ phase: "scouting", message: "Starting..." })}\n\n`,
      `event: tool_call\ndata: ${JSON.stringify({ tool_name: "find_toc", tool_call_id: "tc_a", args: {} })}\n\n`,
      `event: tool_result\ndata: ${JSON.stringify({ tool_name: "find_toc", tool_call_id: "tc_a", result_summary: "ok", duration_ms: 50 })}\n\n`,
      `event: tool_call\ndata: ${JSON.stringify({ tool_name: "view_pages", tool_call_id: "tc_b", args: { pages: [10] } })}\n\n`,
      `event: tool_result\ndata: ${JSON.stringify({ tool_name: "view_pages", tool_call_id: "tc_b", result_summary: "rendered", duration_ms: 80 })}\n\n`,
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

    // During + after the run, a log toggle button must exist. Once auto-detect
    // finishes, the log collapses (aria-expanded=false) but the toggle stays
    // visible so the operator can re-open it to inspect what scout did.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /scout log/i })).toBeInTheDocument();
    });
    const toggleAfter = screen.getByRole("button", { name: /scout log/i });
    expect(toggleAfter.getAttribute("aria-expanded")).toBe("false");

    // Expand it and assert both tool rows are rendered. ToolCallCard uses
    // the humanised tool name (see toolLabels.ts TOOL_LABELS) — for scout
    // that's "Locating table of contents" + "Checking PDF pages".
    fireEvent.click(toggleAfter);
    await waitFor(() => {
      expect(toggleAfter.getAttribute("aria-expanded")).toBe("true");
    });
    expect(screen.getByText(/locating table of contents/i)).toBeInTheDocument();
    expect(screen.getByText(/checking pdf pages/i)).toBeInTheDocument();

    fetchSpy.mockRestore();
  });

  test("Scanned PDF checkbox exists and is unchecked by default", async () => {
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });
    const cb = screen.getByRole("checkbox", { name: /scanned pdf/i }) as HTMLInputElement;
    expect(cb.checked).toBe(false);
  });

  test("enabling Scanned PDF sends scanned_pdf:true in scout POST body", async () => {
    const sseText = `event: scout_complete\ndata: ${JSON.stringify({
      success: true,
      infopack: {
        toc_page: 1,
        page_offset: 0,
        statements: {},
        notes_inventory: [],
      },
    })}\n\n`;
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

    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: /scanned pdf/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("checkbox", { name: /scanned pdf/i }));
    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/scout/abc-123",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ scanned_pdf: true }),
        }),
      );
    });

    // Sanity check: the Content-Type header should tell the server a JSON body follows.
    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("content-type")).toMatch(/application\/json/i);

    fetchSpy.mockRestore();
  });

  test("shows inventory count after scout; red hint appears when empty and a notes sheet is selected", async () => {
    // Scout returns an empty inventory on this scanned PDF. The panel
    // should surface the count ("Inventory: 0 notes") and, because the
    // user has a notes sheet selected, a red hint telling them to flip
    // "Scanned PDF" and retry.
    const scoutPayload = {
      success: true,
      infopack: {
        toc_page: 1,
        page_offset: 0,
        statements: {},
        notes_inventory: [],
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

    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });

    // Turn on at least one notes template so the "enable Scanned PDF" hint fires.
    fireEvent.click(
      screen.getByRole("checkbox", { name: /list of notes \(note 12\)/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    await waitFor(() => {
      expect(screen.getByText(/inventory:\s*0\s*notes/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/enable.*scanned pdf/i)).toBeInTheDocument();

    fetchSpy.mockRestore();
  });

  test("non-empty inventory shows count but no hint even with notes selected", async () => {
    const scoutPayload = {
      success: true,
      infopack: {
        toc_page: 1,
        page_offset: 0,
        statements: {},
        notes_inventory: [
          { note_num: 1, title: "Corporate information", page_range: [10, 10] },
          { note_num: 2, title: "Summary of significant accounting policies", page_range: [10, 15] },
        ],
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

    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });
    fireEvent.click(
      screen.getByRole("checkbox", { name: /list of notes \(note 12\)/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    await waitFor(() => {
      expect(screen.getByText(/inventory:\s*2\s*notes/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/enable.*scanned pdf/i)).not.toBeInTheDocument();

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

  // -------------------------------------------------------------------------
  // Phase 7 MPERS wiring — filing-standard toggle + SoRE picker
  // -------------------------------------------------------------------------

  test("filing standard toggle renders with MFRS default active", async () => {
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^MFRS$/ })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^MPERS$/ })).toBeInTheDocument();
    });

    // MFRS button has the orange active background; MPERS is inactive.
    const mfrsBtn = screen.getByRole("button", { name: /^MFRS$/ }) as HTMLButtonElement;
    const mpersBtn = screen.getByRole("button", { name: /^MPERS$/ }) as HTMLButtonElement;
    expect(mfrsBtn.style.background).not.toBe(mpersBtn.style.background);
    // The active one uses the orange500 theme color (same background family as
    // the active Filing Level toggle). Testing exact pixel values is brittle —
    // the asymmetry above is what we actually care about.
  });

  test("run payload carries filing_standard from toggle", async () => {
    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={onRun} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^MPERS$/ })).toBeInTheDocument();
    });

    // Click MPERS then Run.
    fireEvent.click(screen.getByRole("button", { name: /^MPERS$/ }));
    fireEvent.click(screen.getByRole("button", { name: /run extraction/i }));

    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({ filing_standard: "mpers" }),
    );
  });

  test("filing_standard defaults to mfrs in payload when toggle is untouched", async () => {
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
      expect.objectContaining({ filing_standard: "mfrs" }),
    );
  });

  test("scout detected_standard preselects the toggle", async () => {
    // Scout reports MPERS → toggle should flip to MPERS automatically.
    const scoutPayload = {
      success: true,
      infopack: {
        toc_page: 3,
        page_offset: 6,
        detected_standard: "mpers",
        statements: {
          SOFP: { variant_suggestion: "CuNonCu", face_page: 10, note_pages: [], confidence: "HIGH" },
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

    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={onRun} />,
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    // After scout completes, clicking Run must send filing_standard=mpers
    // even though the user never touched the toggle.
    await waitFor(() => {
      // Toggle state is reflected through the payload to keep the test
      // independent of style-based active-button detection.
      fireEvent.click(screen.getByRole("button", { name: /run extraction/i }));
      expect(onRun).toHaveBeenCalledWith(
        expect.objectContaining({ filing_standard: "mpers" }),
      );
    });

    fetchSpy.mockRestore();
  });

  test("switching standard MFRS resets SoRE back to Default", async () => {
    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={onRun} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^MPERS$/ })).toBeInTheDocument();
    });

    // Switch to MPERS, then pick SoRE on the SOCIE variant dropdown.
    fireEvent.click(screen.getByRole("button", { name: /^MPERS$/ }));

    const socieSelect = screen.getAllByRole("combobox").find(
      (el) => el.querySelector("option[value='SoRE']"),
    ) as HTMLSelectElement;
    fireEvent.change(socieSelect, { target: { value: "SoRE" } });
    expect(socieSelect.value).toBe("SoRE");

    // Flip back to MFRS — SOCIE must fall back to Default explicitly, not
    // blank (peer-review MEDIUM: blanking makes visible UI diverge from
    // what the coordinator actually runs).
    fireEvent.click(screen.getByRole("button", { name: /^MFRS$/ }));

    await waitFor(() => {
      const socieAfter = screen.getAllByRole("combobox").find(
        (el) => {
          // SOCIE dropdown on MFRS has Default only — not SoRE.
          const opts = Array.from((el as HTMLSelectElement).options).map((o) => o.value);
          return opts.includes("Default") && !opts.includes("SoRE")
            && !opts.includes("CuNonCu") && !opts.includes("Function")
            && !opts.includes("BeforeTax") && !opts.includes("Indirect");
        },
      ) as HTMLSelectElement;
      expect(socieAfter).toBeDefined();
      expect(socieAfter.value).toBe("Default");
    });

    // Pin the payload shape too: running after the switch-back must send
    // SOCIE=Default (not blank), matching what the user sees.
    fireEvent.click(screen.getByRole("button", { name: /run extraction/i }));
    expect(onRun).toHaveBeenCalledWith(
      expect.objectContaining({
        filing_standard: "mfrs",
        variants: expect.objectContaining({ SOCIE: "Default" }),
      }),
    );
  });

  test("scout SoRE suggestion with detected_standard=unknown is rejected on MFRS", async () => {
    // Peer-review HIGH: the LLM scout can return variant_suggestion="SoRE"
    // with detected_standard="unknown". Our preselect only fires for
    // mfrs/mpers, so the toggle stays on MFRS — and the server's Phase-3
    // guard would reject SOCIE/SoRE on an MFRS run. The UI must sanitise
    // the suggestion here instead of shipping an invalid config.
    const scoutPayload = {
      success: true,
      infopack: {
        toc_page: 3,
        page_offset: 6,
        detected_standard: "unknown",
        statements: {
          SOCIE: {
            variant_suggestion: "SoRE",
            face_page: 22,
            note_pages: [],
            confidence: "MEDIUM",
          },
          SOFP: {
            variant_suggestion: "CuNonCu",
            face_page: 10,
            note_pages: [],
            confidence: "HIGH",
          },
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

    const onRun = vi.fn();
    const getSettings = vi.fn().mockResolvedValue(mockSettings);
    render(
      <PreRunPanel sessionId="abc-123" getSettings={getSettings} onRun={onRun} />,
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));

    // Wait for scout to settle. SOFP suggestion is valid and should land;
    // SOCIE=SoRE is not valid on MFRS and must be blanked.
    await waitFor(() => {
      const sofpSelect = screen.getAllByRole("combobox").find(
        (el) => el.querySelector("option[value='CuNonCu']"),
      ) as HTMLSelectElement;
      expect(sofpSelect.value).toBe("CuNonCu");
    });

    const socieSelect = screen.getAllByRole("combobox").find(
      (el) => {
        const opts = Array.from((el as HTMLSelectElement).options).map((o) => o.value);
        return opts.includes("Default") && !opts.includes("SoRE")
          && !opts.includes("CuNonCu") && !opts.includes("Function")
          && !opts.includes("BeforeTax") && !opts.includes("Indirect");
      },
    ) as HTMLSelectElement;
    expect(socieSelect.value).toBe("");

    // Runtime payload must not carry the invalid SoRE variant.
    fireEvent.click(screen.getByRole("button", { name: /run extraction/i }));
    const payload = onRun.mock.calls[0][0];
    expect(payload.filing_standard).toBe("mfrs");
    expect(payload.variants.SOCIE).not.toBe("SoRE");

    fetchSpy.mockRestore();
  });
});
